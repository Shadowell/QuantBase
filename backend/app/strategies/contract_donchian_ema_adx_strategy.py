"""OKX USDT perpetual Donchian breakout with EMA and ADX confirmation."""

from __future__ import annotations

import math
from typing import Dict, List, Optional

from app.core.execution.base_strategy import BarData
from app.strategies.contract_common import ContractStrategyBase, atr, closes, ema, is_finite_price


class ContractDonchianEmaAdxStrategy(ContractStrategyBase):
    async def on_init(self) -> None:
        await super().on_init()
        self.lookback_bars = max(2, int(self.config.get("lookback_bars", 55)))
        self.ema_window = max(2, int(self.config.get("ema_window", 144)))
        self.atr_window = max(1, int(self.config.get("atr_window", 14)))
        self.adx_window = max(1, int(self.config.get("adx_window", 14)))
        self.min_adx = max(0.0, float(self.config.get("min_adx", 8.0)))
        self.atr_stop_mult = max(0.0, float(self.config.get("atr_stop_mult", 3.5)))
        self.take_profit_atr_mult = max(0.0, float(self.config.get("take_profit_atr_mult", 0.0)))
        self.min_stop_pct = max(0.0, float(self.config.get("min_stop_pct", 0.02)))
        self.min_holding_bars = max(0, int(self.config.get("min_holding_bars", 1)))
        self.max_holding_bars = max(self.min_holding_bars + 1, int(self.config.get("max_holding_bars", 30)))
        self.ema_soft_exit = bool(self.config.get("ema_soft_exit", True))
        self.reversal_exit = bool(self.config.get("reversal_exit", True))
        self._entry_price: Dict[tuple[str, str], float] = {}
        self._trail_stop: Dict[tuple[str, str], float] = {}
        self._opened_bar: Dict[tuple[str, str], int] = {}

    async def on_bar(self, bar: BarData) -> None:
        if not is_finite_price(bar.close):
            return
        bars = self._append_bar(bar)
        if len(bars) < self._warmup_required():
            return

        price = float(bar.close)
        trend_ema = ema(closes(bars), self.ema_window)
        volatility = atr(bars, self.atr_window) or 0.0
        if trend_ema is None or volatility <= 0:
            return

        long_pos = await self.get_contract_position(bar.symbol, "long")
        short_pos = await self.get_contract_position(bar.symbol, "short")
        signal = self._signal(bars, price, trend_ema)

        if long_pos:
            if self._should_close(bar.symbol, "long", long_pos, price, trend_ema, volatility, signal):
                await self._close_and_reset(bar.symbol, "long", price)
            return
        if short_pos:
            if self._should_close(bar.symbol, "short", short_pos, price, trend_ema, volatility, signal):
                await self._close_and_reset(bar.symbol, "short", price)
            return

        if signal == "long":
            result = await self._open_if_flat(bar.symbol, "long", price)
            self._track_open(bar.symbol, "long", price, result)
        elif signal == "short":
            result = await self._open_if_flat(bar.symbol, "short", price)
            self._track_open(bar.symbol, "short", price, result)

    def _warmup_required(self) -> int:
        adx_required = self.adx_window * 2 + 1 if self.min_adx > 0 else 0
        return max(self.lookback_bars + 1, self.ema_window, self.atr_window + 1, adx_required)

    def _signal(self, bars: List[BarData], price: float, trend_ema: float) -> Optional[str]:
        if self.min_adx > 0:
            adx_value = self._adx(bars, self.adx_window)
            if adx_value is None or adx_value < self.min_adx:
                return None

        prev_channel = bars[-self.lookback_bars - 1:-1]
        if len(prev_channel) < self.lookback_bars:
            return None
        channel_high = max(float(item.high) for item in prev_channel)
        channel_low = min(float(item.low) for item in prev_channel)
        if price > channel_high and price > trend_ema:
            return "long"
        if price < channel_low and price < trend_ema:
            return "short"
        return None

    def _should_close(
        self,
        symbol: str,
        side: str,
        position: dict,
        price: float,
        trend_ema: float,
        volatility: float,
        signal: Optional[str],
    ) -> bool:
        key = (symbol, side)
        self._opened_bar.setdefault(key, self._bar_counts.get(symbol, 0))
        holding_bars = max(0, int(self._bar_counts.get(symbol, 0)) - int(self._opened_bar[key]))
        entry = self._entry_price.get(key) or self._position_entry_price(position) or price
        stop_distance = max(volatility * self.atr_stop_mult, price * self.min_stop_pct)

        if side == "long":
            stop = max(self._trail_stop.get(key, -float("inf")), price - stop_distance)
            self._trail_stop[key] = stop
            if price <= stop:
                return True
            if self.take_profit_atr_mult > 0 and price >= entry + volatility * self.take_profit_atr_mult:
                return True
            if holding_bars >= self.min_holding_bars and self.reversal_exit and signal == "short":
                return True
            if holding_bars >= self.min_holding_bars and self.ema_soft_exit and price < trend_ema:
                return True
        else:
            stop = min(self._trail_stop.get(key, float("inf")), price + stop_distance)
            self._trail_stop[key] = stop
            if price >= stop:
                return True
            if self.take_profit_atr_mult > 0 and price <= entry - volatility * self.take_profit_atr_mult:
                return True
            if holding_bars >= self.min_holding_bars and self.reversal_exit and signal == "long":
                return True
            if holding_bars >= self.min_holding_bars and self.ema_soft_exit and price > trend_ema:
                return True
        return holding_bars >= self.max_holding_bars

    def _track_open(self, symbol: str, side: str, price: float, result) -> None:
        status = str(result.get("status") or "").lower()
        if status not in {"filled", "submitted", "accepted"}:
            return
        key = (symbol, side)
        self._trail_stop.pop(key, None)
        self._entry_price.pop(key, None)
        self._opened_bar.pop(key, None)
        if status == "filled":
            self._entry_price[key] = price
            self._opened_bar[key] = int(self._bar_counts.get(symbol, 0))

    async def _close_and_reset(self, symbol: str, side: str, price: float) -> None:
        result = await self._close_if_present(symbol, side, price)
        if str(result.get("status") or "").lower() not in {"filled", "submitted", "accepted"}:
            return
        key = (symbol, side)
        self._entry_price.pop(key, None)
        self._opened_bar.pop(key, None)
        self._trail_stop.pop(key, None)

    def _position_entry_price(self, position: dict) -> float:
        for key in ("entry_price", "avg_price", "avgPx", "mark_price"):
            try:
                value = float(position.get(key) or 0)
            except (TypeError, ValueError):
                value = 0.0
            if value > 0:
                return value
        return 0.0

    def _adx(self, bars: List[BarData], window: int) -> Optional[float]:
        if window <= 0 or len(bars) < window * 2 + 1:
            return None
        plus_dm: List[float] = []
        minus_dm: List[float] = []
        true_ranges: List[float] = []
        recent = bars[-(window * 2 + 1):]
        for prev, cur in zip(recent[:-1], recent[1:]):
            up_move = float(cur.high) - float(prev.high)
            down_move = float(prev.low) - float(cur.low)
            plus_dm.append(up_move if up_move > down_move and up_move > 0 else 0.0)
            minus_dm.append(down_move if down_move > up_move and down_move > 0 else 0.0)
            true_ranges.append(
                max(
                    float(cur.high) - float(cur.low),
                    abs(float(cur.high) - float(prev.close)),
                    abs(float(cur.low) - float(prev.close)),
                )
            )
        dx_values: List[float] = []
        for idx in range(window, len(true_ranges) + 1):
            tr_sum = sum(true_ranges[idx - window:idx])
            if tr_sum <= 0 or not math.isfinite(tr_sum):
                continue
            plus_di = 100.0 * sum(plus_dm[idx - window:idx]) / tr_sum
            minus_di = 100.0 * sum(minus_dm[idx - window:idx]) / tr_sum
            denom = plus_di + minus_di
            if denom <= 0:
                continue
            dx_values.append(100.0 * abs(plus_di - minus_di) / denom)
        if not dx_values:
            return None
        return sum(dx_values[-window:]) / min(window, len(dx_values))
