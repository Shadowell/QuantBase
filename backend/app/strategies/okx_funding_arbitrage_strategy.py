"""OKX full-market funding-rate arbitrage strategy.

Paper-only hybrid strategy: buy spot and short the matching USDT perpetual.
The strategy scans OKX funding opportunities, opens delta-neutral paper
hedges for high positive funding, and closes when funding decays.
"""

from __future__ import annotations

import asyncio
import logging
import math
from typing import Any, Dict, Iterable, List, Optional

from app.core.execution.base_strategy import BarData, BaseStrategy, OrderResult
from app.services.contract_paper_account import normalize_contract_symbol
from app.services.funding_service import funding_service
from app.services.market_service import market_service

logger = logging.getLogger(__name__)


DECISION_LABELS = {
    "scan_opportunities": "扫描资金费率机会",
    "open_hedge": "建立资金费率对冲",
    "open_skipped": "跳过开仓",
    "open_failed": "对冲开仓失败",
    "rollback_leg": "单腿风险回滚",
    "close_hedge": "平掉资金费率对冲",
    "close_failed": "对冲平仓失败",
    "funding_collected": "资金费率结算",
    "funding_rate_update": "资金费率更新",
    "funding_error": "资金费率查询失败",
    "balance_insufficient": "可用余额不足",
}


class OkxFundingArbitrageStrategy(BaseStrategy):
    """Scan OKX USDT perpetuals and run spot-long/perp-short paper hedges."""

    async def on_init(self) -> None:
        cfg = self.config or {}
        self.exchange = "okx"
        self.min_annualized_rate = self._parse_rate(cfg.get("min_annualized_rate", 0.40), 0.40)
        self.close_annualized_rate = self._parse_rate(cfg.get("close_annualized_rate", 0.10), 0.10)
        self.position_notional_usdt = max(0.0, float(cfg.get("position_notional_usdt", 1_000.0)))
        self.max_active_symbols = max(1, int(cfg.get("max_active_symbols", 3)))
        self.poll_interval_seconds = max(1.0, float(cfg.get("poll_interval_seconds", 300.0)))
        self.leverage = max(1.0, float(cfg.get("leverage", 1.0)))
        self.balance_buffer_pct = max(0.0, float(cfg.get("balance_buffer_pct", 0.01)))
        self.max_opportunities_per_scan = max(1, int(cfg.get("max_opportunities_per_scan", 20)))
        self.funding_scan_limit = max(5, self.max_opportunities_per_scan, int(cfg.get("funding_scan_limit", 100)))
        self.funding_events_per_day = max(1.0, float(cfg.get("funding_events_per_day", 3.0)))
        self.funding_period_minutes = max(1.0, float(cfg.get("funding_period_minutes", 480.0)))
        self.min_expected_funding_events = max(1, int(cfg.get("min_expected_funding_events", 24)))
        self.min_hold_funding_events = max(0, int(cfg.get("min_hold_funding_events", 1)))
        self.min_net_edge_bps = max(0.0, float(cfg.get("min_net_edge_bps", 5.0)))
        self.exit_net_edge_bps = float(cfg.get("exit_net_edge_bps", 0.0))
        self.funding_decay_exit_ratio = max(0.0, float(cfg.get("funding_decay_exit_ratio", 0.30)))
        self.taker_fee_bps = max(0.0, float(cfg.get("taker_fee_bps", cfg.get("fee_bps", 5.0))))
        if "slippage_bps" in cfg:
            self.slippage_bps = max(0.0, float(cfg.get("slippage_bps") or 0.0))
        else:
            self.slippage_bps = max(0.0, float(cfg.get("slippage_rate", 0.0001)) * 10_000)
        one_leg_cost_bps = self.taker_fee_bps + self.slippage_bps
        self.entry_cost_bps = max(0.0, float(cfg.get("entry_cost_bps", one_leg_cost_bps * 2)))
        self.exit_cost_bps = max(0.0, float(cfg.get("exit_cost_bps", one_leg_cost_bps * 2)))
        self.round_trip_cost_bps = max(
            0.0,
            float(cfg.get("round_trip_cost_bps", self.entry_cost_bps + self.exit_cost_bps)),
        )

        self.scan_scope = str(cfg.get("scan_scope") or cfg.get("symbol_scope") or "full_market").strip().lower()
        self.limit_to_configured_symbols = bool(cfg.get("limit_to_configured_symbols", False)) or self.scan_scope in {
            "configured",
            "configured_symbols",
            "allowlist",
            "allowed_symbols",
        }
        raw_allowed = cfg.get("allowed_symbols") or cfg.get("trade_symbols") or cfg.get("contract_trade_symbols") or []
        self.allowed_contract_symbols = set(self._normalize_contract_list(raw_allowed))
        self.active_positions: Dict[str, Dict[str, Any]] = {}
        self._last_prices: Dict[str, float] = {}
        self._contract_metadata_errors: Dict[str, str] = {}
        self._last_poll_ts_ms: Optional[int] = None
        self._scan_lock = asyncio.Lock()
        self._restored_once = False

        logger.info(
            "[OkxFundingArbitrage] initialized strategy_id=%s min_annualized=%.2f%% close_annualized=%.2f%% "
            "notional=%.2f max_active=%d poll_interval=%.0fs round_trip_cost=%.2fbps min_edge=%.2fbps",
            self.state.strategy_id,
            self.min_annualized_rate * 100,
            self.close_annualized_rate * 100,
            self.position_notional_usdt,
            self.max_active_symbols,
            self.poll_interval_seconds,
            self.round_trip_cost_bps,
            self.min_net_edge_bps,
        )

    async def on_bar(self, bar: BarData) -> None:
        price = float(bar.close)
        if math.isfinite(price) and price > 0:
            self._remember_price(bar.symbol, price)
            update_mark = getattr(self.broker, "update_mark_price", None)
            if callable(update_mark):
                try:
                    update_mark(bar.symbol, price)
                except Exception:
                    logger.debug("[OkxFundingArbitrage] mark price update ignored", exc_info=True)

        if getattr(self.broker, "warmup_mode", False):
            return

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
            await self._restore_active_positions_once()
            await self._poll_and_rebalance(int(bar.timestamp))

    async def _poll_and_rebalance(self, timestamp_ms: int) -> None:
        await self._monitor_active_positions(timestamp_ms)
        if len(self.active_positions) >= self.max_active_symbols:
            return
        await self._scan_and_open(timestamp_ms)

    async def _monitor_active_positions(self, timestamp_ms: int) -> None:
        for contract_symbol in list(self.active_positions.keys()):
            try:
                funding_data = await self._fetch_current_rate_data(contract_symbol)
                rate = self._extract_rate(funding_data)
            except Exception as exc:
                logger.error(
                    "[OkxFundingArbitrage] funding fetch failed for active symbol=%s error=%s",
                    contract_symbol,
                    exc,
                )
                await self._emit("funding_error", "持仓标的资金费率查询失败", level="error", symbol=contract_symbol, error=str(exc))
                continue

            next_funding_ts = self._funding_timestamp_ms(funding_data)
            if next_funding_ts and next_funding_ts > timestamp_ms:
                self.active_positions[contract_symbol]["next_funding_timestamp_ms"] = next_funding_ts
            await self._maybe_apply_funding(contract_symbol, timestamp_ms, rate)
            annualized = self._annualized_rate(rate)
            exit_edge = self._estimate_exit_edge_bps(rate, self.active_positions.get(contract_symbol) or {})
            logger.info(
                "[OkxFundingArbitrage] active funding update symbol=%s rate=%.8f annualized=%.2f%% remaining_net=%.2fbps",
                contract_symbol,
                rate,
                annualized * 100,
                exit_edge["remaining_net_bps"],
            )
            await self._emit(
                "funding_rate_update",
                "资金费率已更新，继续监控持仓",
                symbol=contract_symbol,
                funding_rate=rate,
                annualized_rate=annualized,
                close_annualized_rate=self.close_annualized_rate,
                funding_collections=exit_edge["funding_collections"],
                min_hold_funding_events=self.min_hold_funding_events,
                estimated_remaining_funding_bps=exit_edge["remaining_gross_bps"],
                estimated_exit_cost_bps=self.exit_cost_bps,
                estimated_remaining_net_bps=exit_edge["remaining_net_bps"],
            )
            if rate < 0:
                reason = "funding_negative"
                await self._close_hedge(contract_symbol, reason=reason)
                continue

            if exit_edge["funding_collections"] < self.min_hold_funding_events:
                continue

            meta = self.active_positions.get(contract_symbol) or {}
            entry_rate = float(meta.get("entry_funding_rate") or 0.0)
            if annualized <= self.close_annualized_rate:
                await self._close_hedge(contract_symbol, reason="funding_below_close_threshold")
                continue
            if entry_rate > 0 and rate <= entry_rate * self.funding_decay_exit_ratio:
                await self._close_hedge(contract_symbol, reason="funding_decay")
                continue
            if exit_edge["remaining_net_bps"] <= self.exit_net_edge_bps:
                await self._close_hedge(contract_symbol, reason="net_edge_exhausted")

    async def _scan_and_open(self, timestamp_ms: int) -> None:
        try:
            opportunities = await funding_service.get_opportunities(
                self.exchange,
                0.0,
                limit=self.funding_scan_limit,
            )
        except Exception as exc:
            logger.error("[OkxFundingArbitrage] opportunity scan failed: %s", exc)
            await self._emit("funding_error", "全市场资金费率扫描失败", level="error", error=str(exc))
            return

        scan_source = "opportunities"
        if not opportunities:
            opportunities = await self._fetch_known_symbol_funding_rates()
            scan_source = "configured_symbols"

        top_funding_rates = self._top_funding_rates(opportunities, limit=5)
        top_summary = self._format_top_funding_rates(top_funding_rates)
        qualifying_count = sum(
            1
            for item in opportunities
            if self._is_entry_candidate(self._extract_rate(item))
        )
        logger.info(
            "[OkxFundingArbitrage] scanned OKX funding rates strategy_id=%s count=%d qualifying=%d top5=%s",
            self.state.strategy_id,
            len(opportunities),
            qualifying_count,
            top_summary,
        )
        await self._emit(
            "scan_opportunities",
            f"已扫描 OKX 全市场资金费率机会，Top5：{top_summary}",
            opportunity_count=len(opportunities),
            qualifying_count=qualifying_count,
            min_annualized_rate=self.min_annualized_rate,
            scan_source=scan_source,
            top_funding_rates=top_funding_rates,
        )

        for item in opportunities:
            if len(self.active_positions) >= self.max_active_symbols:
                break
            contract_symbol = normalize_contract_symbol(str(item.get("symbol") or ""))
            if not contract_symbol or contract_symbol in self.active_positions:
                continue
            if self.limit_to_configured_symbols and self.allowed_contract_symbols and contract_symbol not in self.allowed_contract_symbols:
                continue

            rate = self._extract_rate(item)
            if rate <= 0:
                continue
            annualized = self._annualized_rate(rate)
            if annualized < self.min_annualized_rate:
                continue
            entry_edge = self._estimate_entry_edge_bps(rate)
            if entry_edge["net_edge_bps"] < self.min_net_edge_bps:
                await self._emit(
                    "open_skipped",
                    "资金费率未覆盖手续费、滑点和安全缓冲，跳过开仓",
                    symbol=contract_symbol,
                    funding_rate=rate,
                    annualized_rate=annualized,
                    expected_funding_events=self.min_expected_funding_events,
                    estimated_gross_funding_bps=entry_edge["gross_funding_bps"],
                    estimated_round_trip_cost_bps=self.round_trip_cost_bps,
                    estimated_net_edge_bps=entry_edge["net_edge_bps"],
                    min_net_edge_bps=self.min_net_edge_bps,
                )
                continue

            price = await self._resolve_price(contract_symbol, item)
            if price is None:
                logger.warning("[OkxFundingArbitrage] skip %s because no market price is available", contract_symbol)
                await self._emit("open_skipped", "缺少可用价格，跳过资金费率套利开仓", level="warning", symbol=contract_symbol)
                continue

            min_notional = self._min_contract_notional(contract_symbol, price)
            if min_notional is None:
                metadata_error = self._contract_metadata_errors.get(contract_symbol, "")
                await self._emit(
                    "open_skipped",
                    "缺少 OKX 合约元数据，跳过资金费率套利开仓",
                    level="warning",
                    symbol=contract_symbol,
                    price=price,
                    target_notional_usdt=self.position_notional_usdt,
                    error=metadata_error,
                )
                continue
            if min_notional > self.position_notional_usdt + 1e-9:
                await self._emit(
                    "open_skipped",
                    "目标名义低于 OKX 最小合约张数要求，跳过资金费率套利开仓",
                    level="warning",
                    symbol=contract_symbol,
                    price=price,
                    target_notional_usdt=self.position_notional_usdt,
                    min_contract_notional_usdt=min_notional,
                )
                continue

            if not await self._has_enough_balance():
                logger.warning(
                    "[OkxFundingArbitrage] insufficient USDT balance for %s notional=%.2f",
                    contract_symbol,
                    self.position_notional_usdt,
                )
                await self._emit(
                    "balance_insufficient",
                    "USDT 可用余额不足，跳过资金费率套利开仓",
                    level="warning",
                    symbol=contract_symbol,
                    required_usdt=self._required_balance(),
                    available_usdt=await self._available_usdt(),
                )
                break

            await self._open_hedge(
                contract_symbol,
                price=price,
                funding_rate=rate,
                annualized=annualized,
                timestamp_ms=timestamp_ms,
                next_funding_timestamp_ms=self._funding_timestamp_ms(item),
            )

    async def _open_hedge(
        self,
        contract_symbol: str,
        *,
        price: float,
        funding_rate: float,
        annualized: float,
        timestamp_ms: int,
        next_funding_timestamp_ms: Optional[int] = None,
    ) -> None:
        spot_symbol = self._spot_symbol(contract_symbol)
        spot_amount = self.position_notional_usdt / price
        if next_funding_timestamp_ms is not None and next_funding_timestamp_ms <= timestamp_ms:
            next_funding_timestamp_ms = None

        logger.info(
            "[OkxFundingArbitrage] opening hedge symbol=%s spot=%s notional=%.2f price=%.8f annualized=%.2f%%",
            contract_symbol,
            spot_symbol,
            self.position_notional_usdt,
            price,
            annualized * 100,
        )

        buy_coro = self.broker.buy(spot_symbol, spot_amount, price=price, order_type="market")
        short_coro = self.broker.open_contract(
            contract_symbol,
            "short",
            self.position_notional_usdt,
            leverage=self.leverage,
            price=price,
        )
        buy_result_raw, short_result_raw = await asyncio.gather(buy_coro, short_coro, return_exceptions=True)
        buy_result = self._coerce_order_result(buy_result_raw)
        short_result = self._coerce_order_result(short_result_raw)

        buy_ok = self._filled(buy_result)
        short_ok = self._filled(short_result)
        if buy_ok and short_ok:
            self.active_positions[contract_symbol] = {
                "contract_symbol": contract_symbol,
                "spot_symbol": spot_symbol,
                "entry_timestamp_ms": timestamp_ms,
                "entry_price": price,
                "entry_funding_rate": funding_rate,
                "entry_annualized_rate": annualized,
                "notional_usdt": self.position_notional_usdt,
                "spot_amount": float(buy_result.get("amount") or spot_amount),
                "funding_collections": 0,
                "last_funding_timestamp_ms": timestamp_ms,
                "next_funding_timestamp_ms": next_funding_timestamp_ms,
                "expected_funding_events": self.min_expected_funding_events,
                "estimated_round_trip_cost_bps": self.round_trip_cost_bps,
            }
            entry_edge = self._estimate_entry_edge_bps(funding_rate)
            await self._emit(
                "open_hedge",
                "资金费率成本后净优势达到阈值，已建立现货买入 + 合约做空对冲",
                symbol=contract_symbol,
                spot_symbol=spot_symbol,
                funding_rate=funding_rate,
                annualized_rate=annualized,
                notional_usdt=self.position_notional_usdt,
                price=price,
                expected_funding_events=self.min_expected_funding_events,
                estimated_gross_funding_bps=entry_edge["gross_funding_bps"],
                estimated_round_trip_cost_bps=self.round_trip_cost_bps,
                estimated_net_edge_bps=entry_edge["net_edge_bps"],
            )
            return

        logger.error(
            "[OkxFundingArbitrage] hedge open failed symbol=%s spot_ok=%s short_ok=%s buy_result=%s short_result=%s",
            contract_symbol,
            buy_ok,
            short_ok,
            dict(buy_result),
            dict(short_result),
        )
        await self._rollback_open_legs(contract_symbol, spot_symbol, price, buy_ok, short_ok, buy_result)
        await self._emit(
            "open_failed",
            "资金费率套利双腿开仓失败，已尝试回滚已成交单腿",
            level="error",
            symbol=contract_symbol,
            spot_result=dict(buy_result),
            contract_result=dict(short_result),
        )

    async def _rollback_open_legs(
        self,
        contract_symbol: str,
        spot_symbol: str,
        price: float,
        buy_ok: bool,
        short_ok: bool,
        buy_result: OrderResult,
    ) -> None:
        tasks = []
        if buy_ok:
            amount = float(buy_result.get("amount") or self._spot_qty(spot_symbol) or 0.0)
            if amount > 1e-12:
                tasks.append(self.broker.sell(spot_symbol, amount, price=price, order_type="market"))
        if short_ok:
            tasks.append(self.broker.close_contract(contract_symbol, "short", ratio=1.0, price=price))
        if not tasks:
            return
        results = await asyncio.gather(*tasks, return_exceptions=True)
        logger.warning(
            "[OkxFundingArbitrage] rolled back single-leg exposure symbol=%s results=%s",
            contract_symbol,
            results,
        )
        await self._emit("rollback_leg", "已尝试回滚资金费率套利单腿敞口", level="warning", symbol=contract_symbol)

    async def _close_hedge(self, contract_symbol: str, *, reason: str) -> None:
        meta = self.active_positions.get(contract_symbol) or {}
        spot_symbol = str(meta.get("spot_symbol") or self._spot_symbol(contract_symbol))
        price = self._price_for(contract_symbol) or float(meta.get("entry_price") or 0.0)
        if price <= 0:
            logger.warning("[OkxFundingArbitrage] cannot close %s because no close price is available", contract_symbol)
            await self._emit("close_failed", "缺少可用价格，暂无法平掉资金费率套利对冲", level="warning", symbol=contract_symbol)
            return

        spot_amount = self._spot_qty(spot_symbol)
        logger.info(
            "[OkxFundingArbitrage] closing hedge symbol=%s spot=%s spot_amount=%.10f reason=%s",
            contract_symbol,
            spot_symbol,
            spot_amount,
            reason,
        )

        tasks = []
        if spot_amount > 1e-12:
            tasks.append(self.broker.sell(spot_symbol, spot_amount, price=price, order_type="market"))
        else:
            tasks.append(self._skipped_order("no_spot_position", spot_symbol))
        tasks.append(self.broker.close_contract(contract_symbol, "short", ratio=1.0, price=price))
        spot_result_raw, contract_result_raw = await asyncio.gather(*tasks, return_exceptions=True)
        spot_result = self._coerce_order_result(spot_result_raw)
        contract_result = self._coerce_order_result(contract_result_raw)

        if self._closed_or_absent(spot_result) and self._closed_or_absent(contract_result):
            self.active_positions.pop(contract_symbol, None)
            await self._emit(
                "close_hedge",
                "资金费率净收益优势消失或转负，已平掉现货 + 合约对冲",
                symbol=contract_symbol,
                reason=reason,
                spot_result=dict(spot_result),
                contract_result=dict(contract_result),
            )
            return

        logger.error(
            "[OkxFundingArbitrage] hedge close incomplete symbol=%s spot_result=%s contract_result=%s",
            contract_symbol,
            dict(spot_result),
            dict(contract_result),
        )
        await self._emit(
            "close_failed",
            "资金费率套利平仓未完全完成，下次轮询继续重试",
            level="error",
            symbol=contract_symbol,
            reason=reason,
            spot_result=dict(spot_result),
            contract_result=dict(contract_result),
        )

    async def _restore_active_positions_once(self) -> None:
        if self._restored_once:
            return
        self._restored_once = True
        for contract_symbol in self._known_contract_symbols():
            if contract_symbol in self.active_positions:
                continue
            try:
                contract_pos = await self.broker.get_contract_position(contract_symbol, "short")
            except Exception:
                continue
            spot_symbol = self._spot_symbol(contract_symbol)
            if contract_pos and self._spot_qty(spot_symbol) > 1e-12:
                self.active_positions[contract_symbol] = {
                    "contract_symbol": contract_symbol,
                    "spot_symbol": spot_symbol,
                    "entry_timestamp_ms": None,
                    "entry_price": float(contract_pos.get("entry_price") or 0.0),
                    "entry_funding_rate": None,
                    "entry_annualized_rate": None,
                    "notional_usdt": float(contract_pos.get("notional_usdt") or self.position_notional_usdt),
                    "spot_amount": self._spot_qty(spot_symbol),
                    "funding_collections": 0,
                    "last_funding_timestamp_ms": None,
                    "expected_funding_events": self.min_expected_funding_events,
                    "estimated_round_trip_cost_bps": self.round_trip_cost_bps,
                    "restored": True,
                }

    async def _maybe_apply_funding(self, contract_symbol: str, timestamp_ms: int, funding_rate: float) -> None:
        meta = self.active_positions.get(contract_symbol)
        if not meta:
            return
        period_ms = int(self.funding_period_minutes * 60 * 1000)
        if period_ms <= 0:
            return
        next_funding_ts = meta.get("next_funding_timestamp_ms")
        if next_funding_ts:
            first_due_ts = int(next_funding_ts)
            if timestamp_ms < first_due_ts:
                return
            due_events = 1 + max(0, int((timestamp_ms - first_due_ts) // period_ms))
        else:
            last_ts = meta.get("last_funding_timestamp_ms") or meta.get("entry_timestamp_ms")
            if not last_ts:
                meta["last_funding_timestamp_ms"] = timestamp_ms
                return
            elapsed_ms = timestamp_ms - int(last_ts)
            if elapsed_ms < period_ms:
                return
            first_due_ts = int(last_ts) + period_ms
            due_events = max(1, int(elapsed_ms // period_ms))

        apply_funding = getattr(self.broker, "apply_funding", None)
        applied_events: List[Any] = []
        completed_events = 0
        for _ in range(due_events):
            if callable(apply_funding):
                try:
                    result = apply_funding(contract_symbol, funding_rate)
                    if asyncio.iscoroutine(result):
                        result = await result
                    if isinstance(result, list):
                        applied_events.extend(result)
                except Exception as exc:
                    logger.warning(
                        "[OkxFundingArbitrage] apply funding failed symbol=%s error=%s",
                        contract_symbol,
                        exc,
                    )
                    break
            meta["funding_collections"] = int(meta.get("funding_collections") or 0) + 1
            completed_events += 1
        if completed_events <= 0:
            return
        last_applied_ts = first_due_ts + (completed_events - 1) * period_ms
        meta["last_funding_timestamp_ms"] = last_applied_ts
        meta["next_funding_timestamp_ms"] = last_applied_ts + period_ms
        await self._emit(
            "funding_collected",
            "已按 OKX 资金费率周期模拟结算持仓资金费",
            symbol=contract_symbol,
            funding_rate=funding_rate,
            funding_collections=int(meta.get("funding_collections") or 0),
            applied_events=applied_events,
        )

    def _annualized_rate(self, funding_rate: float) -> float:
        return funding_rate * self.funding_events_per_day * 365

    def _is_entry_candidate(self, funding_rate: float) -> bool:
        if funding_rate <= 0:
            return False
        if self._annualized_rate(funding_rate) < self.min_annualized_rate:
            return False
        return self._estimate_entry_edge_bps(funding_rate)["net_edge_bps"] >= self.min_net_edge_bps

    def _estimate_entry_edge_bps(self, funding_rate: float) -> Dict[str, float]:
        gross_bps = funding_rate * 10_000 * self.min_expected_funding_events
        net_bps = gross_bps - self.round_trip_cost_bps
        return {
            "gross_funding_bps": gross_bps,
            "round_trip_cost_bps": self.round_trip_cost_bps,
            "net_edge_bps": net_bps,
        }

    def _estimate_exit_edge_bps(self, funding_rate: float, meta: Dict[str, Any]) -> Dict[str, float]:
        funding_collections = max(0, int(meta.get("funding_collections") or 0))
        expected_events = max(1, int(meta.get("expected_funding_events") or self.min_expected_funding_events))
        remaining_events = max(1, expected_events - funding_collections)
        remaining_gross_bps = funding_rate * 10_000 * remaining_events
        remaining_net_bps = remaining_gross_bps - self.exit_cost_bps
        return {
            "funding_collections": float(funding_collections),
            "remaining_events": float(remaining_events),
            "remaining_gross_bps": remaining_gross_bps,
            "remaining_net_bps": remaining_net_bps,
        }

    async def _fetch_current_rate_data(self, contract_symbol: str) -> Dict[str, Any]:
        data = await funding_service.get_funding_rate(self.exchange, contract_symbol)
        return data or {}

    async def _fetch_known_symbol_funding_rates(self) -> List[Dict[str, Any]]:
        symbols = self._known_contract_symbols()
        if not symbols:
            return []
        results = await asyncio.gather(
            *(funding_service.get_funding_rate(self.exchange, symbol) for symbol in symbols),
            return_exceptions=True,
        )
        rows: List[Dict[str, Any]] = []
        for symbol, result in zip(symbols, results):
            if isinstance(result, Exception):
                logger.warning("[OkxFundingArbitrage] fallback funding fetch failed symbol=%s error=%s", symbol, result)
                continue
            if not isinstance(result, dict):
                continue
            row = dict(result)
            row["symbol"] = normalize_contract_symbol(str(row.get("symbol") or symbol))
            row["rate"] = self._extract_rate(row)
            rows.append(row)
        return rows

    async def _has_enough_balance(self) -> bool:
        return await self._available_usdt() + 1e-9 >= self._required_balance()

    async def _available_usdt(self) -> float:
        fn = getattr(self.broker, "get_available_balance", None)
        if callable(fn):
            try:
                return float(await fn("USDT"))
            except Exception:
                logger.debug("[OkxFundingArbitrage] get_available_balance failed", exc_info=True)
        for attr in ("available_balance", "balance", "cash"):
            value = getattr(self.broker, attr, None)
            if value is not None:
                try:
                    return float(value)
                except (TypeError, ValueError):
                    pass
        account = getattr(self.broker, "account", None)
        if account is not None:
            try:
                return float(getattr(account, "free_balance"))
            except (TypeError, ValueError, AttributeError):
                pass
        return 0.0

    def _required_balance(self) -> float:
        contract_margin = self.position_notional_usdt / max(self.leverage, 1.0)
        return (self.position_notional_usdt + contract_margin) * (1 + self.balance_buffer_pct)

    def _spot_qty(self, spot_symbol: str) -> float:
        list_fn = getattr(self.broker, "list_spot_positions", None)
        if callable(list_fn):
            try:
                for pos in list_fn():
                    if self._spot_symbol(str(pos.get("symbol") or "")) == spot_symbol:
                        return max(0.0, float(pos.get("size") or pos.get("amount") or 0.0))
            except Exception:
                logger.debug("[OkxFundingArbitrage] list_spot_positions failed", exc_info=True)
        for attr in ("spot_positions", "positions"):
            positions = getattr(self.broker, attr, None)
            if not isinstance(positions, dict):
                continue
            pos = positions.get(spot_symbol)
            if isinstance(pos, dict):
                try:
                    return max(0.0, float(pos.get("size") or pos.get("amount") or 0.0))
                except (TypeError, ValueError):
                    return 0.0
        return 0.0

    def _known_contract_symbols(self) -> List[str]:
        values: List[str] = []
        values.extend(self._normalize_contract_list(self.state.symbols))
        values.extend(self.allowed_contract_symbols)
        account = getattr(self.broker, "account", None)
        instruments = getattr(account, "instruments", None)
        if isinstance(instruments, dict):
            values.extend(self._normalize_contract_list(instruments.keys()))
        return sorted(set(values))

    def _remember_price(self, symbol: str, price: float) -> None:
        contract_symbol = normalize_contract_symbol(symbol)
        spot_symbol = self._spot_symbol(symbol)
        if contract_symbol:
            self._last_prices[contract_symbol] = price
        if spot_symbol:
            self._last_prices[spot_symbol] = price

    def _price_for(self, contract_symbol: str) -> Optional[float]:
        spot_symbol = self._spot_symbol(contract_symbol)
        for key in (contract_symbol, spot_symbol):
            try:
                price = float(self._last_prices.get(key) or 0.0)
            except (TypeError, ValueError):
                price = 0.0
            if math.isfinite(price) and price > 0:
                return price
        return None

    async def _resolve_price(self, contract_symbol: str, source: Optional[Dict[str, Any]] = None) -> Optional[float]:
        cached = self._price_for(contract_symbol)
        if cached is not None:
            return cached

        for key in ("mark_price", "markPrice", "index_price", "indexPrice", "last", "last_price", "price"):
            price = self._positive_float((source or {}).get(key))
            if price is not None:
                self._remember_price(contract_symbol, price)
                return price

        try:
            ticker = await market_service.get_ticker(self.exchange, contract_symbol)
        except Exception as exc:
            logger.warning("[OkxFundingArbitrage] ticker price fetch failed symbol=%s error=%s", contract_symbol, exc)
            return None

        for key in ("mark_price", "markPrice", "last", "last_price", "price", "close"):
            price = self._positive_float((ticker or {}).get(key))
            if price is not None:
                self._remember_price(contract_symbol, price)
                return price
        return None

    def _min_contract_notional(self, contract_symbol: str, price: float) -> Optional[float]:
        fn = getattr(self.broker, "min_contract_notional", None)
        if not callable(fn):
            return 0.0
        try:
            self._contract_metadata_errors.pop(contract_symbol, None)
            return max(0.0, float(fn(contract_symbol, price) or 0.0))
        except Exception as exc:
            error = str(exc)
            self._contract_metadata_errors[contract_symbol] = error
            logger.warning(
                "[OkxFundingArbitrage] contract metadata unavailable symbol=%s error=%s",
                contract_symbol,
                error,
                exc_info=True,
            )
            return None

    @staticmethod
    def _positive_float(value: Any) -> Optional[float]:
        try:
            out = float(value)
        except (TypeError, ValueError):
            return None
        return out if math.isfinite(out) and out > 0 else None

    @staticmethod
    async def _skipped_order(reason: str, symbol: str) -> OrderResult:
        return OrderResult({"status": "skipped", "reason": reason, "symbol": symbol})

    @staticmethod
    def _normalize_contract_list(values: Any) -> List[str]:
        if isinstance(values, str):
            raw_values: Iterable[Any] = [part.strip() for part in values.split(",")]
        elif isinstance(values, Iterable):
            raw_values = values
        else:
            raw_values = []
        out = []
        for value in raw_values:
            symbol = normalize_contract_symbol(str(value or "").strip())
            if symbol:
                out.append(symbol)
        return out

    @staticmethod
    def _spot_symbol(symbol: str) -> str:
        s = str(symbol or "").strip().upper()
        if not s:
            return s
        if s.endswith("-SWAP"):
            parts = s.split("-")
            if len(parts) >= 2:
                return f"{parts[0]}/{parts[1]}"
        normalized = normalize_contract_symbol(s)
        if ":" in normalized:
            return normalized.split(":", 1)[0]
        return normalized

    @staticmethod
    def _extract_rate(data: Dict[str, Any]) -> float:
        for key in ("current_rate", "fundingRate", "funding_rate", "rate"):
            try:
                value = float(data.get(key))
            except (TypeError, ValueError):
                value = 0.0
            if value:
                return value
        info = data.get("info")
        if isinstance(info, dict):
            for key in ("fundingRate", "funding_rate", "rate"):
                try:
                    value = float(info.get(key))
                except (TypeError, ValueError):
                    value = 0.0
                if value:
                    return value
        return 0.0

    @staticmethod
    def _funding_timestamp_ms(data: Dict[str, Any]) -> Optional[int]:
        for key in ("next_funding_time", "nextFundingTime", "fundingTime", "funding_time"):
            try:
                value = int(float(data.get(key)))
            except (TypeError, ValueError):
                value = 0
            if value > 0:
                return value
        info = data.get("info")
        if isinstance(info, dict):
            for key in ("next_funding_time", "nextFundingTime", "fundingTime", "funding_time"):
                try:
                    value = int(float(info.get(key)))
                except (TypeError, ValueError):
                    value = 0
                if value > 0:
                    return value
        return None

    @classmethod
    def _top_funding_rates(cls, opportunities: Iterable[Dict[str, Any]], *, limit: int = 5) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for item in opportunities:
            symbol = normalize_contract_symbol(str(item.get("symbol") or ""))
            rate = cls._extract_rate(item)
            annualized = rate * 3 * 365
            rows.append(
                {
                    "symbol": symbol,
                    "funding_rate": rate,
                    "funding_rate_pct": rate * 100,
                    "annualized_rate": annualized,
                    "annualized_pct": annualized * 100,
                }
            )
        rows.sort(key=lambda row: float(row["annualized_rate"]), reverse=True)
        return rows[: max(0, int(limit))]

    @staticmethod
    def _format_top_funding_rates(rows: List[Dict[str, Any]]) -> str:
        if not rows:
            return "暂无数据"
        parts = []
        for row in rows:
            parts.append(
                f"{row['symbol']} {float(row['annualized_pct']):+.2f}%/年"
                f"({float(row['funding_rate_pct']):+.4f}%/次)"
            )
        return "；".join(parts)

    @staticmethod
    def _parse_rate(value: Any, default: float) -> float:
        try:
            rate = float(value)
        except (TypeError, ValueError):
            return default
        if rate > 1.0:
            rate /= 100.0
        return max(0.0, rate)

    @staticmethod
    def _coerce_order_result(value: Any) -> OrderResult:
        if isinstance(value, Exception):
            return OrderResult({"status": "rejected", "error": str(value)})
        if isinstance(value, dict):
            return OrderResult(value)
        return OrderResult({"status": "rejected", "error": f"unexpected order result: {value!r}"})

    @staticmethod
    def _filled(result: Dict[str, Any]) -> bool:
        if result.get("error"):
            return False
        status = str(result.get("status") or "filled").lower()
        return status == "filled"

    @staticmethod
    def _closed_or_absent(result: Dict[str, Any]) -> bool:
        if result.get("error"):
            return False
        status = str(result.get("status") or "filled").lower()
        reason = str(result.get("reason") or "")
        return status == "filled" or (status == "skipped" and reason in {"no_position", "no_spot_position"})

    async def _emit(self, decision: str, summary: str, level: str = "info", **details: Any) -> None:
        payload = {
            "type": "bar_diag",
            "decision": decision,
            "decision_label": DECISION_LABELS.get(decision, decision),
            "summary": summary,
            "level": level,
            "details": details,
        }
        await self.broadcast_strategy_channel(payload)
