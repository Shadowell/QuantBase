"""Scheduled real live-account profit card push service."""
from __future__ import annotations

import asyncio
import math
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional

from app.db.local_db import db_instance as db
from app.services.feishu_notifier import feishu_notifier
from app.services import live_account_service
from app.services.live_signal_execution_service import live_signal_execution_service
from app.services.strategy_profit_push_service import (
    as_float,
    clamp_interval_minutes,
    iso,
    parse_dt,
    utcnow,
)
from app.services.trading_service import trading_service


def _upper_symbol(value: Any) -> str:
    return str(value or "").strip().upper()


def _extract_symbols(row: Optional[Dict[str, Any]]) -> List[str]:
    if not row:
        return []
    cfg = row.get("config") or {}
    if not isinstance(cfg, dict):
        cfg = {}
    raw = cfg.get("trade_symbols") or cfg.get("symbols") or row.get("symbols") or []
    if not isinstance(raw, list):
        raw = [raw] if raw else []
    return [str(item) for item in raw if str(item or "").strip()]


def _position_symbol(position: Dict[str, Any]) -> str:
    return str(position.get("symbol") or position.get("instId") or position.get("currency") or "")


def _position_side(position: Dict[str, Any]) -> str:
    side = str(position.get("side") or position.get("posSide") or position.get("pos_side") or "").lower()
    if side in {"long", "short"}:
        return side
    size = as_float(
        position.get("contracts")
        or position.get("size")
        or position.get("base_qty")
        or position.get("baseQty")
        or position.get("amount")
    )
    return "short" if size < 0 else "long"


def _position_size(position: Dict[str, Any]) -> float:
    return abs(
        as_float(
            position.get("contracts")
            or position.get("contractSize")
            or position.get("size")
            or position.get("base_qty")
            or position.get("baseQty")
            or position.get("amount")
        )
    )


def _position_price(position: Dict[str, Any], *keys: str) -> float:
    for key in keys:
        value = as_float(position.get(key))
        if value > 0:
            return value
    return 0.0


def _position_notional(position: Dict[str, Any]) -> float:
    notional = as_float(
        position.get("notional_usdt")
        or position.get("notionalUsdt")
        or position.get("notional")
        or position.get("value")
    )
    if notional > 0:
        return abs(notional)
    mark = _position_price(position, "mark_price", "markPrice", "markPx", "last")
    entry = _position_price(position, "entry_price", "entryPrice", "avgPx")
    return abs(_position_size(position) * (mark or entry))


def _order_symbol(order: Dict[str, Any]) -> str:
    return str(order.get("symbol") or order.get("instrument_id") or order.get("instrumentId") or order.get("instId") or "")


def _order_source_id(order: Dict[str, Any]) -> int:
    try:
        return int(order.get("source_strategy_id") or order.get("sourceStrategyId") or 0)
    except (TypeError, ValueError):
        return 0


class LiveProfitPushService:
    """Build and push Feishu profit cards for QuantBase live execution accounts."""

    def __init__(
        self,
        *,
        database: Any = db,
        account_service: Any = live_account_service,
        live_execution_service: Any = live_signal_execution_service,
        trading_service: Any = trading_service,
        notifier: Any = feishu_notifier,
        now_fn: Callable[[], datetime] = utcnow,
    ) -> None:
        self.db = database
        self.account_service = account_service
        self.live_execution_service = live_execution_service
        self.trading_service = trading_service
        self.notifier = notifier
        self.now_fn = now_fn
        self._run_lock = asyncio.Lock()

    def get_config(self) -> Dict[str, Any]:
        cfg = self.db.get_live_profit_push_config()
        cfg["interval_minutes"] = clamp_interval_minutes(cfg.get("interval_minutes"))
        cfg["notify_ready"] = self._notify_ready()
        return cfg

    def update_config(self, updates: Dict[str, Any]) -> Dict[str, Any]:
        payload: Dict[str, Any] = {}
        if "enabled" in updates and updates.get("enabled") is not None:
            payload["enabled"] = bool(updates.get("enabled"))
        if "interval_minutes" in updates and updates.get("interval_minutes") is not None:
            payload["interval_minutes"] = clamp_interval_minutes(updates.get("interval_minutes"))
        cfg = self.db.update_live_profit_push_config(payload)
        cfg["notify_ready"] = self._notify_ready()
        return cfg

    def _notify_ready(self) -> bool:
        is_ready = getattr(self.notifier, "is_ready", None)
        if not callable(is_ready):
            return False
        try:
            return bool(is_ready(require_enabled=False))
        except TypeError:
            return bool(is_ready())

    async def run_due(self) -> Dict[str, Any]:
        cfg = self.get_config()
        if not bool(cfg.get("enabled")):
            return {"started": False, "skipped": "disabled"}

        interval = clamp_interval_minutes(cfg.get("interval_minutes"))
        now = self.now_fn()
        last_attempt = parse_dt(cfg.get("last_finished_at") or cfg.get("last_sent_at"))
        if last_attempt and (now - last_attempt) < timedelta(minutes=interval):
            return {"started": False, "skipped": "not_due"}

        return await self.run_once(force=False, config=cfg)

    async def run_once(self, *, force: bool = False, config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if self._run_lock.locked():
            return {"started": False, "running": True, "message": "实盘收益卡片推送正在运行"}

        async with self._run_lock:
            cfg = config or self.get_config()
            if not force and not bool(cfg.get("enabled")):
                return {"started": False, "running": False, "skipped": "disabled"}

            started_at = iso(self.now_fn())
            self.db.set_live_profit_push_runtime(
                running=True,
                last_started_at=started_at,
                last_error=None,
                last_skip_reason=None,
            )
            try:
                snapshot = await self.build_snapshot()
                if int(snapshot.get("running_count") or 0) <= 0:
                    self.db.set_live_profit_push_runtime(
                        running=False,
                        last_finished_at=iso(self.now_fn()),
                        last_error=None,
                        last_skip_reason="no_live_positions",
                    )
                    return {
                        "started": True,
                        "running": False,
                        "sent": False,
                        "skipped": "no_live_positions",
                    }

                sent = await self.notifier.notify_strategy_profit_report(snapshot)
                finished_at = iso(self.now_fn())
                if sent:
                    self.db.set_live_profit_push_runtime(
                        running=False,
                        last_sent_at=finished_at,
                        last_finished_at=finished_at,
                        last_error=None,
                        last_skip_reason=None,
                    )
                else:
                    self.db.set_live_profit_push_runtime(
                        running=False,
                        last_finished_at=finished_at,
                        last_error="飞书推送未启用、Webhook 未配置或发送失败",
                        last_skip_reason=None,
                    )
                return {
                    "started": True,
                    "running": False,
                    "sent": bool(sent),
                    "running_count": snapshot.get("running_count", 0),
                    "total_pnl": snapshot.get("total_pnl", 0),
                    "total_return_pct": snapshot.get("total_return_pct", 0),
                }
            except Exception as exc:
                self.db.set_live_profit_push_runtime(
                    running=False,
                    last_finished_at=iso(self.now_fn()),
                    last_error=str(exc),
                    last_skip_reason=None,
                )
                raise

    async def build_snapshot(self) -> Dict[str, Any]:
        accounts = [
            account
            for account in (self.account_service.list_accounts() or [])
            if account.get("enabled") and account.get("configured")
        ]
        subscriptions = list(
            self.live_execution_service.list_subscriptions(
                statuses=sorted(getattr(self.live_execution_service, "ACTIVE_STATUSES", {"running", "deployed"}))
            )
            or []
        )
        rows_by_strategy = {
            int(sub.get("source_strategy_id") or 0): self.db.get_strategy_by_id(int(sub.get("source_strategy_id") or 0))
            for sub in subscriptions
            if int(sub.get("source_strategy_id") or 0) > 0
        }
        account_payloads = await asyncio.gather(
            *[self._load_account_payload(account) for account in accounts],
            return_exceptions=True,
        )

        positions_by_account: Dict[str, List[Dict[str, Any]]] = {}
        orders_by_account: Dict[str, List[Dict[str, Any]]] = {}
        equity_by_account: Dict[str, float] = {}
        balance_by_account: Dict[str, float] = {}
        for account, payload in zip(accounts, account_payloads):
            account_id = str(account.get("account_id") or "default")
            if isinstance(payload, Exception):
                positions_by_account[account_id] = []
                orders_by_account[account_id] = []
                equity_by_account[account_id] = 0.0
                balance_by_account[account_id] = 0.0
                continue
            positions_by_account[account_id] = payload.get("positions") or []
            orders_by_account[account_id] = payload.get("orders") or []
            equity_by_account[account_id] = as_float(payload.get("equity"))
            balance_by_account[account_id] = as_float(payload.get("balance"))

        strategies: List[Dict[str, Any]] = []
        for sub in subscriptions:
            strategy_id = int(sub.get("source_strategy_id") or 0)
            account_id = str(sub.get("account_id") or "default")
            row = rows_by_strategy.get(strategy_id)
            symbols = _extract_symbols(row)
            related_positions = self._filter_positions(positions_by_account.get(account_id, []), symbols)
            related_orders = [
                order
                for order in orders_by_account.get(account_id, [])
                if _order_source_id(order) == strategy_id
                or (symbols and _upper_symbol(_order_symbol(order)) in {_upper_symbol(s) for s in symbols})
            ]
            if not related_positions and not related_orders:
                continue
            account_meta = next((item for item in accounts if str(item.get("account_id") or "") == account_id), {})
            strategies.append(
                self._normalize_live_strategy(
                    strategy_id=strategy_id,
                    row=row,
                    account=account_meta,
                    positions=related_positions,
                    orders=related_orders,
                    account_equity=equity_by_account.get(account_id, 0.0),
                    account_balance=balance_by_account.get(account_id, 0.0),
                )
            )

        if not strategies and accounts:
            for account in accounts:
                account_id = str(account.get("account_id") or "default")
                positions = positions_by_account.get(account_id, [])
                orders = orders_by_account.get(account_id, [])
                if not positions and not orders:
                    continue
                strategies.append(
                    self._normalize_live_strategy(
                        strategy_id=0,
                        row=None,
                        account=account,
                        positions=positions,
                        orders=orders,
                        account_equity=equity_by_account.get(account_id, 0.0),
                        account_balance=balance_by_account.get(account_id, 0.0),
                    )
                )

        strategies.sort(key=lambda item: as_float(item.get("pnl")), reverse=True)
        total_equity = sum(as_float(item.get("equity")) for item in strategies)
        total_unrealized = sum(as_float(item.get("unrealized_pnl")) for item in strategies)
        total_pnl = sum(as_float(item.get("pnl")) for item in strategies)
        total_position_notional = sum(as_float(item.get("position_notional_usdt")) for item in strategies)
        total_trades = sum(int(item.get("total_trades") or 0) for item in strategies)

        total_return_pct = 0.0
        if total_equity > 0 and math.isfinite(total_equity):
            total_return_pct = total_pnl / total_equity * 100

        return {
            "report_scope": "live",
            "title": "实盘收益卡片",
            "generated_at": iso(self.now_fn()),
            "running_count": len(strategies),
            "total_equity": round(total_equity, 6),
            "total_initial_capital": round(max(total_equity - total_pnl, 0), 6),
            "total_pnl": round(total_pnl, 6),
            "total_unrealized_pnl": round(total_unrealized, 6),
            "total_return_pct": round(total_return_pct, 6),
            "total_position_notional_usdt": round(total_position_notional, 6),
            "position_strategy_count": sum(1 for item in strategies if as_float(item.get("position_notional_usdt")) > 1e-6),
            "total_trades": total_trades,
            "closing_trades": 0,
            "winning_trades": 0,
            "gross_profit": 0,
            "gross_loss": 0,
            "win_rate": 0,
            "profit_factor": 0,
            "active_alerts": 0,
            "total_alerts": 0,
            "strategies": strategies,
        }

    async def _load_account_payload(self, account: Dict[str, Any]) -> Dict[str, Any]:
        account_id = str(account.get("account_id") or "default")
        exchange = self.account_service.exchange_alias_for_account(account_id)
        positions_raw, balance_detail, orders_raw = await asyncio.gather(
            self.trading_service.get_positions(exchange, None),
            self.trading_service.get_balance_detail(exchange),
            self.trading_service.get_order_history(exchange, None, 100),
        )
        positions = [self._normalize_position(item) for item in positions_raw or []]
        positions = [item for item in positions if item.get("size", 0) > 1e-12]
        orders = self.live_execution_service.enrich_orders_with_attribution(
            account_id=account_id,
            orders=list(orders_raw or []),
        )
        trading_balances = list((balance_detail or {}).get("trading") or [])
        usdt = next((item for item in trading_balances if str(item.get("currency") or "").upper() == "USDT"), {})
        return {
            "positions": positions,
            "orders": list(orders or []),
            "equity": as_float(usdt.get("total")),
            "balance": as_float(usdt.get("free")),
        }

    @staticmethod
    def _normalize_position(position: Dict[str, Any]) -> Dict[str, Any]:
        entry = _position_price(position, "entry_price", "entryPrice", "avgPx")
        mark = _position_price(position, "mark_price", "markPrice", "markPx", "last") or entry
        upnl = as_float(position.get("unrealized_pnl") or position.get("unrealizedPnl") or position.get("upl"))
        return {
            "symbol": _position_symbol(position),
            "side": _position_side(position),
            "size": round(_position_size(position), 10),
            "entry_price": round(entry, 10),
            "mark_price": round(mark, 10),
            "notional_usdt": round(_position_notional(position), 6),
            "unrealized_pnl": round(upnl, 6),
        }

    @staticmethod
    def _filter_positions(positions: List[Dict[str, Any]], symbols: List[str]) -> List[Dict[str, Any]]:
        if not symbols:
            return list(positions)
        symbol_set = {_upper_symbol(symbol) for symbol in symbols}
        bases = {symbol.split("/")[0].upper() for symbol in symbols if "/" in symbol}
        out = []
        for position in positions:
            symbol = _upper_symbol(position.get("symbol"))
            if symbol in symbol_set or any(symbol.startswith(f"{base}/") or symbol.startswith(f"{base}-") for base in bases):
                out.append(position)
        return out

    def _normalize_live_strategy(
        self,
        *,
        strategy_id: int,
        row: Optional[Dict[str, Any]],
        account: Dict[str, Any],
        positions: List[Dict[str, Any]],
        orders: List[Dict[str, Any]],
        account_equity: float,
        account_balance: float,
    ) -> Dict[str, Any]:
        position_notional = sum(as_float(item.get("notional_usdt")) for item in positions)
        unrealized = sum(as_float(item.get("unrealized_pnl")) for item in positions)
        realized = sum(as_float(order.get("pnl")) for order in orders)
        total_pnl = unrealized + realized
        equity = account_equity or max(position_notional + account_balance, 0)
        return_pct = (total_pnl / equity * 100) if equity > 0 else 0.0
        symbols = _extract_symbols(row) or sorted({_position_symbol(item) for item in positions if _position_symbol(item)})
        name = str(row.get("name") or "") if row else ""
        if not name:
            name = f"实盘账户 {account.get('name') or account.get('account_id') or 'default'}"
        return {
            "strategy_id": int(strategy_id),
            "name": name,
            "status": "live",
            "exchange": "okx",
            "account_id": str(account.get("account_id") or "default"),
            "account_name": str(account.get("name") or account.get("account_id") or "default"),
            "symbols": symbols,
            "pnl": round(total_pnl, 6),
            "return_pct": round(return_pct, 6),
            "equity": round(equity, 6),
            "initial_capital": round(max(equity - total_pnl, 0), 6),
            "balance": round(account_balance, 6),
            "unrealized_pnl": round(unrealized, 6),
            "total_trades": len(orders),
            "closing_trades": 0,
            "winning_trades": 0,
            "gross_profit": 0,
            "gross_loss": 0,
            "win_rate": 0,
            "profit_factor": 0,
            "position_notional_usdt": round(position_notional, 6),
            "positions_count": len(positions),
            "positions": positions,
        }


live_profit_push_service = LiveProfitPushService()
