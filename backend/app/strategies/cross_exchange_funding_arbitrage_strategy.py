"""Cross-exchange funding arbitrage paper strategy."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

from app.core.execution.base_strategy import BarData, BaseStrategy
from app.domain.arbitrage import arbitrage_domain_service

logger = logging.getLogger(__name__)


class CrossExchangeFundingArbitrageStrategy(BaseStrategy):
    """Open paper OKX/Binance USD-M market-neutral pairs from public edge scans."""

    async def on_init(self) -> None:
        cfg = self.config or {}
        self.position_notional_usdt = max(0.0, float(cfg.get("position_notional_usdt", 30.0)))
        self.max_active_pairs = max(1, int(cfg.get("max_active_pairs", cfg.get("max_active_symbols", 2))))
        self.min_net_edge_bps = max(0.0, float(cfg.get("min_net_edge_bps", 6.0)))
        self.min_depth_usdt = max(0.0, float(cfg.get("min_depth_usdt", 50_000.0)))
        self.paper_leverage = max(1.0, float(cfg.get("paper_leverage", cfg.get("leverage", 3.0))))
        self.poll_interval_seconds = max(1.0, float(cfg.get("poll_interval_seconds", cfg.get("loop_interval_sec", 300.0))))
        funding_period_minutes = max(1.0, float(cfg.get("funding_period_minutes", 480.0)))
        max_hold_funding_events = max(1, int(cfg.get("max_hold_funding_events", 9)))
        timeframe_minutes = self._timeframe_minutes(str(cfg.get("timeframe") or "1h"))
        self.max_hold_bars = max(1, int(cfg.get("max_hold_bars", max_hold_funding_events * funding_period_minutes / timeframe_minutes)))
        self.close_when_edge_disappears = bool(cfg.get("close_when_edge_disappears", False))
        self._last_poll_ts_ms: Optional[int] = None
        self._scan_lock = asyncio.Lock()
        self._sync_state()
        logger.info(
            "[CrossExchangeFundingArbitrage] initialized strategy_id=%s notional=%.2f max_active=%d min_edge=%.2fbps",
            self.state.strategy_id,
            self.position_notional_usdt,
            self.max_active_pairs,
            self.min_net_edge_bps,
        )

    async def on_bar(self, bar: BarData) -> None:
        broker = self.broker
        if getattr(broker, "warmup_mode", False):
            return
        advance_bar = getattr(broker, "advance_bar", None)
        if callable(advance_bar):
            advance_bar()
            self._sync_state()

        if self._last_poll_ts_ms is not None:
            elapsed_ms = int(bar.timestamp) - self._last_poll_ts_ms
            if elapsed_ms < self.poll_interval_seconds * 1000:
                return
        if self._scan_lock.locked():
            return

        async with self._scan_lock:
            if self._last_poll_ts_ms is not None:
                elapsed_ms = int(bar.timestamp) - self._last_poll_ts_ms
                if elapsed_ms < self.poll_interval_seconds * 1000:
                    return
            self._last_poll_ts_ms = int(bar.timestamp)
            await self._poll_once()

    async def _poll_once(self) -> None:
        try:
            summary = await arbitrage_domain_service.summary()
        except Exception as exc:
            await self._emit("scan_failed", "跨所套利公开机会扫描失败", level="error", error=str(exc))
            return

        opportunities = [
            dict(item)
            for item in (summary.get("opportunities") or [])
            if isinstance(item, dict)
        ]
        opportunities.sort(key=lambda item: float(item.get("net_edge_bps") or 0.0), reverse=True)
        await self._update_active_positions(opportunities)
        await self._open_new_pairs(opportunities)
        self._sync_state()

    async def _update_active_positions(self, opportunities: List[Dict[str, Any]]) -> None:
        broker = self.broker
        positions = getattr(broker, "positions", {}) or {}
        by_symbol = {str(item.get("symbol") or ""): item for item in opportunities}
        for symbol, position in list(positions.items()):
            opportunity = by_symbol.get(symbol)
            if opportunity:
                update = getattr(broker, "update_from_opportunity", None)
                if callable(update):
                    update(opportunity)
            bars_held = int((position or {}).get("bars_held") or 0)
            should_close = bars_held >= self.max_hold_bars
            if self.close_when_edge_disappears and not opportunity:
                should_close = True
            if should_close:
                close_pair = getattr(broker, "close_pair", None)
                if callable(close_pair):
                    result = close_pair(symbol, reason="max_hold_or_edge_disappeared")
                    await self._emit("close_pair", "跨所套利 paper 组合已平仓", symbol=symbol, result=result)

    async def _open_new_pairs(self, opportunities: List[Dict[str, Any]]) -> None:
        broker = self.broker
        positions = getattr(broker, "positions", {}) or {}
        open_pair = getattr(broker, "open_pair_from_opportunity", None)
        if not callable(open_pair):
            await self._emit("broker_unavailable", "跨所 paper broker 不可用", level="error")
            return

        for opportunity in opportunities:
            if len(positions) >= self.max_active_pairs:
                break
            symbol = str(opportunity.get("symbol") or "")
            if not symbol or symbol in positions:
                continue
            net_edge = float(opportunity.get("net_edge_bps") or 0.0)
            depth = float(opportunity.get("depth_usdt") or 0.0)
            if net_edge < self.min_net_edge_bps or depth < self.min_depth_usdt:
                continue
            result = open_pair(
                opportunity,
                notional_usdt=self.position_notional_usdt,
                leverage=self.paper_leverage,
            )
            if result.get("status") == "filled":
                await self._emit(
                    "open_pair",
                    "跨所套利 paper 组合已开仓",
                    symbol=symbol,
                    net_edge_bps=net_edge,
                    depth_usdt=depth,
                    result=result,
                )
                positions = getattr(broker, "positions", {}) or {}
            else:
                await self._emit(
                    "open_skipped",
                    "跨所套利 paper 组合开仓跳过",
                    level="warning",
                    symbol=symbol,
                    result=result,
                )

    def _sync_state(self) -> None:
        export_state = getattr(self.broker, "export_state", None)
        if callable(export_state):
            self.state.positions["_cross_exchange_arbitrage"] = export_state()

    async def _emit(self, event: str, message: str, level: str = "info", **payload: Any) -> None:
        await self.broadcast_strategy_channel(
            {
                "type": "cross_exchange_arbitrage",
                "event": event,
                "level": level,
                "message": message,
                **payload,
            }
        )

    @staticmethod
    def _timeframe_minutes(value: str) -> float:
        token = str(value or "1h").strip().lower()
        if token.endswith("m"):
            return max(1.0, float(token[:-1] or 1))
        if token.endswith("h"):
            return max(1.0, float(token[:-1] or 1) * 60.0)
        if token.endswith("d"):
            return max(1.0, float(token[:-1] or 1) * 1440.0)
        return 60.0
