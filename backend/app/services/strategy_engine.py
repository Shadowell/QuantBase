"""
策略执行引擎（v2 — BaseStrategy 架构 · MVP 可运行版）
=============================================

核心能力：
1. 通过 importlib 按「模块路径 + 类名」加载继承自 BaseStrategy 的策略。
2. PaperBroker：内存撮合，带详细交易日志（时间、币种、方向、价格、数量、成交额、手续费、盈亏）。
3. LiveBroker：转发到 trading_service 执行真实订单。
4. 异步主循环：拉取 K 线 → 组装 BarData → 驱动 on_bar() → 智能 sleep 对齐下一根 bar 收盘。
5. 全局账户级风控巡检（RiskManager + Kill Switch）。
"""

import asyncio
import importlib
import importlib.util
import inspect
import json
import logging
import math
import os
import sys
import tempfile
import time
import traceback
from datetime import datetime, timezone
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.core.execution.base_strategy import (
    BaseStrategy,
    BarData,
    Broker,
    OrderResult,
    StrategyState,
    TickData,
)
from app.db.local_db import db_instance as db
from app.exchange import exchange_manager
from app.services.risk_manager import RiskConfig, RiskLevel, RiskManager
from app.services.feishu_notifier import feishu_notifier
from app.services.trading_service import trading_service, OrderSide, OrderType
from app.services.websocket_service import connection_manager
from app.services.contract_paper_account import (
    ContractInstrument,
    ContractPaperAccount,
    load_contract_instruments,
    normalize_contract_symbol,
)
from app.services.cross_exchange_paper_account import CrossExchangePaperBroker
from app.services.exchange_fee_model import default_fee_schedule

logger = logging.getLogger(__name__)

# ============================================================
# 时间粒度 → 秒 映射
# ============================================================

_TIMEFRAME_SECONDS: Dict[str, int] = {
    "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "2h": 7200, "4h": 14400, "6h": 21600, "12h": 43200,
    "1d": 86400,
}

# 历史 K 已喂入、指标就绪之后，默认立即允许下一根实时 K 线决策。
DEFAULT_WARMUP_ORDER_DELAY_SEC = 0.0
# CCXT 单次 fetch_ohlcv 根数上限（分批回拉大量预热 K 线）。
_WARMUP_OHLCV_CHUNK = 300
# 模拟盘卖出后会出现 1e-12 量级残余；监控展示与 mark 刷新都不应把它当真实仓位。
_POSITION_DISPLAY_EPSILON = 1e-9
_EXIT_TRADE_SIDES = {"sell", "spot_sell", "close_long", "close_short", "close_pair"}
_RUNTIME_STATE_SETTING_PREFIX = "strategy_runtime_state:"
_PERSISTED_RUNTIME_POSITION_KEYS = {"_cta_risk_state", "_cross_exchange_arbitrage"}


def _is_tick_driven_strategy_config(config: Dict[str, Any]) -> bool:
    strategy_type = str(config.get("strategy_type") or config.get("strategyType") or "").lower()
    strategy_key = str(config.get("strategy_key") or config.get("strategyKey") or "").lower()
    name = str(config.get("name") or "").lower()
    return (
        bool(config.get("tick_driven") or config.get("tickDriven"))
        or strategy_type in {"market_making", "market-making", "mm", "做市"}
        or "market_making" in strategy_key
        or "做市" in name
    )


def _resolve_warmup_bar_count(context: "StrategyContext") -> int:
    """
    预热需要拉取的**已收盘** K 线根数。
    - 若策略 config 显式设置 warmup_bars，以该值为准。
    - 否则取常见窗口键的最大值：min_1m_for_30m_stack / window_size / lookback_bars，至少 100。
    """
    cfg = context.config or {}
    if cfg.get("warmup_bars") is not None:
        return max(1, int(cfg["warmup_bars"]))
    candidates = [100]
    for key in ("min_1m_for_30m_stack", "window_size", "lookback_bars"):
        v = cfg.get(key)
        if v is None:
            continue
        try:
            candidates.append(int(v))
        except (TypeError, ValueError):
            continue
    return max(candidates)


def _sync_fetch_warmup_ohlcv_closed(exchange, symbol: str, timeframe: str, n_closed: int) -> List[Any]:
    """
    同步拉取最近 n_closed 根**已收盘** K 线（升序）。
    根数较大时自动分批 since 向前分页，避免超过交易所单次 limit。
    """
    if n_closed < 1:
        return []

    tf_ms = _TIMEFRAME_SECONDS.get(timeframe, 60) * 1000
    chunk = min(_WARMUP_OHLCV_CHUNK, max(50, n_closed + 2))

    try:
        now_ms = exchange.milliseconds()
    except Exception:
        now_ms = int(time.time() * 1000)

    margin_bars = max(n_closed + chunk * 2, chunk * 3)
    since = max(0, now_ms - margin_bars * tf_ms)
    by_ts: Dict[int, Any] = {}

    for _ in range(500):
        batch = exchange.fetch_ohlcv(symbol, timeframe, since=since, limit=chunk)
        if not batch:
            break
        for row in batch:
            by_ts[_ohlcv_row_ts_ms(row)] = row
        since = _ohlcv_row_ts_ms(batch[-1]) + 1
        last_ts = _ohlcv_row_ts_ms(batch[-1])
        if len(batch) < chunk or last_ts >= now_ms - 2 * tf_ms:
            break
        time.sleep(0.12)

    if not by_ts:
        return []

    merged = [by_ts[t] for t in sorted(by_ts.keys())]
    if len(merged) < 2:
        return merged[:-1] if len(merged) == 1 else merged

    # 去掉最后一根（可能仍在形成中），再取末尾 n_closed 根
    body = merged[:-1]
    return body[-n_closed:] if len(body) >= n_closed else body


def _ohlcv_row_ts_ms(row: Any) -> int:
    """BaseExchange.fetch_ohlcv 返回 dict 行；CCXT 原生为 [ts, o, h, l, c, v] 列表。统一取毫秒时间戳。"""
    if isinstance(row, dict):
        return int(row["timestamp"])
    return int(row[0])


def _candle_to_bar(candle, exchange_name: str, symbol: str, timeframe: str) -> BarData:
    """将 OHLCV candle（dict 或 list）统一转为 BarData。"""
    if isinstance(candle, dict):
        return BarData(
            exchange=exchange_name, symbol=symbol, timeframe=timeframe,
            timestamp=int(candle["timestamp"]),
            open=float(candle["open"]),
            high=float(candle["high"]),
            low=float(candle["low"]),
            close=float(candle["close"]),
            volume=float(candle.get("volume", 0)),
        )
    return BarData(
        exchange=exchange_name, symbol=symbol, timeframe=timeframe,
        timestamp=int(candle[0]),
        open=float(candle[1]),
        high=float(candle[2]),
        low=float(candle[3]),
        close=float(candle[4]),
        volume=float(candle[5]),
    )


def _seconds_until_next_bar(timeframe: str) -> float:
    """计算距离下一根 K 线收盘的秒数（对齐整周期边界 + 5s 缓冲等数据落盘）。"""
    interval = _TIMEFRAME_SECONDS.get(timeframe, 60)
    now = time.time()
    next_close = (math.floor(now / interval) + 1) * interval
    wait = next_close - now + 5.0
    return max(wait, 3.0)


def _expected_last_closed_bar_ts_ms(timeframe: str, *, safety_delay_sec: float = 5.0) -> int:
    """
    估算“最新一根**已收盘**K线”的起始时间戳（毫秒）。

    - safety_delay_sec：给交易所数据落盘预留缓冲（与 _seconds_until_next_bar 的 +5s 对齐）。
    - 用于缓存校验：避免跨策略重复拉同一根已收盘 bar（同一根 bar 的 timestamp 不变），
      但一旦进入下一根 bar 的窗口，就强制刷新，即便缓存 TTL 仍未过期。
    """
    interval = _TIMEFRAME_SECONDS.get(timeframe, 60)
    now = time.time() - max(float(safety_delay_sec), 0.0)
    bucket_close = math.floor(now / interval) * interval
    start = bucket_close - interval
    return max(0, int(start * 1000))


class StrategyStatus(str, Enum):
    STOPPED = "stopped"
    RUNNING = "running"
    PAUSED = "paused"
    ERROR = "error"


def _parse_run_started_at_from_db(iso: Optional[str]) -> Optional[datetime]:
    """解析 strategies.run_started_at（SQLite 存 ISO 字符串，缺省/坏值返回 None）。"""
    if not iso or not str(iso).strip():
        return None
    s = str(iso).strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


@dataclass
class StrategyContext:
    """引擎侧的策略运行上下文。"""
    strategy_id: int
    name: str
    exchange: str
    symbols: List[str]
    config: Dict[str, Any]

    status: StrategyStatus = StrategyStatus.STOPPED
    error_message: Optional[str] = None
    started_at: Optional[datetime] = None

    total_trades: int = 0
    pnl: float = 0.0


def _list_from_symbols(value: Any) -> List[str]:
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(part).strip() for part in value if str(part).strip()]
    return []


def _normalize_contract_symbol_list(value: Any) -> List[str]:
    symbols: List[str] = []
    seen = set()
    for item in _list_from_symbols(value):
        symbol = normalize_contract_symbol(item)
        if not symbol or symbol in seen:
            continue
        symbols.append(symbol)
        seen.add(symbol)
    return symbols


def _float_value(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        out = float(value)
        return out if math.isfinite(out) else default
    except (TypeError, ValueError):
        return default


def _slippage_rate_from_config(config: Dict[str, Any], *, default_rate: float = 0.0001) -> float:
    slippage_bps = _float_value(config.get("slippage_bps"), -1.0)
    if slippage_bps >= 0:
        return slippage_bps / 10_000.0
    return max(0.0, _float_value(config.get("slippage_rate"), default_rate))


def _is_ai_autonomous_config(config: Dict[str, Any]) -> bool:
    return (
        str(config.get("strategy_key") or "").strip() == "ai_autonomous_trader"
        or bool(config.get("ai_autonomous_trader"))
    )


# ============================================================
# PaperBroker — 模拟盘券商（详细日志版）
# ============================================================


class PaperBroker:
    """
    内存撮合的模拟盘 Broker，实现 BaseStrategy.Broker 协议。

    特性：
    - 维护虚拟 balance 和 positions
    - 买卖自动扣除手续费 (默认 0.1%)
    - 每笔成交打印详细日志 + 写入数据库持久化
    - 通过 update_mark_price() 更新浮动盈亏
    - warmup_mode=True 时拒绝下单（仅更新价格），用于历史预热阶段
    """

    def __init__(
        self,
        initial_capital: float = 10000.0,
        commission_rate: float = 0.001,
        slippage_rate: float = 0.0001,
        strategy_id: int = 0,
        exchange_name: str = "okx",
    ):
        self.initial_capital = initial_capital
        self.balance = initial_capital
        self.commission_rate = commission_rate
        self.slippage_rate = slippage_rate
        self.positions: Dict[str, Dict[str, Any]] = {}
        self._cost_lots: Dict[str, List[Dict[str, float]]] = {}
        self.trades: List[Dict[str, Any]] = []
        self._last_prices: Dict[str, float] = {}
        self.warmup_mode: bool = False
        # time.monotonic() 在此刻之前禁止真实/模拟成交（历史 K 喂完后的追加墙钟缓冲）
        self.orders_deadline_monotonic: float = 0.0
        self._strategy_id = strategy_id
        self._exchange_name = exchange_name

    def restore_from_trades(self, trades: List[Dict[str, Any]]) -> None:
        """从已持久化成交恢复模拟盘余额、持仓与成交计数。"""
        restored = 0
        for row in sorted(trades, key=lambda x: (int(x.get("timestamp") or 0), int(x.get("id") or 0))):
            try:
                symbol = str(row.get("symbol") or "")
                side = str(row.get("side") or "").upper()
                price = float(row.get("price") or 0)
                amount = float(row.get("quantity") or 0)
                fee = float(row.get("fee") or 0)
                pnl = float(row.get("pnl") or 0)
            except (TypeError, ValueError):
                continue
            if not symbol or price <= 0 or amount <= 0:
                continue

            cost = price * amount
            if side == "BUY":
                self.balance -= cost + fee
                self._cost_lots.setdefault(symbol, []).append(
                    {"qty": amount, "price": price, "fee": fee}
                )
                pos = self.positions.setdefault(
                    symbol,
                    {"size": 0.0, "entry_price": 0.0, "side": "long", "unrealized_pnl": 0.0},
                )
                prev_size = float(pos.get("size") or 0)
                if prev_size <= 0:
                    pos["entry_price"] = price
                else:
                    pos["entry_price"] = (pos["entry_price"] * prev_size + price * amount) / (prev_size + amount)
                pos["size"] = prev_size + amount
                pos["side"] = "long"
            elif side == "SELL":
                self.balance += cost - fee
                self._consume_cost_lots(
                    symbol,
                    amount,
                    sell_price=price,
                    sell_fee=fee,
                    fallback_entry_price=0.0,
                )
                pos = self.positions.setdefault(
                    symbol,
                    {"size": 0.0, "entry_price": 0.0, "side": "long", "unrealized_pnl": 0.0},
                )
                pos["size"] = max(0.0, float(pos.get("size") or 0) - amount)
                if pos["size"] < 1e-12:
                    pos["size"] = 0.0
                    pos["entry_price"] = 0.0
                    pos["unrealized_pnl"] = 0.0
            else:
                continue

            self._last_prices[symbol] = price
            self.trades.append(
                {
                    "time": datetime.fromtimestamp(int(row.get("timestamp") or 0) / 1000).strftime("%Y-%m-%d %H:%M:%S"),
                    "symbol": symbol,
                    "side": side,
                    "price": price,
                    "amount": amount,
                    "cost": cost,
                    "fee": fee,
                    "pnl": pnl,
                }
            )
            restored += 1

        for symbol, pos in self.positions.items():
            mark = self._last_prices.get(symbol, pos.get("entry_price") or 0)
            if pos.get("size", 0) > 0 and mark > 0:
                pos["unrealized_pnl"] = (mark - pos["entry_price"]) * pos["size"]

        if restored:
            open_positions = sum(1 for pos in self.positions.values() if pos.get("size", 0) > 1e-12)
            logger.info(
                "[PaperBroker] 已从成交记录恢复模拟盘状态 | strategy=%d trades=%d open_positions=%d balance=%.2f equity=%.2f",
                self._strategy_id,
                restored,
                open_positions,
                self.balance,
                self.equity,
            )

    async def get_available_balance(self, currency: str = "USDT") -> float:
        if str(currency).upper() == "USDT":
            return float(self.balance)
        return 0.0

    @property
    def equity(self) -> float:
        """总权益 = 可用余额 + 所有持仓浮动市值。"""
        total = self.balance
        for sym, pos in self.positions.items():
            if pos["size"] > 0:
                mark = self._last_prices.get(sym, pos["entry_price"])
                total += pos["size"] * mark
        return total

    def get_position_size(self, symbol: str) -> float:
        pos = self.positions.get(symbol)
        return pos["size"] if pos else 0.0

    async def buy(
        self,
        symbol: str,
        amount: float,
        price: Optional[float] = None,
        *,
        order_type: str = "market",
    ) -> OrderResult:
        if self.warmup_mode:
            return OrderResult({"status": "skipped", "reason": "warmup_mode"})
        if self.orders_deadline_monotonic and time.monotonic() < self.orders_deadline_monotonic:
            return OrderResult({"status": "skipped", "reason": "warmup_order_delay"})

        exec_price = price or self._last_prices.get(symbol, 0)
        if exec_price <= 0:
            logger.warning("[PaperBroker] BUY 失败：%s 无可用价格", symbol)
            return OrderResult({"error": "no price available", "symbol": symbol})

        exec_price *= (1 + self.slippage_rate)
        cost = exec_price * amount
        fee = cost * self.commission_rate

        if cost + fee > self.balance:
            affordable = self.balance / (exec_price * (1 + self.commission_rate))
            if affordable < 1e-8:
                logger.warning("[PaperBroker] BUY 失败：%s 余额不足 (need=%.2f, have=%.2f)", symbol, cost + fee, self.balance)
                return OrderResult({"error": "insufficient balance", "symbol": symbol})
            amount = affordable
            cost = exec_price * amount
            fee = cost * self.commission_rate

        self.balance -= (cost + fee)

        pos = self.positions.setdefault(symbol, {"size": 0.0, "entry_price": 0.0, "side": "long", "unrealized_pnl": 0.0})
        if pos["size"] <= 0:
            pos["entry_price"] = exec_price
        else:
            pos["entry_price"] = (pos["entry_price"] * pos["size"] + exec_price * amount) / (pos["size"] + amount)
        pos["size"] += amount
        pos["side"] = "long"

        trade = {
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "symbol": symbol, "side": "BUY", "price": exec_price,
            "amount": amount, "cost": cost, "fee": fee, "pnl": 0.0,
        }
        self._cost_lots.setdefault(symbol, []).append(
            {"qty": amount, "price": exec_price, "fee": fee}
        )
        self.trades.append(trade)
        self._persist_trade(trade)

        logger.info(
            "\033[32m[PaperBroker] ▲ BUY  %s | 价格: %.2f | 数量: %.6f | 成交额: %.2f USDT | "
            "手续费: %.4f | 持仓: %.6f | 余额: %.2f USDT\033[0m",
            symbol, exec_price, amount, cost, fee, pos["size"], self.balance,
        )
        try:
            await feishu_notifier.notify_trade(
                strategy=f"Paper#{self._strategy_id}", symbol=symbol, side="BUY",
                price=exec_price, amount=amount, cost=cost, fee=fee,
            )
        except Exception:
            pass
        return OrderResult(trade)

    async def sell(
        self,
        symbol: str,
        amount: float,
        price: Optional[float] = None,
        *,
        order_type: str = "market",
    ) -> OrderResult:
        if self.warmup_mode:
            return OrderResult({"status": "skipped", "reason": "warmup_mode"})
        if self.orders_deadline_monotonic and time.monotonic() < self.orders_deadline_monotonic:
            return OrderResult({"status": "skipped", "reason": "warmup_order_delay"})

        exec_price = price or self._last_prices.get(symbol, 0)
        if exec_price <= 0:
            logger.warning("[PaperBroker] SELL 失败：%s 无可用价格", symbol)
            return OrderResult({"error": "no price available", "symbol": symbol})

        pos = self.positions.get(symbol)
        if not pos or pos["size"] <= 1e-12:
            logger.warning("[PaperBroker] SELL 跳过：%s 当前无持仓", symbol)
            return OrderResult({"status": "skipped", "reason": "no_position", "symbol": symbol})

        sell_qty = min(amount, float(pos.get("size") or 0))
        if sell_qty <= 1e-12:
            return OrderResult({"status": "skipped", "reason": "qty_zero", "symbol": symbol})

        exec_price *= (1 - self.slippage_rate)
        revenue = exec_price * sell_qty
        fee = revenue * self.commission_rate

        pnl = self._consume_cost_lots(
            symbol,
            sell_qty,
            sell_price=exec_price,
            sell_fee=fee,
            fallback_entry_price=float(pos.get("entry_price") or 0),
        )
        pos["size"] -= sell_qty
        if pos["size"] < 1e-12:
            pos["size"] = 0.0
            pos["entry_price"] = 0.0
            pos["unrealized_pnl"] = 0.0

        self.balance += (revenue - fee)

        remaining = pos["size"] if pos else 0.0
        trade = {
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "symbol": symbol, "side": "SELL", "price": exec_price,
            "amount": sell_qty, "cost": revenue, "fee": fee, "pnl": pnl,
        }
        self.trades.append(trade)
        self._persist_trade(trade)

        color = "\033[31m" if pnl < 0 else "\033[32m"
        logger.info(
            "%s[PaperBroker] ▼ SELL %s | 价格: %.2f | 数量: %.6f | 成交额: %.2f USDT | "
            "手续费: %.4f | 盈亏: %+.2f | 剩余持仓: %.6f | 余额: %.2f USDT\033[0m",
            color, symbol, exec_price, sell_qty, revenue, fee, pnl, remaining, self.balance,
        )
        try:
            await feishu_notifier.notify_trade(
                strategy=f"Paper#{self._strategy_id}", symbol=symbol, side="SELL",
                price=exec_price, amount=sell_qty, cost=revenue, fee=fee, pnl=pnl,
            )
        except Exception:
            pass
        return OrderResult(trade)

    def _consume_cost_lots(
        self,
        symbol: str,
        amount: float,
        *,
        sell_price: float,
        sell_fee: float,
        fallback_entry_price: float,
    ) -> float:
        """按 FIFO 成本批次计算卖出净盈亏，包含对应买入手续费和本次卖出手续费。"""
        remaining = max(0.0, float(amount or 0.0))
        if remaining <= 1e-12:
            return 0.0

        lots = self._cost_lots.setdefault(symbol, [])
        realized = 0.0
        sold = 0.0
        while lots and remaining > 1e-12:
            lot = lots[0]
            lot_qty = float(lot.get("qty") or 0.0)
            if lot_qty <= 1e-12:
                lots.pop(0)
                continue
            qty = min(remaining, lot_qty)
            buy_price = float(lot.get("price") or fallback_entry_price or 0.0)
            buy_fee = float(lot.get("fee") or 0.0)
            buy_fee_part = buy_fee * (qty / lot_qty) if lot_qty > 0 else 0.0
            realized += (sell_price - buy_price) * qty - buy_fee_part
            sold += qty
            remaining -= qty
            lot["qty"] = lot_qty - qty
            lot["fee"] = max(0.0, buy_fee - buy_fee_part)
            if lot["qty"] <= 1e-12:
                lots.pop(0)

        if remaining > 1e-12 and fallback_entry_price > 0:
            realized += (sell_price - fallback_entry_price) * remaining
            sold += remaining

        if sold <= 1e-12:
            return -sell_fee
        return realized - sell_fee

    async def close_position(self, symbol: str) -> OrderResult:
        pos = self.positions.get(symbol)
        if not pos or pos["size"] <= 0:
            return OrderResult({"closed": False, "reason": "no position", "symbol": symbol})
        return await self.sell(symbol, pos["size"])

    def update_mark_price(self, symbol: str, price: float):
        """更新标记价格（用于浮动盈亏计算和后续买卖定价）。"""
        self._last_prices[symbol] = price
        pos = self.positions.get(symbol)
        if pos and pos["size"] > 0:
            pos["unrealized_pnl"] = (price - pos["entry_price"]) * pos["size"]

    def _persist_trade(self, trade: Dict[str, Any]):
        """将交易记录写入数据库。"""
        if self._strategy_id <= 0:
            return
        try:
            db.insert_strategy_trade(self._strategy_id, {
                "exchange": self._exchange_name,
                "symbol": trade["symbol"],
                "order_id": f"paper_{int(datetime.now().timestamp() * 1000)}",
                "timestamp": int(datetime.now().timestamp() * 1000),
                "side": trade["side"].lower(),
                "type": "market",
                "price": trade["price"],
                "quantity": trade["amount"],
                "fee": trade.get("fee", 0),
                "pnl": trade.get("pnl", 0),
            })
        except Exception as e:
            logger.warning("[PaperBroker] 持久化交易记录失败: %s", e)

    def summary(self) -> str:
        """返回账户摘要字符串。"""
        realized_pnl = sum(t.get("pnl", 0) for t in self.trades)
        lines = [
            f"═══ PaperBroker 账户摘要 ═══",
            f"  初始资金:  {self.initial_capital:.2f} USDT",
            f"  当前余额:  {self.balance:.2f} USDT",
            f"  总权益:    {self.equity:.2f} USDT",
            f"  已实现盈亏: {realized_pnl:+.2f} USDT",
            f"  总交易笔数: {len(self.trades)}",
        ]
        for sym, pos in self.positions.items():
            if pos["size"] > 0:
                lines.append(f"  持仓 {sym}: {pos['size']:.6f} @ {pos['entry_price']:.2f}  浮动P&L: {pos.get('unrealized_pnl', 0):+.2f}")
        return "\n".join(lines)


# ============================================================
# ContractPaperBroker — OKX USDT 永续合约模拟盘
# ============================================================


class ContractPaperBroker:
    """Paper broker for OKX USDT perpetual swaps. Never sends real orders."""

    def __init__(
        self,
        *,
        initial_capital: float,
        strategy_id: int,
        exchange_name: str,
        symbols: List[str],
        config: Dict[str, Any],
    ):
        instruments = load_contract_instruments(exchange_name, symbols, config)
        fee_schedule = default_fee_schedule(exchange_name, "swap")
        self.account = ContractPaperAccount(
            initial_equity=float(initial_capital),
            instruments=instruments,
            taker_fee_bps=float(config.get("taker_fee_bps", config.get("fee_bps", fee_schedule.taker_fee_bps))),
            maker_fee_bps=float(config.get("maker_fee_bps", fee_schedule.maker_fee_bps)),
            maintenance_margin_rate=float(config.get("maintenance_margin_rate", 0.005)),
            max_leverage=float(config.get("max_leverage", 5.0)),
        )
        self.initial_capital = float(initial_capital)
        self._strategy_id = strategy_id
        self._exchange_name = exchange_name
        self._config = config
        self.positions = self.account.positions
        self.spot_positions: Dict[str, Dict[str, Any]] = {}
        self._spot_cost_lots: Dict[str, List[Dict[str, float]]] = {}
        self._spot_last_prices: Dict[str, float] = {}
        self.commission_rate = float(config.get("commission_rate", 0.001))
        self.slippage_rate = _slippage_rate_from_config(config, default_rate=0.0)
        self.trades: List[Dict[str, Any]] = []
        self.warmup_mode: bool = False
        self.orders_deadline_monotonic: float = 0.0
        self._current_bar_ts_ms: Optional[int] = None

    @property
    def equity(self) -> float:
        spot_value = 0.0
        for sym, pos in self.spot_positions.items():
            size = float(pos.get("size") or 0.0)
            if size <= 0:
                continue
            mark = float(self._spot_last_prices.get(sym) or pos.get("entry_price") or 0.0)
            spot_value += size * mark
        return self.account.total_equity + spot_value

    @property
    def balance(self) -> float:
        return self.account.free_balance

    async def get_available_balance(self, currency: str = "USDT") -> float:
        return self.account.free_balance if str(currency).upper() == "USDT" else 0.0

    @staticmethod
    def _spot_symbol(symbol: str) -> str:
        s = str(symbol or "").strip().upper()
        if not s:
            return s
        normalized = normalize_contract_symbol(s)
        return normalized.split(":", 1)[0] if "/" in normalized else s

    def update_mark_price(self, symbol: str, price: float):
        px = float(price)
        if px <= 0:
            return []

        spot_symbol = self._spot_symbol(symbol)
        if spot_symbol:
            self._spot_last_prices[spot_symbol] = px
            pos = self.spot_positions.get(spot_symbol)
            if pos and float(pos.get("size") or 0.0) > 0:
                pos["mark_price"] = px
                pos["unrealized_pnl"] = (px - float(pos.get("entry_price") or 0.0)) * float(pos.get("size") or 0.0)

        contract_symbol = normalize_contract_symbol(symbol)
        events = []
        if contract_symbol in self.account.instruments:
            if self.warmup_mode:
                self.account.mark_prices[contract_symbol] = px
                for pos in self.account.positions.values():
                    if pos.symbol == contract_symbol:
                        pos.mark_price = px
                events = []
            else:
                events = self.account.update_mark_price(contract_symbol, px)
        for event in events:
            trade = {
                "status": "filled",
                "action": "liquidation",
                "symbol": normalize_contract_symbol(str(event.get("symbol") or symbol)),
                "pos_side": event.get("pos_side"),
                "contracts": event.get("contracts", 0.0),
                "base_qty": event.get("base_qty", 0.0),
                "leverage": event.get("leverage"),
                "price": event.get("price", price),
                "notional_usdt": event.get("notional_usdt", 0.0),
                "margin": event.get("margin", 0.0),
                "fee": event.get("fee", 0.0),
                "realized_pnl": event.get("realized_pnl", 0.0),
                "liquidation_price": event.get("liquidation_price"),
                "maintenance_margin": event.get("maintenance_margin"),
                "position_equity": event.get("position_equity"),
                "account_equity_before": event.get("account_equity_before"),
            }
            self._persist_contract_trade(trade)
            self.trades.append(trade)
        return events

    def _ensure_instrument(self, symbol: str) -> ContractInstrument:
        normalized = normalize_contract_symbol(symbol)
        inst = self.account.instruments.get(normalized)
        if inst:
            return inst
        loaded = load_contract_instruments(self._exchange_name, [normalized], self._config)
        self.account.instruments.update(loaded)
        inst = self.account.instruments.get(normalized)
        if not inst:
            raise ValueError(f"missing OKX SWAP instrument metadata for {normalized}")
        return inst

    def _ensure_trade_instruments(self, trades: List[Dict[str, Any]]) -> None:
        missing: set[str] = set()
        for row in trades:
            meta = row.get("meta")
            if isinstance(meta, str):
                try:
                    meta = json.loads(meta)
                except json.JSONDecodeError:
                    meta = {}
            if not isinstance(meta, dict) or meta.get("market_type") != "swap":
                continue
            symbol = normalize_contract_symbol(row.get("symbol") or meta.get("symbol") or "")
            if symbol and symbol not in self.account.instruments:
                missing.add(symbol)
        for symbol in sorted(missing):
            self._ensure_instrument(symbol)

    def set_signal_bar_timestamp(self, timestamp_ms: Optional[int]) -> None:
        self._current_bar_ts_ms = int(timestamp_ms) if timestamp_ms is not None else None

    def _contract_execution_price(
        self,
        symbol: str,
        side: str,
        price: Optional[float],
        *,
        closing: bool,
    ) -> Optional[float]:
        raw_price = _float_value(price, 0.0)
        if raw_price <= 0:
            raw_price = _float_value(self.account.mark_prices.get(normalize_contract_symbol(symbol)), 0.0)
        if raw_price <= 0 or self.slippage_rate <= 0:
            return price

        normalized_side = str(side or "").strip().lower()
        if normalized_side not in {"long", "short"}:
            return price
        is_buy = normalized_side == "long"
        if closing:
            is_buy = normalized_side == "short"
        return raw_price * (1.0 + self.slippage_rate if is_buy else 1.0 - self.slippage_rate)

    async def open_contract(
        self,
        symbol: str,
        side: str,
        notional_usdt: float,
        leverage: Optional[float] = None,
        price: Optional[float] = None,
    ) -> OrderResult:
        if self.orders_deadline_monotonic and time.monotonic() < self.orders_deadline_monotonic:
            return OrderResult({"status": "skipped", "reason": "warmup_order_delay"})
        if self.warmup_mode:
            return OrderResult({"status": "skipped", "reason": "warmup_mode"})
        try:
            self._ensure_instrument(symbol)
            result = self.account.open_position(
                symbol,
                side,
                notional_usdt=float(notional_usdt),
                leverage=leverage,
                price=self._contract_execution_price(symbol, side, price, closing=False),
            )
        except ValueError as exc:
            return OrderResult({"status": "rejected", "reason": str(exc), "symbol": symbol, "pos_side": side})
        self._persist_contract_trade(result)
        self.trades.append(result)
        await self._record_live_contract_signal(result)
        self._record_contract_signal(result)
        return OrderResult(result)

    async def close_contract(
        self,
        symbol: str,
        side: str,
        ratio: float = 1.0,
        contracts: Optional[float] = None,
        price: Optional[float] = None,
        *,
        emit_signal: bool = True,
    ) -> OrderResult:
        if self.orders_deadline_monotonic and time.monotonic() < self.orders_deadline_monotonic:
            return OrderResult({"status": "skipped", "reason": "warmup_order_delay"})
        if self.warmup_mode:
            return OrderResult({"status": "skipped", "reason": "warmup_mode"})
        try:
            self._ensure_instrument(symbol)
            result = self.account.close_position(
                symbol,
                side,
                ratio=ratio,
                contracts=contracts,
                price=self._contract_execution_price(symbol, side, price, closing=True),
            )
        except ValueError as exc:
            return OrderResult({"status": "rejected", "reason": str(exc), "symbol": symbol, "pos_side": side})
        if result.get("status") == "filled":
            self._persist_contract_trade(result)
            self.trades.append(result)
            if emit_signal:
                await self._record_live_contract_signal(result, ratio=ratio)
                self._record_contract_signal(result, ratio=ratio)
        return OrderResult(result)

    async def get_contract_position(self, symbol: str, side: str) -> Optional[Dict[str, Any]]:
        return self.account.get_position(symbol, side)

    def min_contract_notional(self, symbol: str, price: float) -> float:
        inst = self._ensure_instrument(symbol)
        px = _float_value(price, 0.0)
        if px <= 0:
            px = _float_value(self.account.mark_prices.get(inst.symbol), 0.0)
        if px <= 0:
            return 0.0
        return max(inst.min_sz, inst.lot_sz) * inst.ct_val * px

    def apply_funding(self, symbol: str, funding_rate: float) -> List[Dict[str, Any]]:
        return self.account.apply_funding(symbol, funding_rate)

    async def buy(self, symbol: str, amount: float, price: Optional[float] = None, *, order_type: str = "market") -> OrderResult:
        if self.orders_deadline_monotonic and time.monotonic() < self.orders_deadline_monotonic:
            return OrderResult({"status": "skipped", "reason": "warmup_order_delay"})
        if self.warmup_mode:
            return OrderResult({"status": "skipped", "reason": "warmup_mode"})

        spot_symbol = self._spot_symbol(symbol)
        exec_price = float(price or self._spot_last_prices.get(spot_symbol, 0.0))
        if exec_price <= 0:
            return OrderResult({"error": "no price available", "symbol": spot_symbol})

        exec_price *= (1 + self.slippage_rate)
        qty = max(0.0, float(amount or 0.0))
        cost = exec_price * qty
        fee = cost * self.commission_rate
        if cost + fee > self.account.free_balance:
            affordable = self.account.free_balance / (exec_price * (1 + self.commission_rate))
            if affordable < 1e-8:
                return OrderResult({"error": "insufficient balance", "symbol": spot_symbol})
            qty = affordable
            cost = exec_price * qty
            fee = cost * self.commission_rate
        if qty <= 1e-12:
            return OrderResult({"status": "skipped", "reason": "qty_zero", "symbol": spot_symbol})

        self.account.free_balance -= cost + fee
        pos = self.spot_positions.setdefault(
            spot_symbol,
            {"symbol": spot_symbol, "size": 0.0, "entry_price": 0.0, "side": "long", "unrealized_pnl": 0.0},
        )
        prev_size = float(pos.get("size") or 0.0)
        if prev_size <= 0:
            pos["entry_price"] = exec_price
        else:
            pos["entry_price"] = (float(pos["entry_price"]) * prev_size + exec_price * qty) / (prev_size + qty)
        pos["size"] = prev_size + qty
        pos["side"] = "long"
        pos["mark_price"] = exec_price
        self._spot_last_prices[spot_symbol] = exec_price
        self._spot_cost_lots.setdefault(spot_symbol, []).append({"qty": qty, "price": exec_price, "fee": fee})

        trade = {
            "status": "filled",
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "symbol": spot_symbol,
            "side": "SPOT_BUY",
            "price": exec_price,
            "amount": qty,
            "cost": cost,
            "fee": fee,
            "pnl": 0.0,
        }
        self._persist_spot_trade(trade)
        self.trades.append(trade)
        return OrderResult(trade)

    async def sell(self, symbol: str, amount: float, price: Optional[float] = None, *, order_type: str = "market") -> OrderResult:
        if self.orders_deadline_monotonic and time.monotonic() < self.orders_deadline_monotonic:
            return OrderResult({"status": "skipped", "reason": "warmup_order_delay"})
        if self.warmup_mode:
            return OrderResult({"status": "skipped", "reason": "warmup_mode"})

        spot_symbol = self._spot_symbol(symbol)
        exec_price = float(price or self._spot_last_prices.get(spot_symbol, 0.0))
        if exec_price <= 0:
            return OrderResult({"error": "no price available", "symbol": spot_symbol})

        pos = self.spot_positions.get(spot_symbol)
        if not pos or float(pos.get("size") or 0.0) <= 1e-12:
            return OrderResult({"status": "skipped", "reason": "no_position", "symbol": spot_symbol})

        qty = min(max(0.0, float(amount or 0.0)), float(pos.get("size") or 0.0))
        if qty <= 1e-12:
            return OrderResult({"status": "skipped", "reason": "qty_zero", "symbol": spot_symbol})

        exec_price *= (1 - self.slippage_rate)
        revenue = exec_price * qty
        fee = revenue * self.commission_rate
        pnl = self._consume_spot_cost_lots(
            spot_symbol,
            qty,
            sell_price=exec_price,
            sell_fee=fee,
            fallback_entry_price=float(pos.get("entry_price") or 0.0),
        )
        pos["size"] = max(0.0, float(pos.get("size") or 0.0) - qty)
        if pos["size"] <= 1e-12:
            pos["size"] = 0.0
            pos["entry_price"] = 0.0
            pos["unrealized_pnl"] = 0.0
        else:
            pos["mark_price"] = exec_price
            pos["unrealized_pnl"] = (exec_price - float(pos.get("entry_price") or 0.0)) * pos["size"]
        self._spot_last_prices[spot_symbol] = exec_price
        self.account.free_balance += revenue - fee

        trade = {
            "status": "filled",
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "symbol": spot_symbol,
            "side": "SPOT_SELL",
            "price": exec_price,
            "amount": qty,
            "cost": revenue,
            "fee": fee,
            "pnl": pnl,
        }
        self._persist_spot_trade(trade)
        self.trades.append(trade)
        return OrderResult(trade)

    async def close_position(self, symbol: str) -> OrderResult:
        details = []
        spot_symbol = self._spot_symbol(symbol)
        spot_pos = self.spot_positions.get(spot_symbol)
        if spot_pos and float(spot_pos.get("size") or 0.0) > 1e-12:
            details.append(await self.sell(spot_symbol, float(spot_pos.get("size") or 0.0)))
        for side in ("long", "short"):
            result = await self.close_contract(symbol, side, ratio=1.0)
            details.append(result)
        return OrderResult({"closed": sum(1 for item in details if item.get("status") == "filled"), "details": details})

    def restore_from_trades(self, trades: List[Dict[str, Any]]) -> None:
        self._ensure_trade_instruments(trades)
        self.account.restore_from_trades(trades)
        self._restore_spot_from_trades(trades)

    def list_spot_positions(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for symbol, pos in self.spot_positions.items():
            size = float(pos.get("size") or 0.0)
            if size <= 1e-12:
                continue
            entry = float(pos.get("entry_price") or 0.0)
            mark = float(self._spot_last_prices.get(symbol) or pos.get("mark_price") or entry)
            out.append(
                {
                    "symbol": symbol,
                    "market_type": "spot",
                    "side": "long",
                    "size": size,
                    "entry_price": entry,
                    "mark_price": mark,
                    "notional_usdt": size * mark,
                    "unrealized_pnl": (mark - entry) * size,
                }
            )
        return out

    def _consume_spot_cost_lots(
        self,
        symbol: str,
        amount: float,
        *,
        sell_price: float,
        sell_fee: float,
        fallback_entry_price: float,
    ) -> float:
        remaining = max(0.0, float(amount or 0.0))
        lots = self._spot_cost_lots.setdefault(symbol, [])
        realized = 0.0
        sold = 0.0
        while lots and remaining > 1e-12:
            lot = lots[0]
            lot_qty = float(lot.get("qty") or 0.0)
            if lot_qty <= 1e-12:
                lots.pop(0)
                continue
            qty = min(remaining, lot_qty)
            buy_price = float(lot.get("price") or fallback_entry_price or 0.0)
            buy_fee = float(lot.get("fee") or 0.0)
            buy_fee_part = buy_fee * (qty / lot_qty) if lot_qty > 0 else 0.0
            realized += (sell_price - buy_price) * qty - buy_fee_part
            sold += qty
            remaining -= qty
            lot["qty"] = lot_qty - qty
            lot["fee"] = max(0.0, buy_fee - buy_fee_part)
            if lot["qty"] <= 1e-12:
                lots.pop(0)
        if remaining > 1e-12 and fallback_entry_price > 0:
            realized += (sell_price - fallback_entry_price) * remaining
            sold += remaining
        if sold <= 1e-12:
            return -sell_fee
        return realized - sell_fee

    def _restore_spot_from_trades(self, rows: List[Dict[str, Any]]) -> None:
        for row in sorted(rows, key=lambda x: (int(x.get("timestamp") or 0), int(x.get("id") or 0))):
            meta = row.get("meta")
            if isinstance(meta, str):
                import json

                try:
                    meta = json.loads(meta)
                except json.JSONDecodeError:
                    meta = {}
            if isinstance(meta, dict) and meta.get("market_type") == "swap":
                continue
            side = str(row.get("side") or "").strip().lower()
            if side not in {"buy", "sell", "spot_buy", "spot_sell"}:
                continue
            symbol = self._spot_symbol(str(row.get("symbol") or ""))
            try:
                price = float(row.get("price") or 0.0)
                amount = float(row.get("quantity") or 0.0)
                fee = float(row.get("fee") or 0.0)
            except (TypeError, ValueError):
                continue
            if not symbol or price <= 0 or amount <= 0:
                continue
            cost = price * amount
            if side in {"buy", "spot_buy"}:
                self.account.free_balance -= cost + fee
                pos = self.spot_positions.setdefault(
                    symbol,
                    {"symbol": symbol, "size": 0.0, "entry_price": 0.0, "side": "long", "unrealized_pnl": 0.0},
                )
                prev = float(pos.get("size") or 0.0)
                pos["entry_price"] = price if prev <= 0 else (float(pos["entry_price"]) * prev + price * amount) / (prev + amount)
                pos["size"] = prev + amount
                pos["mark_price"] = price
                self._spot_cost_lots.setdefault(symbol, []).append({"qty": amount, "price": price, "fee": fee})
            else:
                self.account.free_balance += cost - fee
                pos = self.spot_positions.setdefault(
                    symbol,
                    {"symbol": symbol, "size": 0.0, "entry_price": 0.0, "side": "long", "unrealized_pnl": 0.0},
                )
                self._consume_spot_cost_lots(
                    symbol,
                    amount,
                    sell_price=price,
                    sell_fee=fee,
                    fallback_entry_price=float(pos.get("entry_price") or 0.0),
                )
                pos["size"] = max(0.0, float(pos.get("size") or 0.0) - amount)
                if pos["size"] <= 1e-12:
                    pos["size"] = 0.0
                    pos["entry_price"] = 0.0
                    pos["unrealized_pnl"] = 0.0
            self._spot_last_prices[symbol] = price
            if side in {"buy", "spot_buy"}:
                display_side = "SPOT_BUY"
            else:
                display_side = "SPOT_SELL"
            self.trades.append(
                {
                    "time": datetime.fromtimestamp(int(row.get("timestamp") or 0) / 1000).strftime("%Y-%m-%d %H:%M:%S"),
                    "symbol": symbol,
                    "side": display_side,
                    "price": price,
                    "amount": amount,
                    "cost": cost,
                    "fee": fee,
                    "pnl": float(row.get("pnl") or 0.0),
                }
            )

    def _persist_spot_trade(self, trade: Dict[str, Any]):
        if self._strategy_id <= 0:
            return
        try:
            side = "spot_buy" if str(trade.get("side") or "").upper().endswith("BUY") else "spot_sell"
            db.insert_strategy_trade(
                self._strategy_id,
                {
                    "exchange": self._exchange_name,
                    "symbol": self._spot_symbol(str(trade.get("symbol") or "")),
                    "order_id": f"spot_paper_{side}_{int(datetime.now().timestamp() * 1000)}",
                    "timestamp": int(datetime.now().timestamp() * 1000),
                    "side": side,
                    "type": "market",
                    "price": float(trade.get("price") or 0.0),
                    "quantity": float(trade.get("amount") or 0.0),
                    "fee": float(trade.get("fee") or 0.0),
                    "fee_asset": "USDT",
                    "pnl": float(trade.get("pnl") or 0.0),
                    "meta": {
                        "market_type": "spot",
                        "notional_usdt": float(trade.get("cost") or 0.0),
                    },
                },
            )
        except Exception as e:
            logger.warning("[ContractPaperBroker] 持久化现货对冲腿交易记录失败: %s", e)

    def _persist_contract_trade(self, result: Dict[str, Any]):
        if self._strategy_id <= 0:
            return
        try:
            action = str(result.get("action") or "open")
            pos_side = str(result.get("pos_side") or "")
            leverage = result.get("leverage")
            try:
                leverage_value = float(leverage) if leverage not in (None, "") else None
            except (TypeError, ValueError):
                leverage_value = None
            db.insert_strategy_trade(
                self._strategy_id,
                {
                    "exchange": self._exchange_name,
                    "symbol": normalize_contract_symbol(str(result.get("symbol") or "")),
                    "order_id": f"contract_paper_{action}_{int(datetime.now().timestamp() * 1000)}",
                    "timestamp": int(datetime.now().timestamp() * 1000),
                    "side": f"{action}_{pos_side}".strip("_"),
                    "type": "market",
                    "price": float(result.get("price") or 0.0),
                    "quantity": float(result.get("contracts") or 0.0),
                    "fee": float(result.get("fee") or 0.0),
                    "fee_asset": "USDT",
                    "pnl": float(result.get("realized_pnl") or 0.0),
                    "meta": {
                        "market_type": "swap",
                        "action": action,
                        "pos_side": pos_side,
                        "contracts": float(result.get("contracts") or 0.0),
                        "base_qty": float(result.get("base_qty") or 0.0),
                        "notional_usdt": float(result.get("notional_usdt") or 0.0),
                        "margin": float(result.get("margin") or 0.0),
                        "leverage": leverage_value,
                        "liquidation_price": result.get("liquidation_price"),
                        "maintenance_margin": result.get("maintenance_margin"),
                        "position_equity": result.get("position_equity"),
                        "account_equity_before": result.get("account_equity_before"),
                    },
                },
            )
        except Exception as e:
            logger.warning("[ContractPaperBroker] 持久化合约交易记录失败: %s", e)

    def _record_contract_signal(self, result: Dict[str, Any], *, ratio: Optional[float] = None):
        if self._strategy_id <= 0 or result.get("status") != "filled":
            return
        try:
            from app.services.signal_center_service import signal_center_service

            signal_center_service.record_contract_paper_signal(
                strategy_id=self._strategy_id,
                symbol=normalize_contract_symbol(str(result.get("symbol") or "")),
                action=str(result.get("action") or ""),
                side=str(result.get("pos_side") or ""),
                price=float(result.get("price") or 0.0),
                margin=float(result.get("margin") or 0.0),
                notional_usdt=float(result.get("notional_usdt") or 0.0),
                leverage=float(result.get("leverage") or 1.0),
                ratio=ratio,
                bar_ts_ms=self._current_bar_ts_ms or int(datetime.now(timezone.utc).timestamp() * 1000),
                raw_context={
                    "contracts": float(result.get("contracts") or 0.0),
                    "fee": float(result.get("fee") or 0.0),
                    "realized_pnl": float(result.get("realized_pnl") or 0.0),
                },
            )
        except Exception as e:
            logger.warning("[ContractPaperBroker] 生成 OKX Signal Bot 待确认信号失败: %s", e)

    async def _record_live_contract_signal(self, result: Dict[str, Any], *, ratio: Optional[float] = None):
        if self._strategy_id <= 0 or result.get("status") != "filled":
            return
        try:
            from app.services.live_signal_execution_service import live_signal_execution_service

            payload = {
                "contracts": float(result.get("contracts") or 0.0),
                "base_qty": float(result.get("base_qty") or 0.0),
                "fee": float(result.get("fee") or 0.0),
                "realized_pnl": float(result.get("realized_pnl") or 0.0),
            }
            if ratio is not None:
                payload["ratio"] = ratio
            await live_signal_execution_service.record_contract_signal_and_dispatch(
                source_strategy_id=self._strategy_id,
                exchange=self._exchange_name,
                symbols=list(self.account.instruments.keys()),
                source_config=self._config,
                action=str(result.get("action") or ""),
                symbol=normalize_contract_symbol(str(result.get("symbol") or "")),
                side=str(result.get("pos_side") or ""),
                price=float(result.get("price") or 0.0),
                notional_usdt=float(result.get("notional_usdt") or 0.0),
                leverage=float(result.get("leverage") or 1.0),
                quantity=float(result.get("contracts") or 0.0),
                margin=float(result.get("margin") or 0.0),
                paper_trade_id=str(result.get("order_id") or ""),
                paper_status=str(result.get("status") or ""),
                payload=payload,
            )
        except Exception as e:
            logger.warning("[ContractPaperBroker] 记录/分发实盘订阅信号失败: %s", e)

    def summary(self) -> str:
        return "\n".join(
            [
                "═══ ContractPaperBroker 账户摘要 ═══",
                f"  初始资金:  {self.initial_capital:.2f} USDT",
                f"  可用余额:  {self.account.free_balance:.2f} USDT",
                f"  总权益:    {self.equity:.2f} USDT",
                f"  未实现盈亏: {self.account.total_unrealized_pnl:+.2f} USDT",
                f"  持仓数:    {len(self.account.positions)}",
            ]
        )


# ============================================================
# LiveBroker — 实盘券商
# ============================================================


class LiveBroker:
    """通过 trading_service 发送真实订单，实现 Broker 协议。"""

    def __init__(self, exchange_name: str, strategy_id: int):
        self._exchange_name = exchange_name
        self._strategy_id = strategy_id
        self.orders_deadline_monotonic: float = 0.0
        self.warmup_mode: bool = False

    async def get_available_balance(self, currency: str = "USDT") -> float:
        balances = await trading_service.get_balance(self._exchange_name)
        wanted = str(currency).upper()
        for item in balances:
            if isinstance(item, dict) and str(item.get("currency", "")).upper() == wanted:
                value = item.get("free")
                if value in (None, ""):
                    value = item.get("total")
                try:
                    return float(value or 0.0)
                except (TypeError, ValueError):
                    return 0.0
        return 0.0

    async def buy(self, symbol: str, amount: float, price: Optional[float] = None, *, order_type: str = "market") -> OrderResult:
        if self.orders_deadline_monotonic and time.monotonic() < self.orders_deadline_monotonic:
            logger.info("[LiveBroker] BUY skipped (warmup_order_delay)")
            return OrderResult({"status": "skipped", "reason": "warmup_order_delay"})
        if self.warmup_mode:
            logger.info("[LiveBroker] BUY skipped (warmup_mode)")
            return OrderResult({"status": "skipped", "reason": "warmup_mode"})
        logger.info("[LiveBroker] BUY %s amount=%.6f price=%s type=%s", symbol, amount, price, order_type)
        ot = OrderType.LIMIT if order_type == "limit" else OrderType.MARKET
        result = await trading_service._create_order(self._exchange_name, symbol, OrderSide.BUY, ot, amount, price)
        self._record_trade(symbol, "buy", amount, price, result)
        return OrderResult(result)

    async def sell(self, symbol: str, amount: float, price: Optional[float] = None, *, order_type: str = "market") -> OrderResult:
        if self.orders_deadline_monotonic and time.monotonic() < self.orders_deadline_monotonic:
            logger.info("[LiveBroker] SELL skipped (warmup_order_delay)")
            return OrderResult({"status": "skipped", "reason": "warmup_order_delay"})
        if self.warmup_mode:
            logger.info("[LiveBroker] SELL skipped (warmup_mode)")
            return OrderResult({"status": "skipped", "reason": "warmup_mode"})
        logger.info("[LiveBroker] SELL %s amount=%.6f price=%s type=%s", symbol, amount, price, order_type)
        ot = OrderType.LIMIT if order_type == "limit" else OrderType.MARKET
        result = await trading_service._create_order(self._exchange_name, symbol, OrderSide.SELL, ot, amount, price)
        self._record_trade(symbol, "sell", amount, price, result)
        return OrderResult(result)

    async def close_position(self, symbol: str) -> OrderResult:
        if self.orders_deadline_monotonic and time.monotonic() < self.orders_deadline_monotonic:
            logger.info("[LiveBroker] CLOSE_POSITION skipped (warmup_order_delay)")
            return OrderResult({"status": "skipped", "reason": "warmup_order_delay"})
        if self.warmup_mode:
            logger.info("[LiveBroker] CLOSE_POSITION skipped (warmup_mode)")
            return OrderResult({"status": "skipped", "reason": "warmup_mode"})
        logger.info("[LiveBroker] CLOSE_POSITION %s", symbol)
        results = await trading_service.futures_close_all(self._exchange_name, symbol)
        return OrderResult({"closed": len(results), "details": results})

    def _record_trade(self, symbol: str, side: str, amount: float, price: Optional[float], result: Dict):
        try:
            db.insert_strategy_trade(self._strategy_id, {
                "exchange": self._exchange_name, "symbol": symbol,
                "order_id": result.get("id", ""),
                "timestamp": int(datetime.now().timestamp() * 1000),
                "side": side, "type": "market",
                "price": result.get("price") or price or 0, "quantity": amount,
            })
        except Exception as e:
            logger.warning("Failed to record trade: %s", e)


class LiveContractBroker:
    """OKX USDT 永续合约实盘 broker，复用策略的 open_contract/close_contract 接口。"""

    _DEFAULT_TD_MODE = "isolated"
    _POSITION_MODE_ALIASES = {
        "long_short_mode": "long_short_mode",
        "longshort": "long_short_mode",
        "hedge": "long_short_mode",
        "hedge_mode": "long_short_mode",
        "net": "net_mode",
        "net_mode": "net_mode",
    }

    def __init__(
        self,
        *,
        strategy_id: int,
        exchange_name: str,
        symbols: List[str],
        config: Dict[str, Any],
    ):
        self._strategy_id = int(strategy_id)
        self._exchange_name = exchange_name
        self._config = config or {}
        self._td_mode = self._normalize_td_mode(
            self._config.get("td_mode") or self._config.get("mgn_mode"),
            default=self._DEFAULT_TD_MODE,
        )
        self._max_leverage = max(1.0, _float_value(self._config.get("max_leverage"), 5.0))
        self.instruments: Dict[str, ContractInstrument] = load_contract_instruments(
            exchange_name,
            symbols,
            self._config,
        )
        self.orders_deadline_monotonic: float = 0.0
        self.warmup_mode: bool = False
        self._last_prices: Dict[str, float] = {}
        self._position_mode_cache: Optional[str] = None
        self._leverage_set_cache: set[tuple[str, str, str, float]] = set()
        self._order_seq = 0
        self._spot_broker = LiveBroker(exchange_name, strategy_id)

    def update_mark_price(self, symbol: str, price: float):
        contract_symbol = normalize_contract_symbol(symbol)
        px = _float_value(price, 0.0)
        if contract_symbol and px > 0:
            self._last_prices[contract_symbol] = px
        return []

    def min_contract_notional(self, symbol: str, price: float) -> float:
        inst = self._instrument(symbol)
        px = _float_value(price, 0.0)
        if px <= 0:
            px = _float_value(self._last_prices.get(inst.symbol), 0.0)
        if px <= 0:
            return 0.0
        return max(inst.min_sz, inst.lot_sz) * inst.ct_val * px

    async def get_available_balance(self, currency: str = "USDT") -> float:
        return await self._spot_broker.get_available_balance(currency)

    async def buy(self, symbol: str, amount: float, price: Optional[float] = None, *, order_type: str = "market") -> OrderResult:
        self._sync_spot_warmup_state()
        return await self._spot_broker.buy(symbol, amount, price, order_type=order_type)

    async def sell(self, symbol: str, amount: float, price: Optional[float] = None, *, order_type: str = "market") -> OrderResult:
        self._sync_spot_warmup_state()
        return await self._spot_broker.sell(symbol, amount, price, order_type=order_type)

    async def close_position(self, symbol: str) -> OrderResult:
        details: List[Dict[str, Any]] = []
        for side in ("long", "short"):
            result = await self.close_contract(symbol, side, ratio=1.0)
            details.append(dict(result))
        return OrderResult({"closed": sum(1 for item in details if item.get("status") == "filled"), "details": details})

    async def open_contract(
        self,
        symbol: str,
        side: str,
        notional_usdt: float,
        leverage: Optional[float] = None,
        price: Optional[float] = None,
    ) -> OrderResult:
        if self._orders_blocked():
            return self._blocked_result()
        pos_side = self._normalize_side(side)
        try:
            inst = self._instrument(symbol)
            fill_price = await self._resolve_price(inst.symbol, price)
            lev = self._resolve_leverage(inst, leverage)
            contracts = self._notional_to_contracts(inst, fill_price, float(notional_usdt), op_type="open")
            if contracts < inst.min_sz:
                return OrderResult(
                    {
                        "status": "rejected",
                        "reason": f"order size below OKX minSz: {contracts:g} < {inst.min_sz:g}",
                        "symbol": inst.symbol,
                        "pos_side": pos_side,
                    }
                )
            actual_notional = contracts * inst.ct_val * fill_price
            margin = actual_notional / max(lev, 1.0)
            free_usdt = await self.get_available_balance("USDT")
            if free_usdt > 0 and margin * 1.05 > free_usdt:
                return OrderResult(
                    {
                        "status": "rejected",
                        "reason": f"insufficient live margin: need≈{margin * 1.05:.2f} USDT, free={free_usdt:.2f} USDT",
                        "symbol": inst.symbol,
                        "pos_side": pos_side,
                    }
                )
            position_mode = await self._position_mode()
            await self._ensure_leverage(inst, lev, pos_side=pos_side, position_mode=position_mode)
            order_side = "buy" if pos_side == "long" else "sell"
            result = await self._place_contract_order(
                inst=inst,
                action="open",
                pos_side=pos_side,
                order_side=order_side,
                contracts=contracts,
                price=price,
                leverage=lev,
                position_mode=position_mode,
            )
            result.update(
                {
                    "notional_usdt": actual_notional,
                    "margin": margin,
                    "base_qty": contracts * inst.ct_val,
                    "price": _float_value(result.get("price") or result.get("average"), fill_price),
                }
            )
            self._persist_contract_trade(result)
            return OrderResult(result)
        except Exception as exc:
            logger.error("[LiveContractBroker] open_contract failed: %s", exc)
            return OrderResult({"status": "rejected", "reason": str(exc), "symbol": symbol, "pos_side": pos_side})

    async def close_contract(
        self,
        symbol: str,
        side: str,
        ratio: float = 1.0,
        contracts: Optional[float] = None,
        price: Optional[float] = None,
    ) -> OrderResult:
        if self._orders_blocked():
            return self._blocked_result()
        pos_side = self._normalize_side(side)
        try:
            inst = self._instrument(symbol)
            position = await self.get_contract_position(inst.symbol, pos_side)
            if not position:
                return OrderResult({"status": "skipped", "reason": "no_position", "symbol": inst.symbol, "pos_side": pos_side})
            held = _float_value(
                position.get("contracts")
                or position.get("size")
                or position.get("amount")
                or position.get("pos"),
                0.0,
            )
            requested = float(contracts) if contracts is not None else held * max(0.0, min(float(ratio), 1.0))
            close_contracts = min(held, self._round_to_lot(inst, requested, op_type="close"))
            if close_contracts <= 0:
                return OrderResult({"status": "skipped", "reason": "contracts_zero", "symbol": inst.symbol, "pos_side": pos_side})
            fill_price = await self._resolve_price(inst.symbol, price or position.get("mark_price") or position.get("entry_price"))
            lev = _float_value(position.get("leverage"), self._max_leverage)
            position_mode = await self._position_mode()
            order_side = "sell" if pos_side == "long" else "buy"
            td_mode = self._normalize_td_mode(position.get("td_mode"), default=self._td_mode)
            result = await self._place_contract_order(
                inst=inst,
                action="close",
                pos_side=pos_side,
                order_side=order_side,
                contracts=close_contracts,
                price=price,
                leverage=lev,
                position_mode=position_mode,
                td_mode=td_mode,
            )
            result.update(
                {
                    "notional_usdt": close_contracts * inst.ct_val * fill_price,
                    "margin": 0.0,
                    "base_qty": close_contracts * inst.ct_val,
                    "price": _float_value(result.get("price") or result.get("average"), fill_price),
                    "realized_pnl": _float_value(result.get("realized_pnl"), 0.0),
                }
            )
            self._persist_contract_trade(result)
            return OrderResult(result)
        except Exception as exc:
            logger.error("[LiveContractBroker] close_contract failed: %s", exc)
            return OrderResult({"status": "rejected", "reason": str(exc), "symbol": symbol, "pos_side": pos_side})

    async def get_contract_position(self, symbol: str, side: str) -> Optional[Dict[str, Any]]:
        pos_side = self._normalize_side(side)
        inst = self._instrument(symbol)
        exchange = self._exchange()
        rows = await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: exchange.fetch_positions([inst.symbol]),
        )
        for row in rows or []:
            normalized = normalize_contract_symbol(str(row.get("symbol") or inst.symbol))
            if normalized != inst.symbol:
                continue
            parsed_side = self._position_side_from_row(row)
            if parsed_side != pos_side:
                continue
            contracts = abs(self._position_contracts_from_row(row))
            if contracts <= 1e-12:
                continue
            entry = _float_value(row.get("entry_price") or row.get("entryPrice") or row.get("avgPx"), 0.0)
            mark = _float_value(row.get("mark_price") or row.get("markPrice") or row.get("last") or entry, entry)
            td_mode = self._position_td_mode_from_row(row)
            return {
                "symbol": inst.symbol,
                "inst_id": inst.inst_id,
                "side": pos_side,
                "pos_side": pos_side,
                "contracts": contracts,
                "size": contracts,
                "base_qty": contracts * inst.ct_val,
                "entry_price": entry,
                "mark_price": mark,
                "notional_usdt": contracts * inst.ct_val * mark,
                "leverage": _float_value(row.get("leverage"), self._max_leverage),
                "td_mode": td_mode,
                "unrealized_pnl": _float_value(row.get("unrealized_pnl") or row.get("unrealizedPnl") or row.get("upl"), 0.0),
                "raw": row,
            }
        return None

    def _sync_spot_warmup_state(self) -> None:
        self._spot_broker.warmup_mode = self.warmup_mode
        self._spot_broker.orders_deadline_monotonic = self.orders_deadline_monotonic

    def _orders_blocked(self) -> bool:
        return bool(self.orders_deadline_monotonic and time.monotonic() < self.orders_deadline_monotonic) or self.warmup_mode

    def _blocked_result(self) -> OrderResult:
        reason = "warmup_mode" if self.warmup_mode else "warmup_order_delay"
        return OrderResult({"status": "skipped", "reason": reason})

    def _exchange(self):
        exchange = exchange_manager.get_exchange(self._exchange_name)
        if not exchange:
            raise ValueError(f"Exchange {self._exchange_name} not available")
        return exchange

    def _native_exchange(self):
        exchange = self._exchange()
        native = getattr(exchange, "exchange", None)
        if native is None:
            raise ValueError(f"Exchange {self._exchange_name} native client not available")
        return native

    def _instrument(self, symbol: str) -> ContractInstrument:
        normalized = normalize_contract_symbol(symbol)
        inst = self.instruments.get(normalized)
        if not inst:
            raise ValueError(f"missing OKX SWAP instrument metadata for {normalized}")
        if str(inst.state).lower() != "live":
            raise ValueError(f"OKX instrument is not live: {inst.inst_id} state={inst.state}")
        for name, value in (("ctVal", inst.ct_val), ("lotSz", inst.lot_sz), ("minSz", inst.min_sz)):
            if value <= 0:
                raise ValueError(f"invalid OKX instrument metadata {name}: {inst.inst_id}")
        return inst

    async def _resolve_price(self, symbol: str, explicit: Optional[Any]) -> float:
        px = _float_value(explicit, 0.0)
        if px <= 0:
            px = _float_value(self._last_prices.get(normalize_contract_symbol(symbol)), 0.0)
        if px <= 0:
            exchange = self._exchange()
            ticker = await asyncio.get_running_loop().run_in_executor(None, lambda: exchange.fetch_ticker(symbol))
            px = _float_value((ticker or {}).get("last"), 0.0)
        if px <= 0:
            raise ValueError(f"no live mark price available for {symbol}")
        self._last_prices[normalize_contract_symbol(symbol)] = px
        return px

    def _resolve_leverage(self, inst: ContractInstrument, leverage: Optional[float]) -> float:
        requested = _float_value(leverage, _float_value(self._config.get("leverage"), self._max_leverage))
        max_allowed = min(self._max_leverage, inst.max_leverage or self._max_leverage)
        if requested <= 0:
            raise ValueError("leverage must be positive")
        if requested > max_allowed + 1e-12:
            raise ValueError(f"requested leverage exceeds max leverage {max_allowed:g}")
        return requested

    def _notional_to_contracts(self, inst: ContractInstrument, price: float, notional: float, *, op_type: str) -> float:
        if price <= 0:
            raise ValueError("price must be positive")
        if notional <= 0:
            raise ValueError("notional_usdt must be positive")
        raw = float(notional) / (price * inst.ct_val)
        return self._round_to_lot(inst, raw, op_type=op_type)

    def _round_to_lot(self, inst: ContractInstrument, contracts: float, *, op_type: str) -> float:
        lot = inst.lot_sz or 1.0
        if op_type == "close":
            rounded = round(float(contracts) / lot) * lot
        else:
            rounded = math.floor((float(contracts) / lot) + 1e-12) * lot
        return round(max(0.0, rounded), 12)

    async def _position_mode(self) -> str:
        if self._position_mode_cache:
            return self._position_mode_cache
        # `position_mode` belongs to paper strategy configuration and may not
        # match the real OKX account. Live execution must read the account mode
        # unless an operator explicitly provides a live-only override.
        override = str(self._config.get("live_position_mode") or "").strip().lower()
        if override:
            mode = self._POSITION_MODE_ALIASES.get(override)
            if mode:
                self._position_mode_cache = mode
                return mode
        native = self._native_exchange()

        def read_config():
            if not hasattr(native, "privateGetAccountConfig"):
                raise ValueError("OKX account config endpoint unavailable")
            return native.privateGetAccountConfig({})

        response = await asyncio.get_running_loop().run_in_executor(None, read_config)
        data = response.get("data") if isinstance(response, dict) else None
        first = data[0] if isinstance(data, list) and data else {}
        raw_mode = str(first.get("posMode") or first.get("positionMode") or "").strip().lower()
        mode = self._POSITION_MODE_ALIASES.get(raw_mode)
        if not mode:
            raise ValueError(f"无法识别 OKX 持仓模式: {raw_mode or response}")
        self._position_mode_cache = mode
        return mode

    async def _ensure_leverage(
        self,
        inst: ContractInstrument,
        leverage: float,
        *,
        pos_side: str,
        position_mode: str,
    ) -> None:
        lev = round(float(leverage), 8)
        cache_key = (inst.inst_id or inst.symbol, self._td_mode, pos_side if position_mode == "long_short_mode" else "net", lev)
        if cache_key in self._leverage_set_cache:
            return
        native = self._native_exchange()
        payload = {
            "instId": inst.inst_id,
            "lever": str(lev).rstrip("0").rstrip("."),
            "mgnMode": self._td_mode,
        }
        if position_mode == "long_short_mode":
            payload["posSide"] = pos_side

        def set_leverage():
            if hasattr(native, "privatePostAccountSetLeverage"):
                return native.privatePostAccountSetLeverage(payload)
            if hasattr(native, "set_leverage"):
                params = {"mgnMode": self._td_mode}
                if position_mode == "long_short_mode":
                    params["posSide"] = pos_side
                return native.set_leverage(lev, inst.symbol, params)
            raise ValueError("OKX set leverage endpoint unavailable")

        response = await asyncio.get_running_loop().run_in_executor(None, set_leverage)
        if isinstance(response, dict):
            code = str(response.get("code") or "0")
            data = response.get("data") if isinstance(response.get("data"), list) else []
            sub_code = str((data[0] or {}).get("sCode") or "0") if data else "0"
            if code not in {"", "0"} or sub_code not in {"", "0"}:
                raise ValueError(f"OKX 设置杠杆失败: {response}")
        self._leverage_set_cache.add(cache_key)

    async def _place_contract_order(
        self,
        *,
        inst: ContractInstrument,
        action: str,
        pos_side: str,
        order_side: str,
        contracts: float,
        price: Optional[float],
        leverage: float,
        position_mode: str,
        td_mode: Optional[str] = None,
    ) -> Dict[str, Any]:
        native = self._native_exchange()
        order_type = str(self._config.get("live_order_type") or "market").lower()
        if order_type not in {"market", "limit"}:
            order_type = "market"
        order_price = float(price) if order_type == "limit" and price is not None else None
        client_order_id = self._client_order_id()
        effective_td_mode = self._normalize_td_mode(td_mode, default=self._td_mode)
        params: Dict[str, Any] = {
            "tdMode": effective_td_mode,
            "clOrdId": client_order_id,
            "tag": "quantbase",
        }
        if position_mode == "long_short_mode":
            params["posSide"] = pos_side
        else:
            if action == "close":
                params["reduceOnly"] = True

        def create_order():
            return native.create_order(
                inst.symbol,
                order_type,
                order_side,
                contracts,
                order_price,
                params,
            )

        raw = await asyncio.get_running_loop().run_in_executor(None, create_order)
        info = raw.get("info") if isinstance(raw, dict) else {}
        if isinstance(info, dict):
            data = info.get("data") if isinstance(info.get("data"), list) else []
            first = data[0] if data else {}
            sub_code = str(first.get("sCode") or "0")
            if sub_code not in {"", "0"}:
                raise ValueError(f"OKX 下单失败: {first.get('sMsg') or info}")

        order_id = ""
        if isinstance(raw, dict):
            order_id = str(raw.get("id") or "")
            if not order_id and isinstance(info, dict):
                data = info.get("data") if isinstance(info.get("data"), list) else []
                if data:
                    order_id = str(data[0].get("ordId") or "")
        raw_status = str((raw or {}).get("status") or "").lower()
        status = "filled" if order_type == "market" and raw_status in {"", "open", "closed"} else (raw_status or "submitted")
        return {
            "status": status,
            "action": action,
            "symbol": inst.symbol,
            "inst_id": inst.inst_id,
            "pos_side": pos_side,
            "contracts": contracts,
            "leverage": leverage,
            "order_id": order_id,
            "client_order_id": params["clOrdId"],
            "order_side": order_side,
            "order_type": order_type,
            "position_mode": position_mode,
            "td_mode": effective_td_mode,
            "fee": self._fee_from_order(raw),
            "raw_order": raw,
        }

    def _client_order_id(self) -> str:
        configured = str(
            self._config.get("live_client_order_id")
            or self._config.get("client_order_id")
            or ""
        ).strip()
        if configured:
            cleaned = "".join(ch for ch in configured if ch.isalnum())
            if cleaned:
                return cleaned[:32]
        return self._next_client_order_id()

    def _next_client_order_id(self) -> str:
        self._order_seq += 1
        ts = int(time.time() * 1000) % 1_000_000_000_000
        return f"bp{self._strategy_id}{ts}{self._order_seq % 10000}"

    def _fee_from_order(self, raw: Any) -> float:
        if not isinstance(raw, dict):
            return 0.0
        fee = raw.get("fee")
        if isinstance(fee, dict):
            return _float_value(fee.get("cost"), 0.0)
        fees = raw.get("fees")
        if isinstance(fees, list):
            return sum(_float_value(item.get("cost"), 0.0) for item in fees if isinstance(item, dict))
        return 0.0

    def _position_side_from_row(self, row: Dict[str, Any]) -> str:
        info = row.get("info") if isinstance(row.get("info"), dict) else {}
        raw_pos_side = str(row.get("pos_side") or row.get("posSide") or info.get("posSide") or "").lower()
        if raw_pos_side in {"long", "short"}:
            return raw_pos_side
        side = str(row.get("side") or info.get("side") or "").lower()
        if side in {"long", "short"}:
            return side
        if side == "buy":
            return "long"
        if side == "sell":
            return "short"
        signed = self._position_contracts_from_row(row, signed=True)
        return "short" if signed < 0 else "long"

    def _position_contracts_from_row(self, row: Dict[str, Any], *, signed: bool = False) -> float:
        info = row.get("info") if isinstance(row.get("info"), dict) else {}
        for source in (row, info):
            for key in ("contracts", "amount", "size", "pos"):
                if key not in source:
                    continue
                value = _float_value(source.get(key), 0.0)
                if value:
                    return value if signed else abs(value)
        return 0.0

    @classmethod
    def _normalize_td_mode(cls, value: Any, *, default: str = "") -> str:
        text = str(value or "").strip().lower().replace("-", "_")
        if text in {"cross", "cross_margin"}:
            return "cross"
        if text in {"isolated", "isolated_margin"}:
            return "isolated"
        return default

    def _position_td_mode_from_row(self, row: Dict[str, Any]) -> str:
        info = row.get("info") if isinstance(row.get("info"), dict) else {}
        for source in (row, info):
            for key in ("td_mode", "tdMode", "mgn_mode", "mgnMode", "margin_mode", "marginMode"):
                mode = self._normalize_td_mode(source.get(key), default="")
                if mode:
                    return mode
        return ""

    @staticmethod
    def _normalize_side(side: str) -> str:
        normalized = str(side or "").strip().lower()
        if normalized not in {"long", "short"}:
            raise ValueError("contract side must be long or short")
        return normalized

    def _persist_contract_trade(self, result: Dict[str, Any]):
        if self._strategy_id <= 0:
            return
        try:
            action = str(result.get("action") or "open")
            pos_side = str(result.get("pos_side") or "")
            leverage_value = _float_value(result.get("leverage"), 0.0) or None
            db.insert_strategy_trade(
                self._strategy_id,
                {
                    "exchange": self._exchange_name,
                    "symbol": normalize_contract_symbol(str(result.get("symbol") or "")),
                    "order_id": result.get("order_id") or result.get("id") or "",
                    "timestamp": int(datetime.now().timestamp() * 1000),
                    "side": f"{action}_{pos_side}".strip("_"),
                    "type": str(result.get("order_type") or "market"),
                    "price": _float_value(result.get("price") or result.get("average"), 0.0),
                    "quantity": _float_value(result.get("contracts"), 0.0),
                    "fee": _float_value(result.get("fee"), 0.0),
                    "fee_asset": "USDT",
                    "pnl": _float_value(result.get("realized_pnl"), 0.0),
                    "meta": {
                        "market_type": "swap",
                        "live": True,
                        "action": action,
                        "pos_side": pos_side,
                        "contracts": _float_value(result.get("contracts"), 0.0),
                        "base_qty": _float_value(result.get("base_qty"), 0.0),
                        "notional_usdt": _float_value(result.get("notional_usdt"), 0.0),
                        "margin": _float_value(result.get("margin"), 0.0),
                        "leverage": leverage_value,
                        "client_order_id": result.get("client_order_id"),
                        "position_mode": result.get("position_mode"),
                    },
                },
            )
        except Exception as e:
            logger.warning("[LiveContractBroker] 持久化实盘合约交易记录失败: %s", e)


# ============================================================
# 策略动态加载
# ============================================================


def load_strategy_by_module(module_path: str, class_name: str) -> type:
    """
    按「Python 模块路径 + 类名」加载策略类。

    示例:
        cls = load_strategy_by_module("app.strategies.kairos_30m_horizon_dca_strategy", "Kairos30mHorizonDcaStrategy")
    """
    mod = importlib.import_module(module_path)
    cls = getattr(mod, class_name, None)
    if cls is None:
        raise AttributeError(f"模块 {module_path} 中未找到类 {class_name}")
    if not (inspect.isclass(cls) and issubclass(cls, BaseStrategy)):
        raise TypeError(f"{class_name} 不是 BaseStrategy 的子类")
    return cls


def load_strategy_from_script(script_content: str, strategy_name: str) -> type:
    """从源码字符串动态加载策略类（兼容 DB 存储的脚本）。"""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", prefix=f"strat_{strategy_name}_", delete=False, dir=tempfile.gettempdir(),
    ) as f:
        f.write(script_content)
        tmp_path = f.name

    module_name = f"_dyn_strat_{strategy_name}_{id(script_content)}"
    try:
        spec = importlib.util.spec_from_file_location(module_name, tmp_path)
        if spec is None or spec.loader is None:
            raise ImportError(f"Cannot create module spec from {tmp_path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)

        candidates = [
            cls for _, cls in inspect.getmembers(module, inspect.isclass)
            if issubclass(cls, BaseStrategy) and cls is not BaseStrategy
        ]
        if not candidates:
            raise TypeError("脚本中未找到继承自 BaseStrategy 的策略类")
        return candidates[0]
    finally:
        try:
            Path(tmp_path).unlink(missing_ok=True)
        except Exception:
            pass
        sys.modules.pop(module_name, None)


# ============================================================
# 策略引擎
# ============================================================


class StrategyEngine:
    """策略执行引擎（v2 — BaseStrategy + Broker 协议 + K线事件驱动）。"""

    def __init__(self):
        self._contexts: Dict[int, StrategyContext] = {}
        self._tasks: Dict[int, asyncio.Task] = {}
        self._strategy_instances: Dict[int, BaseStrategy] = {}
        self._running = False
        self._lock = asyncio.Lock()
        self._kill_switch_lock = asyncio.Lock()
        self._risk_manager = RiskManager(RiskConfig(max_total_drawdown_pct=0.10))
        # CCXT 的 enableRateLimit 在多线程并发下容易失效；策略引擎侧做串行与缓存，
        # 避免多策略/多 symbol 同时拉取导致 OKX 50011 Too Many Requests。
        self._ohlcv_fetch_lock = asyncio.Lock()
        # OKX 对 candles 接口有较严格的限速；即使完全串行化，也可能在同一进程内
        # 因“每分钟要拉取的 symbol 数过多”而在短窗口内触发 50011。
        # 这里再加一层全局节流：每次真实 fetch_ohlcv 之间至少间隔 N 秒。
        #
        # 说明：
        # - 该节流仅影响策略引擎的 OHLCV 拉取路径（warmup + latest bar）
        # - N 可通过环境变量调整，默认较保守，优先保证币池分钟对齐形成
        self._ohlcv_min_interval_sec = float(os.environ.get("QUANTBASE_OHLCV_MIN_INTERVAL_SEC", "0.20"))
        self._ohlcv_next_allowed_monotonic = 0.0
        self._ohlcv_penalty_until_monotonic = 0.0
        self._latest_bar_cache: Dict[tuple[str, str, str], tuple[float, Optional[BarData]]] = {}
        self._latest_bar_inflight: Dict[tuple[str, str, str], asyncio.Future] = {}
        self._tick_fetch_lock = asyncio.Lock()
        self._latest_tick_cache: Dict[tuple[str, str, int], tuple[float, Optional[TickData]]] = {}
        self._latest_tick_inflight: Dict[tuple[str, str, int], asyncio.Future] = {}
        self._warmup_ohlcv_cache: Dict[tuple[str, str, str, int], tuple[float, List[list]]] = {}
        self._warmup_ohlcv_inflight: Dict[tuple[str, str, str, int], asyncio.Future] = {}
        self._runtime_state_cache: Dict[int, str] = {}

    @staticmethod
    def _position_display_size(position: Dict[str, Any]) -> float:
        """返回用于监控展示/刷新判断的仓位规模。"""
        candidates: List[float] = []
        for key in ("contracts", "size", "base_qty", "amount"):
            try:
                candidates.append(abs(float(position.get(key) or 0.0)))
            except (TypeError, ValueError):
                continue
        return max(candidates) if candidates else 0.0

    @classmethod
    def _is_display_position(cls, position: Dict[str, Any]) -> bool:
        return cls._position_display_size(position) > _POSITION_DISPLAY_EPSILON

    @staticmethod
    def _ticker_price(ticker: Any) -> float:
        if not isinstance(ticker, dict):
            return 0.0
        for key in ("last", "mark", "markPrice", "close"):
            try:
                px = float(ticker.get(key) or 0.0)
            except (TypeError, ValueError):
                px = 0.0
            if px > 0:
                return px
        return 0.0

    @staticmethod
    def _normalize_trade_side(side: Any) -> str:
        return str(side or "").strip().lower().replace("-", "_").replace(" ", "_")

    @staticmethod
    def _runtime_state_setting_key(strategy_id: int) -> str:
        return f"{_RUNTIME_STATE_SETTING_PREFIX}{int(strategy_id)}"

    @staticmethod
    def _extract_persistable_runtime_state(state: StrategyState) -> Dict[str, Any]:
        positions = getattr(state, "positions", {}) or {}
        if not isinstance(positions, dict):
            return {}
        snapshot: Dict[str, Any] = {}
        for key in _PERSISTED_RUNTIME_POSITION_KEYS:
            value = positions.get(key)
            if isinstance(value, dict):
                snapshot[key] = value
        return snapshot

    def _load_strategy_runtime_state(self, strategy_id: int, state: StrategyState) -> None:
        try:
            raw = db.get_app_setting(self._runtime_state_setting_key(strategy_id), "{}")
            payload = json.loads(raw or "{}")
        except Exception as exc:
            logger.warning("Load strategy runtime state failed for %s: %s", strategy_id, exc)
            return
        self._runtime_state_cache[int(strategy_id)] = json.dumps(
            payload if isinstance(payload, dict) else {},
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        if not isinstance(payload, dict):
            return
        for key in _PERSISTED_RUNTIME_POSITION_KEYS:
            value = payload.get(key)
            if isinstance(value, dict):
                state.positions[key] = value

    def _persist_strategy_runtime_state(self, strategy_id: int, state: StrategyState) -> None:
        snapshot = self._extract_persistable_runtime_state(state)
        payload = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        if self._runtime_state_cache.get(int(strategy_id)) == payload:
            return
        try:
            db.set_app_setting(self._runtime_state_setting_key(strategy_id), payload)
            self._runtime_state_cache[int(strategy_id)] = payload
        except Exception as exc:
            logger.warning("Persist strategy runtime state failed for %s: %s", strategy_id, exc)

    def _record_equity_sample(
        self,
        context: StrategyContext,
        broker: Any,
        *,
        source: str = "runtime",
    ) -> None:
        """Persist paper account equity independently from dashboard page reads."""
        if not bool(context.config.get("is_paper_trading", True)):
            return
        if not hasattr(db, "insert_strategy_equity_sample"):
            return
        try:
            equity = float(getattr(broker, "equity"))
            initial = float(getattr(broker, "initial_capital", 0.0) or 0.0)
        except (TypeError, ValueError):
            return
        if equity <= 0:
            return
        total_pnl = equity - initial
        try:
            balance = float(getattr(broker, "balance", 0.0) or 0.0)
        except (TypeError, ValueError):
            balance = None
        return_pct = ((equity - initial) / initial * 100) if initial > 0 else None
        trade_metrics = self._strategy_trade_metrics(
            context.strategy_id,
            started_at=None,
            fallback_trades=list(getattr(broker, "trades", []) or []),
        )
        try:
            db.insert_strategy_equity_sample(
                context.strategy_id,
                int(datetime.now(timezone.utc).timestamp() * 1000),
                equity,
                balance=balance,
                total_pnl=round(total_pnl, 6),
                return_pct=round(return_pct, 6) if return_pct is not None else None,
                win_rate=trade_metrics.get("win_rate"),
                profit_factor=trade_metrics.get("profit_factor"),
                source=source,
            )
        except Exception as exc:
            logger.debug("Persist strategy equity sample failed for %s: %s", context.strategy_id, exc)

    def _strategy_trade_metrics(
        self,
        strategy_id: int,
        *,
        started_at: Optional[datetime],
        fallback_trades: List[Dict[str, Any]],
    ) -> Dict[str, float | int]:
        rows: List[Dict[str, Any]] = []
        if started_at:
            try:
                since_ms = int(started_at.timestamp() * 1000)
                rows = db.get_strategy_trades_since(strategy_id, since_ms)
            except Exception:
                rows = []
        if not rows:
            rows = list(fallback_trades or [])

        closing_trades = 0
        winning_trades = 0
        gross_profit = 0.0
        gross_loss = 0.0
        for row in rows:
            side = self._normalize_trade_side(row.get("side"))
            if side not in _EXIT_TRADE_SIDES:
                continue
            closing_trades += 1
            try:
                pnl = float(row.get("pnl") or 0.0)
            except (TypeError, ValueError):
                pnl = 0.0
            if pnl > 0:
                winning_trades += 1
                gross_profit += pnl
            elif pnl < 0:
                gross_loss += abs(pnl)

        win_rate = (winning_trades / closing_trades * 100) if closing_trades > 0 else 0.0
        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else 0.0
        return {
            "total_trades": len(rows),
            "closing_trades": closing_trades,
            "winning_trades": winning_trades,
            "win_rate": round(win_rate, 4),
            "gross_profit": round(gross_profit, 6),
            "gross_loss": round(gross_loss, 6),
            "profit_factor": round(profit_factor, 4),
        }

    def _build_broker_for_context(self, context: StrategyContext):
        is_paper = context.config.get("is_paper_trading", True)
        market_type = str(context.config.get("market_type") or "spot").lower()
        if market_type == "cross_exchange_swap":
            if not is_paper:
                raise ValueError("cross_exchange_swap is paper-only in this contract")
            return CrossExchangePaperBroker(
                initial_capital=context.config.get("initial_capital", 10000.0),
                strategy_id=context.strategy_id,
                config=context.config,
            )
        if market_type == "swap":
            broker_symbols_raw = (
                context.config.get("contract_trade_symbols")
                or context.config.get("trade_symbols")
                or context.symbols
            )
            if isinstance(broker_symbols_raw, str):
                broker_symbols = [part.strip() for part in broker_symbols_raw.split(",") if part.strip()]
            elif isinstance(broker_symbols_raw, (list, tuple, set)):
                broker_symbols = [str(part).strip() for part in broker_symbols_raw if str(part).strip()]
            else:
                broker_symbols = list(context.symbols)
            broker_symbols = _normalize_contract_symbol_list(broker_symbols) or list(context.symbols)
            if not is_paper:
                return LiveContractBroker(
                    strategy_id=context.strategy_id,
                    exchange_name=context.exchange,
                    symbols=broker_symbols or context.symbols,
                    config=context.config,
                )
            broker = ContractPaperBroker(
                initial_capital=context.config.get("initial_capital", 10000.0),
                strategy_id=context.strategy_id,
                exchange_name=context.exchange,
                symbols=broker_symbols or context.symbols,
                config=context.config,
            )
            if context.started_at:
                since_ms = int(context.started_at.timestamp() * 1000)
                broker.restore_from_trades(db.get_strategy_trades_since(context.strategy_id, since_ms))
            return broker

        if is_paper:
            broker = PaperBroker(
                initial_capital=context.config.get("initial_capital", 10000.0),
                commission_rate=context.config.get("commission_rate", 0.001),
                slippage_rate=_slippage_rate_from_config(context.config),
                strategy_id=context.strategy_id,
                exchange_name=context.exchange,
            )
            if context.started_at:
                since_ms = int(context.started_at.timestamp() * 1000)
                broker.restore_from_trades(db.get_strategy_trades_since(context.strategy_id, since_ms))
            return broker
        return LiveBroker(context.exchange, context.strategy_id)

    async def _throttle_ohlcv_fetch(self) -> None:
        """全局串行 + 最小间隔节流（需在 _ohlcv_fetch_lock 内调用）。"""
        now = time.monotonic()
        sleep_until = max(self._ohlcv_next_allowed_monotonic, self._ohlcv_penalty_until_monotonic)
        if now < sleep_until:
            await asyncio.sleep(sleep_until - now)
        # 下一次最早允许时间（用“当前时间”重算，避免 sleep 被取消/打断时漂移）
        self._ohlcv_next_allowed_monotonic = time.monotonic() + max(self._ohlcv_min_interval_sec, 0.0)

    def _mark_ohlcv_rate_limited(self, err: Exception) -> None:
        """遇到 OKX 50011 时做短暂冷却，降低连续触发概率。"""
        s = str(err)
        if "50011" in s or "Too Many Requests" in s:
            self._ohlcv_penalty_until_monotonic = max(
                self._ohlcv_penalty_until_monotonic,
                time.monotonic() + 2.0,
            )

    # -------------------------------------------------------
    # 引擎生命周期
    # -------------------------------------------------------

    async def start(self):
        if self._running:
            return
        self._running = True
        logger.info("Strategy engine v2 started")
        await self._restore_running_strategies()

    async def stop(self, *, persist_running_in_db: bool = True):
        """
        停止引擎并取消所有策略异步任务。

        persist_running_in_db=True（默认）: 不写库为 stopped，保留 DB 中 ``running``，
        也不执行策略 ``on_stop`` 钩子，便于进程重启后 ``_restore_running_strategies``
        自动拉起（正常关机/部署重启场景）。

        persist_running_in_db=False: 与原先行为一致，对每个策略写库 stopped 并发停机通知。
        """
        self._running = False
        for sid in list(self._tasks.keys()):
            if persist_running_in_db:
                await self._cancel_strategy_task_preserve_db(sid)
            else:
                await self.stop_strategy(sid)
        logger.info(
            "Strategy engine v2 stopped (persist_running_in_db=%s)",
            persist_running_in_db,
        )

    async def _cancel_strategy_task_preserve_db(self, strategy_id: int) -> None:
        """
        仅取消进程内任务，不写 DB，也不调用策略 on_stop。

        ``on_stop`` 是用户显式停止策略时的业务钩子，部分策略会在其中平仓或清理批次。
        部署、systemd restart、机器重启只能视为运行进程挂起；数据库中的 running
        状态保留，下一次应用启动会自动恢复。
        """
        async with self._lock:
            if strategy_id in self._tasks:
                task = self._tasks.pop(strategy_id)
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                except Exception as e:
                    logger.warning(
                        "Shutdown cancel strategy %d: task awaited with error: %s",
                        strategy_id,
                        e,
                    )
            self._strategy_instances.pop(strategy_id, None)
            self._contexts.pop(strategy_id, None)

    async def _restore_running_strategies(self):
        try:
            conn = db.get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT id, name FROM strategies WHERE status = 'running'")
            rows = cursor.fetchall()
            conn.close()
            if not rows:
                logger.info("没有需要恢复的策略")
                return
            logger.info("发现 %d 个需要恢复的策略", len(rows))
            for row in rows:
                sid, name = row["id"], row["name"]
                try:
                    if await self.start_strategy(sid):
                        logger.info("策略恢复成功: #%d %s", sid, name)
                        continue
                    row_after = db.get_strategy_by_id(sid)
                    cur_status = (row_after or {}).get("status")
                    # 熔断拒绝启动时 start_strategy 会把库改为 paused，不得再写成 stopped
                    if cur_status == StrategyStatus.PAUSED.value:
                        logger.warning("策略恢复被跳过(熔断/暂停态): #%d %s", sid, name)
                        continue
                    # start_strategy 未改写库仍可能为 running（极少见）；避免误标 stopped，下次启动会再试
                    if cur_status == StrategyStatus.RUNNING.value:
                        logger.warning(
                            "策略恢复未拉起但库仍为 running，保留状态以便下次重试: #%d %s",
                            sid,
                            name,
                        )
                        continue
                    logger.warning("策略恢复失败: #%d %s (status=%s)", sid, name, cur_status)
                    if cur_status not in (StrategyStatus.ERROR.value, StrategyStatus.STOPPED.value):
                        db.update_strategy_status(sid, "stopped")
                except Exception as e:
                    logger.error("策略恢复异常: #%d %s: %s", sid, name, e)
                    row_after = db.get_strategy_by_id(sid)
                    cur_status = (row_after or {}).get("status")
                    if cur_status == StrategyStatus.PAUSED.value:
                        continue
                    if cur_status == StrategyStatus.RUNNING.value:
                        logger.warning(
                            "策略恢复异常但库仍为 running，保留以便下次重试: #%d %s",
                            sid,
                            name,
                        )
                        continue
                    if cur_status not in (StrategyStatus.ERROR.value, StrategyStatus.STOPPED.value):
                        db.update_strategy_status(sid, "stopped")
        except Exception as e:
            logger.error("恢复运行中策略失败: %s", e)

    # -------------------------------------------------------
    # 策略管理（外部 API）
    # -------------------------------------------------------

    async def load_strategy(self, strategy_id: int) -> Optional[StrategyContext]:
        strategy = db.get_strategy_by_id(strategy_id)
        if not strategy:
            return None
        config = dict(strategy.get("config") or {})
        symbols = strategy.get("symbols", ["BTC/USDT"])
        if _is_ai_autonomous_config(config):
            runtime_symbols = (
                _normalize_contract_symbol_list(config.get("contract_trade_symbols"))
                or _normalize_contract_symbol_list(config.get("trade_symbols"))
                or _normalize_contract_symbol_list(config.get("symbols"))
                or _normalize_contract_symbol_list(symbols)
            )
            if runtime_symbols:
                symbols = runtime_symbols
                config["symbols"] = runtime_symbols
                config["trade_symbols"] = runtime_symbols
                config["contract_trade_symbols"] = runtime_symbols
        context = StrategyContext(
            strategy_id=strategy_id,
            name=strategy["name"],
            exchange=strategy.get("exchange", "okx"),
            symbols=symbols,
            config=config,
        )
        async with self._lock:
            self._contexts[strategy_id] = context
        return context

    async def start_strategy(self, strategy_id: int) -> bool:
        if self._risk_manager.is_circuit_breaker_active():
            logger.warning("拒绝启动策略 %d: 全局熔断中", strategy_id)
            db.update_strategy_status(strategy_id, StrategyStatus.PAUSED.value)
            return False

        context = self._contexts.get(strategy_id) or await self.load_strategy(strategy_id)
        if not context:
            logger.error("Strategy %d not found", strategy_id)
            return False
        if context.status == StrategyStatus.RUNNING:
            return True

        strategy_row = db.get_strategy_by_id(strategy_id)
        if not strategy_row:
            return False

        persisted = _parse_run_started_at_from_db(strategy_row.get("run_started_at"))
        if persisted:
            context_started = persisted
        else:
            context_started = datetime.now(timezone.utc)
            db.set_strategy_run_started_at(strategy_id, context_started.isoformat())

        try:
            async with self._lock:
                context.status = StrategyStatus.RUNNING
                context.started_at = context_started
            task = asyncio.create_task(self._run_strategy_from_db(context, strategy_row))
            async with self._lock:
                self._tasks[strategy_id] = task
            db.update_strategy_status(strategy_id, "running")
            logger.info("Strategy %d (%s) started", strategy_id, context.name)
            try:
                await feishu_notifier.notify_strategy_status(strategy_id, context.name, "running")
            except Exception:
                pass
            return True
        except Exception as e:
            logger.error("Failed to start strategy %d: %s", strategy_id, e)
            context.status = StrategyStatus.ERROR
            context.error_message = str(e)
            db.update_strategy_status(strategy_id, "error")
            return False

    async def stop_strategy(self, strategy_id: int, *, clear_metrics: bool = False) -> bool:
        return await self._set_strategy_status(
            strategy_id,
            StrategyStatus.STOPPED,
            call_on_stop=False,
            clear_metrics=clear_metrics,
        )

    async def pause_strategy(self, strategy_id: int) -> bool:
        return await self._set_strategy_status(
            strategy_id,
            StrategyStatus.PAUSED,
            call_on_stop=False,
            clear_metrics=False,
        )

    async def _set_strategy_status(
        self,
        strategy_id: int,
        status: StrategyStatus,
        *,
        call_on_stop: bool = False,
        clear_metrics: bool = False,
    ) -> bool:
        async with self._lock:
            if strategy_id in self._tasks:
                task = self._tasks.pop(strategy_id)
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                except Exception as e:
                    # 任务可能已在后台异常结束；stop 接口不应因此返回 500
                    logger.warning("Stop strategy %d: task awaited with error: %s", strategy_id, e)
            if strategy_id in self._contexts:
                self._contexts[strategy_id].status = status
            instance = self._strategy_instances.pop(strategy_id, None)
            if instance and call_on_stop:
                try:
                    await instance.on_stop()
                except Exception as e:
                    logger.warning("on_stop() error for strategy %d: %s", strategy_id, e)
        if clear_metrics:
            db.clear_strategy_runtime_metrics(strategy_id)
        db.update_strategy_status(
            strategy_id,
            status.value,
            clear_run_started_at=clear_metrics or status == StrategyStatus.ERROR,
        )
        if clear_metrics:
            try:
                from app.services.strategy_log_store import strategy_log_store

                strategy_log_store.clear(strategy_id)
            except Exception:
                pass
        logger.info("Strategy %d %s", strategy_id, status.value)
        ctx = self._contexts.get(strategy_id)
        name = ctx.name if ctx else f"#{strategy_id}"
        try:
            await feishu_notifier.notify_strategy_status(strategy_id, name, status.value)
        except Exception:
            pass
        return True

    # -------------------------------------------------------
    # 核心: 从 DB 记录启动策略
    # -------------------------------------------------------

    async def _run_strategy_from_db(self, context: StrategyContext, strategy_row: Dict):
        """从数据库策略记录加载并运行：优先统一 BaseStrategy 解析，其次 module_path/class_name，最后 script。"""
        from app.services.strategy_registry import resolve_unified_base_strategy_class

        unified = resolve_unified_base_strategy_class(strategy_row)
        if unified:
            strategy_cls, merged_cfg = unified
            context.config = {**(context.config or {}), **merged_cfg}
            await self._run_strategy_loop(context, strategy_cls)
            return

        module_path = strategy_row.get("module_path") or context.config.get("module_path", "")
        class_name = strategy_row.get("class_name") or context.config.get("class_name", "")
        script_content = strategy_row.get("script_content", "")

        if module_path and class_name:
            try:
                strategy_cls = load_strategy_by_module(module_path, class_name)
            except Exception as e:
                if not script_content or not script_content.strip():
                    raise
                logger.warning(
                    "策略 %d 动态模块 %s.%s 加载失败，改用数据库 script_content: %s",
                    context.strategy_id,
                    module_path,
                    class_name,
                    e,
                )
                strategy_cls = load_strategy_from_script(script_content, context.name)
        else:
            if not script_content or not script_content.strip():
                context.status = StrategyStatus.ERROR
                context.error_message = "策略脚本为空且未指定 module_path/class_name，且无法映射到内置 BaseStrategy"
                db.update_strategy_status(context.strategy_id, "error")
                return
            strategy_cls = load_strategy_from_script(script_content, context.name)

        await self._run_strategy_loop(context, strategy_cls)

    # -------------------------------------------------------
    # 核心: 主事件循环
    # -------------------------------------------------------

    def _fail_runtime_symbols_resolution(self, context: StrategyContext, message: str) -> None:
        context.status = StrategyStatus.ERROR
        context.error_message = message
        try:
            db.update_strategy_status(context.strategy_id, "error")
        except Exception:
            pass

    async def _resolve_runtime_symbols_if_needed(self, context: StrategyContext, strategy_cls: type) -> bool:
        if _list_from_symbols(context.symbols):
            return True
        resolver = getattr(strategy_cls, "resolve_runtime_symbols", None)
        if not callable(resolver):
            return True

        try:
            resolved = resolver(context.exchange, context.config)
            if inspect.isawaitable(resolved):
                resolved = await resolved
        except Exception as exc:
            message = f"runtime symbols 解析失败: {exc}"
            logger.warning(
                "策略 %s %s",
                context.name,
                message,
            )
            self._fail_runtime_symbols_resolution(context, message)
            return False

        market_type = str((context.config or {}).get("market_type") or "spot").lower()
        runtime_symbols = (
            _normalize_contract_symbol_list(resolved)
            if market_type == "swap"
            else _list_from_symbols(resolved)
        )
        if not runtime_symbols:
            message = (
                "runtime symbols 解析为空: "
                f"{getattr(strategy_cls, '__name__', str(strategy_cls))}.resolve_runtime_symbols 未返回行情驱动标的"
            )
            logger.warning(
                "策略 %s 未配置固定 symbols，%s",
                context.name,
                message,
            )
            self._fail_runtime_symbols_resolution(context, message)
            return False

        context.symbols = runtime_symbols
        context.config["symbols"] = list(runtime_symbols)
        logger.info(
            "策略 %s 使用 runtime 行情驱动标的: %s",
            context.name,
            runtime_symbols,
        )
        return True

    async def _run_strategy_loop(
        self,
        context: StrategyContext,
        strategy_cls: type,
        broker_override: Optional[Any] = None,
    ):
        """
        策略执行主循环（可由 DB 启动和测试入口共用）。

        参数:
            context:          引擎上下文
            strategy_cls:     BaseStrategy 子类
            broker_override:  外部注入的 broker (测试用)；为 None 时自动创建
        """
        if not await self._resolve_runtime_symbols_if_needed(context, strategy_cls):
            return

        logger.info("=" * 60)
        logger.info("启动策略: %s (id=%d)", context.name, context.strategy_id)
        logger.info("交易对: %s | 交易所: %s", context.symbols, context.exchange)
        logger.info("=" * 60)

        try:
            # ---- 1. Broker ----
            is_paper = context.config.get("is_paper_trading", True)
            if broker_override:
                broker = broker_override
            else:
                broker = self._build_broker_for_context(context)

            # ---- 2. 实例化策略 ----
            state = StrategyState(
                strategy_id=context.strategy_id,
                name=context.name,
                exchange=context.exchange,
                symbols=context.symbols,
            )
            if is_paper and isinstance(broker, (PaperBroker, ContractPaperBroker, CrossExchangePaperBroker)):
                state.positions["_capital"] = broker.initial_capital
            self._load_strategy_runtime_state(context.strategy_id, state)
            if isinstance(broker, CrossExchangePaperBroker):
                broker.restore_from_state(state.positions.get("_cross_exchange_arbitrage") or {})

            strategy_instance = strategy_cls(state=state, broker=broker)
            strategy_instance.set_config(context.config)

            async with self._lock:
                self._strategy_instances[context.strategy_id] = strategy_instance

            # ---- 3. on_init / on_start ----
            await strategy_instance.on_init()
            await strategy_instance.on_start()

            # ---- 4. 推断 timeframe ----
            timeframe = context.config.get("timeframe", "1m")
            logger.info("K线周期: %s (每 %ds 拉取一次)", timeframe, _TIMEFRAME_SECONDS.get(timeframe, 60))
            loop_config = dict(context.config or {})
            loop_config["name"] = context.name
            tick_driven = _is_tick_driven_strategy_config(loop_config)
            quote_interval_sec = max(1.0, min(_float_value(context.config.get("quote_interval_sec"), 3.0), 30.0))
            if tick_driven:
                logger.info("Tick 驱动已启用: quote_interval=%.1fs", quote_interval_sec)

            # ---- 4b. 历史 K 线预热（Warmup） + 下单墙钟缓冲 ----
            warmup_limit = _resolve_warmup_bar_count(context)
            order_delay = float(
                context.config.get("warmup_order_delay_sec", DEFAULT_WARMUP_ORDER_DELAY_SEC)
            )
            await self._warmup_history(
                context, strategy_instance, broker, timeframe, warmup_limit, order_delay_sec=order_delay,
            )

            await self._broadcast_log(context, "策略初始化完成，进入主循环")

            # 每交易对单独去重：共享一个 timestamp 会让 multi-symbol 时仅首个 symbol 收到 on_bar
            last_bar_ts_by_symbol: Dict[str, Optional[int]] = {}
            tick_count = 0

            # ---- 5. 主循环 ----
            while context.status == StrategyStatus.RUNNING:
                try:
                    # 5a. 全局风控
                    try:
                        await self.run_account_risk_check(context.exchange, context=context)
                    except Exception:
                        pass
                    if context.status != StrategyStatus.RUNNING:
                        break

                    # 5b. 拉取 K 线 & 驱动策略
                    for symbol in context.symbols:
                        bar = await self._fetch_latest_bar(context.exchange, symbol, timeframe)
                        if bar is None:
                            continue

                        # 去重: 同一交易对同一根 bar 不重复触发
                        prev_ts = last_bar_ts_by_symbol.get(symbol)
                        if bar.timestamp == prev_ts:
                            continue
                        last_bar_ts_by_symbol[symbol] = bar.timestamp

                        tick_count += 1

                        # 更新 broker 标记价格（用于后续下单 sizing / 监控定价）
                        mark_events = []
                        if hasattr(broker, "update_mark_price"):
                            mark_events = broker.update_mark_price(symbol, bar.close) or []
                        if isinstance(broker, (PaperBroker, ContractPaperBroker, CrossExchangePaperBroker)):
                            state.positions["_capital"] = broker.balance
                            if isinstance(broker, ContractPaperBroker) and await self._handle_contract_liquidation_events(
                                context,
                                mark_events or [],
                            ):
                                return

                        if tick_count % 10 == 1:
                            bar_time = datetime.fromtimestamp(bar.timestamp / 1000).strftime("%Y-%m-%d %H:%M")
                            logger.info(
                                "[Tick #%d] %s %s | O=%.2f H=%.2f L=%.2f C=%.2f V=%.1f",
                                tick_count, bar_time, symbol,
                                bar.open, bar.high, bar.low, bar.close, bar.volume,
                            )

                        if isinstance(broker, ContractPaperBroker):
                            broker.set_signal_bar_timestamp(bar.timestamp)
                        await strategy_instance.on_bar(bar)
                        if isinstance(broker, CrossExchangePaperBroker):
                            state.positions["_cross_exchange_arbitrage"] = broker.export_state()
                        self._persist_strategy_runtime_state(context.strategy_id, state)
                        if str(getattr(strategy_instance.state, "status", "")).lower() == StrategyStatus.PAUSED.value:
                            context.status = StrategyStatus.PAUSED
                            db.update_strategy_status(context.strategy_id, StrategyStatus.PAUSED.value, clear_run_started_at=False)
                            await self._broadcast_log(
                                context,
                                getattr(strategy_instance.state, "error_message", None) or "策略已自动暂停",
                                level="error",
                            )
                            return

                    if tick_driven:
                        tick_limit = int(context.config.get("orderbook_depth_limit", 20) or 20)
                        for symbol in context.symbols:
                            tick = await self._fetch_latest_tick(context.exchange, symbol, limit=tick_limit)
                            if tick is None:
                                continue

                            mark_events = []
                            if hasattr(broker, "update_mark_price"):
                                mark_events = broker.update_mark_price(symbol, tick.last) or []
                            if isinstance(broker, (PaperBroker, ContractPaperBroker, CrossExchangePaperBroker)):
                                state.positions["_capital"] = broker.balance
                                if isinstance(broker, ContractPaperBroker) and await self._handle_contract_liquidation_events(
                                    context,
                                    mark_events or [],
                                ):
                                    return

                            await strategy_instance.on_tick(tick)
                            if isinstance(broker, CrossExchangePaperBroker):
                                state.positions["_cross_exchange_arbitrage"] = broker.export_state()
                            self._persist_strategy_runtime_state(context.strategy_id, state)
                            if str(getattr(strategy_instance.state, "status", "")).lower() == StrategyStatus.PAUSED.value:
                                context.status = StrategyStatus.PAUSED
                                db.update_strategy_status(context.strategy_id, StrategyStatus.PAUSED.value, clear_run_started_at=False)
                                await self._broadcast_log(
                                    context,
                                    getattr(strategy_instance.state, "error_message", None) or "策略已自动暂停",
                                    level="error",
                                )
                                return

                    # 5c. 统计更新
                    if isinstance(broker, (PaperBroker, ContractPaperBroker, CrossExchangePaperBroker)):
                        context.pnl = broker.equity - broker.initial_capital
                        context.total_trades = len(broker.trades)
                        self._record_equity_sample(context, broker, source="runtime")

                    # 5d. 智能 sleep：对齐下一根 K 线收盘
                    wait_sec = quote_interval_sec if tick_driven else _seconds_until_next_bar(timeframe)
                    await asyncio.sleep(wait_sec)

                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.error("Strategy %s tick error: %s", context.name, e)
                    await self._broadcast_log(context, f"Tick 异常: {e}", level="error")
                    await asyncio.sleep(10)

        except asyncio.CancelledError:
            logger.info("Strategy %s cancelled", context.name)
        except Exception as e:
            logger.error("Strategy %s fatal error: %s\n%s", context.name, e, traceback.format_exc())
            context.status = StrategyStatus.ERROR
            context.error_message = str(e)
            try:
                db.update_strategy_status(context.strategy_id, "error")
            except Exception:
                pass
            await self._broadcast_log(context, f"策略异常终止: {e}", level="error")
        finally:
            if 'state' in dir():
                self._persist_strategy_runtime_state(context.strategy_id, state)
            # 打印 PaperBroker 最终摘要
            if 'broker' in dir() and isinstance(broker, (PaperBroker, ContractPaperBroker, CrossExchangePaperBroker)):
                logger.info("\n%s", broker.summary())

    # -------------------------------------------------------
    # 历史 K 线预热
    # -------------------------------------------------------

    async def _warmup_history(
        self,
        context: StrategyContext,
        strategy_instance: BaseStrategy,
        broker: Any,
        timeframe: str,
        limit: int = 100,
        *,
        order_delay_sec: float = DEFAULT_WARMUP_ORDER_DELAY_SEC,
    ):
        """
        在进入主循环之前，拉取最近 limit 根已收盘历史 K 线，
        依次喂给 on_bar()，使策略内部指标容器（deque / EMA 等）预热完毕。
        根数较大时自动分批回拉。预热结束后可选经过 order_delay_sec 墙钟时间再允许下单。
        """
        logger.info("开始 K 线预热: 目标 %d 根已收盘 %s K 线 ...", limit, timeframe)

        if hasattr(broker, "warmup_mode"):
            broker.warmup_mode = True
        if hasattr(broker, "orders_deadline_monotonic"):
            broker.orders_deadline_monotonic = 0.0

        for symbol in context.symbols:
            exchange = exchange_manager.get_exchange(context.exchange)
            if not exchange:
                logger.warning("预热跳过: 交易所 %s 不可用", context.exchange)
                continue

            try:
                history = await self._fetch_warmup_ohlcv_closed(
                    context.exchange,
                    symbol,
                    timeframe,
                    limit,
                )
            except Exception as e:
                logger.warning("预热拉取失败 (%s %s): %s", symbol, timeframe, e)
                continue

            if not history:
                logger.warning("预热无数据: %s %s", symbol, timeframe)
                continue

            for candle in history:
                bar = _candle_to_bar(candle, context.exchange, symbol, timeframe)

                if hasattr(broker, "update_mark_price"):
                    broker.update_mark_price(symbol, bar.close)

                warmup_handler = getattr(strategy_instance, "on_warmup_bar", None)
                if callable(warmup_handler):
                    await warmup_handler(bar)
                else:
                    await strategy_instance.on_bar(bar)

            logger.info(
                "预热完成: %s %s — 共 %d 根已收盘 K 线",
                symbol, timeframe, len(history),
            )

        if hasattr(broker, "warmup_mode"):
            broker.warmup_mode = False

        if order_delay_sec > 0 and hasattr(broker, "orders_deadline_monotonic"):
            broker.orders_deadline_monotonic = time.monotonic() + order_delay_sec
            logger.info(
                "预热下单缓冲: 未来 %.1fs 内仅预测/on_bar，不允许下单",
                order_delay_sec,
            )
            await self._broadcast_log(
                context,
                f"K 线预热完成 ({limit} 根 {timeframe})，{order_delay_sec:.0f}s 后可下单",
            )
        else:
            await self._broadcast_log(context, f"K 线预热完成 ({limit} 根 {timeframe})")

    # -------------------------------------------------------
    # K 线拉取
    # -------------------------------------------------------

    async def _fetch_latest_bar(
        self, exchange_name: str, symbol: str, timeframe: str,
    ) -> Optional[BarData]:
        """
        异步从交易所拉取最新一根已收盘 K 线，封装为 BarData。

        多策略会共享同一批 symbols（例如 SuperPnL Top20 universe），如果每个 strategy task
        都单独调用 CCXT，会导致 OKX 50011（Too Many Requests）。这里做三层保护：

        - in-flight 去重：同一 (exchange, symbol, timeframe) 并发请求合并为一次
        - 短 TTL 缓存：同一根已收盘 bar 在 1m 内重复读取直接返回缓存
        - 全局串行：串行化底层 CCXT 调用，规避多线程并发下 enableRateLimit 失效
        """
        key = (exchange_name, symbol, timeframe)
        now = time.monotonic()
        expected_ts_ms = _expected_last_closed_bar_ts_ms(timeframe)
        cached = self._latest_bar_cache.get(key)
        if cached is not None:
            cached_at, cached_bar = cached
            # 短 TTL：最近刚拉取过（含失败 None）直接复用，避免限速失败被其他策略立刻重试放大。
            if now - cached_at <= 12.0:
                return cached_bar

            # 长 TTL：同一根“已收盘 bar”在整个 bar 周期内 timestamp 不变；
            # 只要缓存里的 bar.timestamp 已经达到 expected_ts_ms，就可复用更久，
            # 避免 10+ 个策略并行时把同一 symbol 的同一根 bar 重复拉 N 次触发 50011。
            if cached_bar is not None and now - cached_at <= 120.0:
                cached_ts = int(getattr(cached_bar, "timestamp", 0) or 0)
                if cached_ts >= expected_ts_ms:
                    return cached_bar

        inflight = self._latest_bar_inflight.get(key)
        if inflight is not None:
            try:
                return await inflight
            except Exception:
                return None

        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        self._latest_bar_inflight[key] = fut
        try:
            bar = await self._fetch_latest_bar_uncached(exchange_name, symbol, timeframe)
            self._latest_bar_cache[key] = (time.monotonic(), bar)
            if not fut.done():
                fut.set_result(bar)
            return bar
        except Exception as e:
            if not fut.done():
                fut.set_exception(e)
            raise
        finally:
            self._latest_bar_inflight.pop(key, None)

    async def _fetch_latest_bar_uncached(
        self, exchange_name: str, symbol: str, timeframe: str,
    ) -> Optional[BarData]:
        """真实请求交易所的版本（不做缓存/去重）。"""
        exchange = exchange_manager.get_exchange(exchange_name)
        if not exchange:
            logger.warning("交易所 %s 不可用", exchange_name)
            return None

        try:
            async with self._ohlcv_fetch_lock:
                await self._throttle_ohlcv_fetch()
                ohlcv = await asyncio.get_running_loop().run_in_executor(
                    None, lambda: exchange.fetch_ohlcv(symbol, timeframe, limit=2),
                )
            if not ohlcv or len(ohlcv) < 2:
                return None

            candle = ohlcv[-2]
            return _candle_to_bar(candle, exchange_name, symbol, timeframe)
        except Exception as e:
            self._mark_ohlcv_rate_limited(e)
            logger.warning("拉取 K 线失败 (%s %s %s): %s", exchange_name, symbol, timeframe, e)
            return None

    async def _fetch_latest_tick(
        self,
        exchange_name: str,
        symbol: str,
        *,
        limit: int = 20,
    ) -> Optional[TickData]:
        """拉取最新盘口快照并封装成 TickData，用于做市/盘口驱动策略。"""
        depth_limit = max(1, min(int(limit or 20), 100))
        key = (exchange_name, symbol, depth_limit)
        now = time.monotonic()
        cached = self._latest_tick_cache.get(key)
        if cached is not None:
            cached_at, cached_tick = cached
            if now - cached_at <= 0.75:
                return cached_tick

        inflight = self._latest_tick_inflight.get(key)
        if inflight is not None:
            try:
                return await inflight
            except Exception:
                return None

        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        self._latest_tick_inflight[key] = fut
        try:
            tick = await self._fetch_latest_tick_uncached(exchange_name, symbol, limit=depth_limit)
            self._latest_tick_cache[key] = (time.monotonic(), tick)
            if not fut.done():
                fut.set_result(tick)
            return tick
        except Exception as e:
            if not fut.done():
                fut.set_result(None)
            logger.warning("拉取盘口 tick 失败 (%s %s): %s", exchange_name, symbol, e)
            return None
        finally:
            self._latest_tick_inflight.pop(key, None)

    async def _fetch_latest_tick_uncached(
        self,
        exchange_name: str,
        symbol: str,
        *,
        limit: int = 20,
    ) -> Optional[TickData]:
        exchange = exchange_manager.get_exchange(exchange_name)
        if not exchange:
            logger.warning("交易所 %s 不可用", exchange_name)
            return None

        def _sync_fetch() -> tuple[Any, Any]:
            order_book = exchange.fetch_order_book(symbol, limit=limit)
            ticker = None
            fetch_ticker = getattr(exchange, "fetch_ticker", None)
            if callable(fetch_ticker):
                try:
                    ticker = fetch_ticker(symbol)
                except Exception:
                    ticker = None
            return order_book, ticker

        async with self._tick_fetch_lock:
            order_book, ticker = await asyncio.get_running_loop().run_in_executor(None, _sync_fetch)

        if not isinstance(order_book, dict):
            return None
        bids = order_book.get("bids") or []
        asks = order_book.get("asks") or []
        if not bids or not asks:
            return None

        def _row_price(row: Any) -> float:
            try:
                if isinstance(row, dict):
                    return float(row.get("price") or row.get("px") or 0.0)
                return float(row[0])
            except (TypeError, ValueError, IndexError):
                return 0.0

        def _row_amount(row: Any) -> float:
            try:
                if isinstance(row, dict):
                    return float(row.get("amount") or row.get("size") or row.get("sz") or 0.0)
                return float(row[1])
            except (TypeError, ValueError, IndexError):
                return 0.0

        bid = _row_price(bids[0])
        ask = _row_price(asks[0])
        if bid <= 0 or ask <= 0 or ask <= bid:
            return None

        bid_depth = sum(max(0.0, _row_amount(row)) for row in bids[:limit])
        ask_depth = sum(max(0.0, _row_amount(row)) for row in asks[:limit])
        total_depth = bid_depth + ask_depth
        imbalance = (bid_depth - ask_depth) / total_depth if total_depth > 0 else None
        mid = (bid + ask) / 2.0
        spread_bps = round((ask - bid) / mid * 10_000.0, 8) if mid > 0 else None

        last = self._ticker_price(ticker)
        if last <= 0:
            last = mid

        timestamp = None
        if isinstance(order_book, dict):
            timestamp = order_book.get("timestamp")
        if timestamp in (None, "") and isinstance(ticker, dict):
            timestamp = ticker.get("timestamp")
        try:
            ts_ms = int(timestamp) if timestamp not in (None, "") else int(time.time() * 1000)
        except (TypeError, ValueError):
            ts_ms = int(time.time() * 1000)

        volume = None
        if isinstance(ticker, dict):
            for key_name in ("baseVolume", "quoteVolume", "volume"):
                try:
                    value = ticker.get(key_name)
                    if value not in (None, ""):
                        volume = float(value)
                        break
                except (TypeError, ValueError):
                    continue

        return TickData(
            exchange=exchange_name,
            symbol=symbol,
            timestamp=ts_ms,
            last=last,
            bid=bid,
            ask=ask,
            volume=volume,
            bid_depth=bid_depth,
            ask_depth=ask_depth,
            spread_bps=spread_bps,
            imbalance=imbalance,
        )

    async def _fetch_warmup_ohlcv_closed(
        self,
        exchange_name: str,
        symbol: str,
        timeframe: str,
        limit: int,
    ) -> List[list]:
        """
        预热阶段拉取最近 limit 根已收盘 K 线（list[list] 格式）。

        多策略并发启动时会重复拉取同一批 warmup 数据；这里做 in-flight 合并 + TTL 缓存，
        并在底层用全局 lock 串行化 CCXT 调用以避免 OKX 50011。
        """
        key = (exchange_name, symbol, timeframe, int(limit))
        now = time.monotonic()
        cached = self._warmup_ohlcv_cache.get(key)
        if cached is not None:
            cached_at, cached_hist = cached
            if now - cached_at <= 90.0:
                return list(cached_hist)

        inflight = self._warmup_ohlcv_inflight.get(key)
        if inflight is not None:
            try:
                return await inflight
            except Exception:
                return []

        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        self._warmup_ohlcv_inflight[key] = fut
        try:
            exchange = exchange_manager.get_exchange(exchange_name)
            if not exchange:
                raise RuntimeError(f"交易所 {exchange_name} 不可用")

            if limit + 1 <= _WARMUP_OHLCV_CHUNK:
                async with self._ohlcv_fetch_lock:
                    await self._throttle_ohlcv_fetch()
                    ohlcv = await asyncio.get_running_loop().run_in_executor(
                        None, lambda: exchange.fetch_ohlcv(symbol, timeframe, limit=limit + 1),
                    )
                if not ohlcv:
                    history: List[list] = []
                else:
                    history = ohlcv[:-1] if len(ohlcv) > limit else ohlcv
            else:
                async with self._ohlcv_fetch_lock:
                    await self._throttle_ohlcv_fetch()
                    history = await asyncio.get_running_loop().run_in_executor(
                        None, lambda: _sync_fetch_warmup_ohlcv_closed(exchange, symbol, timeframe, limit),
                    )

            history = list(history or [])
            self._warmup_ohlcv_cache[key] = (time.monotonic(), history)
            if not fut.done():
                fut.set_result(history)
            return history
        except Exception as e:
            self._mark_ohlcv_rate_limited(e)
            if not fut.done():
                fut.set_exception(e)
            raise
        finally:
            self._warmup_ohlcv_inflight.pop(key, None)

    # -------------------------------------------------------
    # WebSocket 广播
    # -------------------------------------------------------

    async def _broadcast_log(self, context: StrategyContext, message: str, level: str = "info"):
        try:
            from app.services.strategy_log_store import strategy_log_store

            payload = {"type": "log", "message": message, "level": level}
            strategy_log_store.append(context.strategy_id, payload, level=level)
            await connection_manager.broadcast(
                "strategy", context.exchange, str(context.strategy_id),
                payload,
            )
        except Exception:
            pass

    async def _broadcast_strategy_event(
        self,
        context: StrategyContext,
        payload: Dict[str, Any],
        *,
        level: str = "info",
    ) -> None:
        try:
            from app.services.strategy_log_store import strategy_log_store

            data = dict(payload)
            data.setdefault("level", level)
            strategy_log_store.append(context.strategy_id, data, level=level)
            await connection_manager.broadcast(
                "strategy",
                context.exchange,
                str(context.strategy_id),
                data,
            )
        except Exception:
            pass

    async def _handle_contract_liquidation_events(
        self,
        context: StrategyContext,
        events: List[Dict[str, Any]],
    ) -> bool:
        liquidations = [
            event for event in (events or [])
            if isinstance(event, dict) and event.get("type") == "liquidation"
        ]
        if not liquidations:
            return False

        for event in liquidations:
            symbol = str(event.get("symbol") or "")
            side = str(event.get("pos_side") or "")
            price = _float_value(event.get("price"), 0.0)
            liq_price = _float_value(event.get("liquidation_price"), 0.0)
            message = (
                f"合约模拟盘爆仓: {symbol} {side} "
                f"标记价 {price:.6g} / 强平价 {liq_price:.6g}，策略已自动暂停"
            )
            await self._broadcast_strategy_event(
                context,
                {
                    "type": "liquidation",
                    "message": message,
                    "symbol": symbol,
                    "pos_side": side,
                    "price": price,
                    "liquidation_price": liq_price,
                    "contracts": event.get("contracts"),
                    "leverage": event.get("leverage"),
                    "realized_pnl": event.get("realized_pnl"),
                    "maintenance_margin": event.get("maintenance_margin"),
                    "account_equity_before": event.get("account_equity_before"),
                },
                level="error",
            )
            try:
                await feishu_notifier.notify_paper_liquidation(
                    {
                        "strategy_id": context.strategy_id,
                        "strategy_name": context.name,
                        **event,
                    }
                )
            except Exception as exc:
                logger.warning("合约模拟盘爆仓告警发送失败: %s", exc)

        context.status = StrategyStatus.PAUSED
        db.update_strategy_status(
            context.strategy_id,
            StrategyStatus.PAUSED.value,
            clear_run_started_at=False,
        )
        return True

    # -------------------------------------------------------
    # 全局风控
    # -------------------------------------------------------

    async def run_account_risk_check(
        self,
        exchange_name: str = "okx",
        *,
        context: Optional[StrategyContext] = None,
    ):
        if context is not None and bool((context.config or {}).get("is_paper_trading", True)):
            return
        if self._risk_manager.is_circuit_breaker_active():
            return
        total_equity = await trading_service._get_account_equity(exchange_name)
        if total_equity <= 0:
            return
        if self._risk_manager.get_status().get("equity_peak", 0) <= 0:
            self._risk_manager.initialize(total_equity)
        result = self._risk_manager.check_account_drawdown(total_equity)
        if not result.approved and result.risk_level == RiskLevel.CIRCUIT_BREAKER:
            await self._activate_global_kill_switch(exchange_name)

    async def _activate_global_kill_switch(self, exchange_name: str):
        async with self._kill_switch_lock:
            snapshot = self._risk_manager.get_circuit_breaker_snapshot()
            if not snapshot.get("active"):
                return
            cancelled_orders, cancel_failures = await self._cancel_all_orders(exchange_name)
            positions_closed, close_failures = await self._close_all_positions(exchange_name)
            strategies_paused = await self._pause_all_running_strategies()
            failures = cancel_failures + close_failures
            logger.warning(
                "Global kill switch activated: reason=%s positions_closed=%s strategies_paused=%s",
                snapshot.get("reason"), positions_closed, strategies_paused,
            )
            await feishu_notifier.notify_kill_switch({
                "reason": snapshot.get("reason", ""),
                "equity_peak": snapshot.get("equity_peak", 0),
                "current_equity": snapshot.get("current_equity", 0),
                "drawdown_pct": snapshot.get("drawdown_pct", 0),
                "positions_closed": positions_closed,
                "strategies_paused": strategies_paused,
                "orders_cancelled": cancelled_orders,
                "failures": failures,
            })

    async def _cancel_all_orders(self, exchange_name: str) -> tuple[int, List[str]]:
        try:
            cancelled = await trading_service.cancel_all_orders(exchange_name)
            return cancelled, []
        except Exception as exc:
            logger.error("Kill switch 撤单失败: %s", exc)
            return 0, [f"撤销挂单失败: {exc}"]

    async def _close_all_positions(self, exchange_name: str) -> tuple[int, List[str]]:
        positions_closed, failures = 0, []
        try:
            positions = await trading_service.get_positions(exchange_name)
        except Exception as exc:
            return 0, [f"获取持仓失败: {exc}"]
        symbols = sorted({
            p.get("symbol") for p in positions
            if p.get("symbol") and abs(p.get("amount", 0) or 0) > 0
        })
        for symbol in symbols:
            try:
                results = await trading_service.futures_close_all(exchange_name, symbol)
                if results:
                    positions_closed += 1
            except Exception as exc:
                failures.append(f"{symbol} 平仓失败: {exc}")
        return positions_closed, failures

    async def _pause_all_running_strategies(self) -> int:
        paused = 0
        for sid in [s for s, c in self._contexts.items() if c.status == StrategyStatus.RUNNING]:
            if await self.pause_strategy(sid):
                paused += 1
        return paused

    async def reset_global_circuit_breaker(self):
        self._risk_manager.reset_circuit_breaker()
        logger.warning("Global circuit breaker reset manually")

    def get_risk_status(self) -> Dict[str, Any]:
        return self._risk_manager.get_status()

    # -------------------------------------------------------
    # 状态查询
    # -------------------------------------------------------

    def get_strategy_status(self, strategy_id: int) -> Optional[Dict]:
        context = self._contexts.get(strategy_id)
        if not context:
            return None
        result: Dict[str, Any] = {
            "strategy_id": strategy_id,
            "name": context.name,
            "status": context.status.value,
            "exchange": context.exchange,
            "symbols": context.symbols,
            "strategy_key": context.config.get("strategy_key"),
            "is_ai_autonomous": _is_ai_autonomous_config(context.config),
            "pnl": context.pnl,
            "total_trades": context.total_trades,
            "error_message": context.error_message,
            "started_at": context.started_at.isoformat() if context.started_at else None,
            "equity": 0.0,
            "initial_capital": 0.0,
            "balance": 0.0,
            "unrealized_pnl": 0.0,
            "positions": {},
            "return_pct": 0.0,
            "win_rate": 0.0,
            "closing_trades": 0,
            "winning_trades": 0,
            "gross_profit": 0.0,
            "gross_loss": 0.0,
            "profit_factor": 0.0,
        }
        instance = self._strategy_instances.get(strategy_id)
        if instance and isinstance(instance.broker, (PaperBroker, ContractPaperBroker, CrossExchangePaperBroker)):
            broker = instance.broker
            result["equity"] = broker.equity
            result["initial_capital"] = broker.initial_capital
            result["balance"] = broker.balance
            if isinstance(broker, ContractPaperBroker):
                positions = broker.account.list_positions() + broker.list_spot_positions()
                visible_positions = [p for p in positions if self._is_display_position(p)]
                result["unrealized_pnl"] = sum(
                    float(p.get("unrealized_pnl") or 0.0) for p in visible_positions
                )
                result["positions"] = {
                    f"{p['symbol']}:{p.get('pos_side') or p.get('market_type') or p.get('side') or 'position'}": p
                    for p in visible_positions
                }
            elif isinstance(broker, CrossExchangePaperBroker):
                positions = [
                    {
                        **p,
                        "market_type": "cross_exchange_swap",
                        "side": "market_neutral",
                        "size": abs(float(p.get("long_notional_usdt") or 0.0)) + abs(float(p.get("short_notional_usdt") or 0.0)),
                        "unrealized_pnl": p.get("unrealized_pnl_usdt", 0.0),
                    }
                    for p in broker.list_positions()
                ]
                visible_positions = [p for p in positions if self._is_display_position(p)]
                result["unrealized_pnl"] = sum(
                    float(p.get("unrealized_pnl_usdt") or 0.0) for p in visible_positions
                )
                result["positions"] = {
                    f"{p['symbol']}:cross_exchange": p
                    for p in visible_positions
                }
            else:
                visible_positions = [
                    (sym, pos)
                    for sym, pos in broker.positions.items()
                    if self._is_display_position(pos)
                ]
                result["unrealized_pnl"] = sum(
                    pos.get("unrealized_pnl", 0) for _, pos in visible_positions
                )
                result["positions"] = {
                    sym: {
                        "size": pos["size"],
                        "entry_price": pos["entry_price"],
                        "side": pos.get("side", "long"),
                        "unrealized_pnl": pos.get("unrealized_pnl", 0),
                        "mark_price": broker._last_prices.get(sym, 0),
                    }
                    for sym, pos in visible_positions
                }
            result["pnl"] = broker.equity - broker.initial_capital
            result["return_pct"] = (
                (broker.equity - broker.initial_capital) / broker.initial_capital * 100
                if broker.initial_capital > 0 else 0.0
            )
            result.update(
                self._strategy_trade_metrics(
                    strategy_id,
                    started_at=context.started_at,
                    fallback_trades=broker.trades,
                )
            )
        return result

    def refresh_paper_marks(self, strategy_id: int, prices: Dict[str, float]) -> None:
        """用最新行情刷新模拟盘持仓标记价。"""
        instance = self._strategy_instances.get(strategy_id)
        if not instance or not isinstance(instance.broker, (PaperBroker, ContractPaperBroker, CrossExchangePaperBroker)):
            return
        for symbol, price in prices.items():
            try:
                px = float(price)
            except (TypeError, ValueError):
                continue
            if px > 0:
                instance.broker.update_mark_price(symbol, px)

    async def close_paper_position(
        self,
        strategy_id: int,
        *,
        symbol: str,
        side: Optional[str] = None,
        market_type: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Manually close one visible paper position without touching real accounts."""
        instance = self._strategy_instances.get(strategy_id)
        if not instance or not isinstance(instance.broker, (PaperBroker, ContractPaperBroker)):
            return {"status": "rejected", "reason": "paper runtime not available", "symbol": symbol}

        broker = instance.broker
        normalized_symbol = str(symbol or "").strip()
        if not normalized_symbol:
            return {"status": "rejected", "reason": "symbol required"}

        normalized_market = str(market_type or "").strip().lower()
        normalized_side = str(side or "").strip().lower()

        if isinstance(broker, ContractPaperBroker):
            is_contract_target = (
                normalized_market == "swap"
                or normalized_symbol.endswith(":USDT")
                or normalized_side in {"short", "long"} and normalized_market != "spot"
            )
            if is_contract_target:
                close_side = normalized_side if normalized_side in {"long", "short"} else ""
                if not close_side:
                    return {
                        "status": "rejected",
                        "reason": "contract position side required",
                        "symbol": normalized_symbol,
                    }
                result = await broker.close_contract(
                    normalized_symbol,
                    close_side,
                    ratio=1.0,
                    emit_signal=False,
                )
            else:
                spot_symbol = broker._spot_symbol(normalized_symbol)
                spot_pos = broker.spot_positions.get(spot_symbol)
                if not spot_pos or float(spot_pos.get("size") or 0.0) <= 1e-12:
                    return {"status": "skipped", "reason": "no_position", "symbol": spot_symbol}
                result = await broker.sell(spot_symbol, float(spot_pos.get("size") or 0.0))
        else:
            result = await broker.close_position(normalized_symbol)

        context = self._contexts.get(strategy_id)
        if context is not None and isinstance(broker, (PaperBroker, ContractPaperBroker)):
            context.pnl = broker.equity - broker.initial_capital
            context.total_trades = len(broker.trades)
        return dict(result)

    def _position_symbols_from_status(self, status: Optional[Dict[str, Any]]) -> List[str]:
        if not status:
            return []
        raw = status.get("positions") or {}
        if not isinstance(raw, dict):
            return []
        symbols: set[str] = set()
        for key, position in raw.items():
            if not isinstance(position, dict) or not self._is_display_position(position):
                continue
            symbol = str(position.get("symbol") or key).strip()
            if symbol:
                symbols.add(symbol)
        return sorted(symbols)

    def refresh_running_position_marks(self, strategy_ids: Optional[List[int]] = None) -> None:
        """刷新运行中模拟盘真实持仓的标记价，供监控卡片与详情页保持一致。"""
        if strategy_ids is None:
            ids = [
                sid for sid, ctx in self._contexts.items()
                if ctx.status == StrategyStatus.RUNNING
            ]
        else:
            ids = strategy_ids
        symbols_by_exchange: Dict[str, set[str]] = {}
        instance_symbols: Dict[int, tuple[str, List[str]]] = {}
        for strategy_id in ids:
            status = self.get_strategy_status(strategy_id)
            if not status or status.get("status") != StrategyStatus.RUNNING.value:
                continue
            exchange_name = str(status.get("exchange") or "okx")
            symbols = self._position_symbols_from_status(status)
            if not symbols:
                continue
            instance_symbols[strategy_id] = (exchange_name, symbols)
            symbols_by_exchange.setdefault(exchange_name, set()).update(symbols)

        if not instance_symbols:
            return

        prices_by_exchange: Dict[str, Dict[str, float]] = {}
        for exchange_name, symbols in symbols_by_exchange.items():
            ex = exchange_manager.get_exchange(exchange_name)
            if not ex:
                continue
            prices: Dict[str, float] = {}
            for symbol in sorted(symbols):
                try:
                    px = self._ticker_price(ex.fetch_ticker(symbol))
                except Exception as exc:
                    logger.debug("refresh running paper mark failed: %s %s", symbol, exc)
                    continue
                if px > 0:
                    prices[symbol] = px
            if prices:
                prices_by_exchange[exchange_name] = prices

        for strategy_id, (exchange_name, symbols) in instance_symbols.items():
            exchange_prices = prices_by_exchange.get(exchange_name) or {}
            prices = {
                symbol: exchange_prices[symbol]
                for symbol in symbols
                if symbol in exchange_prices
            }
            if prices:
                self.refresh_paper_marks(strategy_id, prices)

    def get_all_running(self, *, refresh_marks: bool = False) -> List[Dict]:
        strategy_ids = [
            sid for sid, ctx in self._contexts.items()
            if ctx.status == StrategyStatus.RUNNING
        ]
        if refresh_marks:
            self.refresh_running_position_marks(strategy_ids)
        return [
            s for sid in strategy_ids
            if (s := self.get_strategy_status(sid))
        ]

    def list_running_or_paused_ids(self) -> List[int]:
        """供 Live 聚合接口挑选当前会话（运行中或已暂停）。"""
        return [
            sid for sid, ctx in self._contexts.items()
            if ctx.status in (StrategyStatus.RUNNING, StrategyStatus.PAUSED)
        ]

    def drop_cached_context(self, strategy_id: int) -> None:
        """清除内存中的策略上下文，便于下次启动从数据库加载最新 config（如 Live 配置页保存后）。"""
        self._contexts.pop(strategy_id, None)

    def update_cached_config(self, strategy_id: int, config: Dict[str, Any]) -> Dict[str, bool]:
        """更新内存中的策略配置；运行中策略可选择性实现 apply_runtime_config。"""
        context_updated = False
        runtime_applied = False
        next_config = dict(config or {})

        context = self._contexts.get(strategy_id)
        if context is not None:
            context.config = next_config
            context_updated = True

        instance = self._strategy_instances.get(strategy_id)
        if instance is not None:
            instance.set_config(next_config)
            apply_runtime_config = getattr(instance, "apply_runtime_config", None)
            if callable(apply_runtime_config):
                apply_runtime_config(next_config)
                runtime_applied = True

        return {"context_updated": context_updated, "runtime_applied": runtime_applied}


# 全局引擎实例
strategy_engine = StrategyEngine()
