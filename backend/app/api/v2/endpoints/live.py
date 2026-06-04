"""
Live trading orchestration — v2 `/live/*` used by LiveTrading.tsx.

与单策略引擎 `strategy_engine` 对齐：configure 写入 strategies 表后 start 启动对应 ID。
"""
from __future__ import annotations

import asyncio
import copy
import json
import logging
import math
import os
import time
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional

from fastapi import APIRouter, Body, Query
from pydantic import BaseModel, ConfigDict, Field

from app.core.config import settings
from app.core.contracts import ok
from app.core.errors import BadRequestError, NotFoundError
from app.db.local_db import db_instance as db
from app.domain.funding import funding_domain_service
from app.domain.market import market_domain_service
from app.exchange import exchange_manager
from app.services.strategy_engine import LiveContractBroker, strategy_engine
from app.services.strategy_log_store import strategy_log_store
from app.services.feishu_notifier import feishu_notifier
from app.services import live_account_service
from app.services.live_signal_execution_service import live_signal_execution_service
from app.services.contract_paper_account import load_contract_instruments, normalize_contract_symbol
from app.services.strategy_registry import resolve_unified_base_strategy_class
from app.services.telegram_notifier import telegram_notifier
from app.services.trading_service import trading_service

logger = logging.getLogger(__name__)

router = APIRouter()

# 最近一次 Live 配置对应的策略 ID（configure / start 成功后会设置）
_active_strategy_id: Optional[int] = None

# 权益曲线采样：strategy_id -> 时间序列
_equity_curve_samples: Dict[int, List[Dict[str, Any]]] = {}
_EQUITY_MAX = 400

_SUPERPNL_STRATEGY_KEY = "superpnl_15m_low_turnover"
_SUPERPNL_DEFAULT_SYMBOLS = [
    "BTC/USDT",
    "ETH/USDT",
    "DOGE/USDT",
    "SOL/USDT",
    "XRP/USDT",
    "PEPE/USDT",
    "TRX/USDT",
    "XAUT/USDT",
    "BIO/USDT",
    "PENGU/USDT",
    "PI/USDT",
    "ZKJ/USDT",
    "TRUMP/USDT",
    "SUI/USDT",
    "FIL/USDT",
    "ADA/USDT",
    "APE/USDT",
    "CHZ/USDT",
    "LINK/USDT",
    "LTC/USDT",
]

_LIVE_PRIVATE_READ_CACHE_TTL_SEC = 3.0
_LIVE_ASSET_PRIVATE_READ_CACHE_TTL_SEC = 60.0
_live_private_read_cache: Dict[tuple[Any, ...], tuple[float, Any]] = {}
_live_private_read_inflight: Dict[tuple[Any, ...], asyncio.Task[Any]] = {}


def _clone_live_private_read(value: Any) -> Any:
    return copy.deepcopy(value)


def _clear_live_private_read_cache(exchange: Optional[str] = None) -> None:
    if not exchange:
        _live_private_read_cache.clear()
        return
    for key in list(_live_private_read_cache):
        if len(key) > 1 and key[1] == exchange:
            _live_private_read_cache.pop(key, None)


async def _cached_live_private_read(
    key: tuple[Any, ...],
    loader: Callable[[], Awaitable[Any]],
    *,
    ttl_sec: float = _LIVE_PRIVATE_READ_CACHE_TTL_SEC,
) -> Any:
    now = time.monotonic()
    cached = _live_private_read_cache.get(key)
    if cached is not None:
        cached_at, value = cached
        if now - cached_at <= ttl_sec:
            return _clone_live_private_read(value)
        _live_private_read_cache.pop(key, None)

    inflight = _live_private_read_inflight.get(key)
    if inflight is not None and not inflight.done():
        return _clone_live_private_read(await inflight)

    task = asyncio.create_task(loader())
    _live_private_read_inflight[key] = task
    try:
        value = await task
    finally:
        if _live_private_read_inflight.get(key) is task:
            _live_private_read_inflight.pop(key, None)
    if len(_live_private_read_cache) > 128:
        _live_private_read_cache.clear()
    _live_private_read_cache[key] = (time.monotonic(), _clone_live_private_read(value))
    return _clone_live_private_read(value)


async def _cached_live_positions(exchange: str, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
    return await _cached_live_private_read(
        ("positions", exchange, symbol or ""),
        lambda: trading_service.get_positions(exchange, symbol),
    )


async def _cached_live_balance(exchange: str) -> List[Dict[str, Any]]:
    return await _cached_live_private_read(
        ("balance", exchange),
        lambda: trading_service.get_balance(exchange),
        ttl_sec=_LIVE_ASSET_PRIVATE_READ_CACHE_TTL_SEC,
    )


async def _cached_live_balance_detail(exchange: str) -> Dict[str, Any]:
    return await _cached_live_private_read(
        ("balance_detail", exchange),
        lambda: trading_service.get_balance_detail(exchange),
        ttl_sec=_LIVE_ASSET_PRIVATE_READ_CACHE_TTL_SEC,
    )


async def _cached_live_return_rates(exchange: str) -> Dict[str, Any]:
    return await _cached_live_private_read(
        ("return_rates", exchange),
        lambda: trading_service.get_account_return_rates(exchange),
        ttl_sec=_LIVE_ASSET_PRIVATE_READ_CACHE_TTL_SEC,
    )


async def _cached_live_open_orders(exchange: str, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
    return await _cached_live_private_read(
        ("open_orders", exchange, symbol or ""),
        lambda: trading_service.get_open_orders(exchange, symbol),
    )


async def _cached_live_order_history(
    exchange: str,
    symbol: Optional[str] = None,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    return await _cached_live_private_read(
        ("order_history", exchange, symbol or "", int(limit)),
        lambda: trading_service.get_order_history(exchange, symbol, limit),
    )


class LiveConfigureBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    exchange: str = "okx"
    strategy_type: str = Field(..., description="策略 ID（与前端 selectedStrategy 一致）")
    symbol: Optional[str] = None
    timeframe: Optional[str] = Field(
        default=None,
        description="兼容旧客户端；运行周期始终从策略定义 config.timeframe 读取",
    )
    initial_equity: float = 1000
    dry_run: bool = True
    loop_interval: Optional[int] = None
    risk_config: Optional[Dict[str, Any]] = None


class PreFlightBody(BaseModel):
    strategy: str
    exchange: str = "okx"
    symbol: Optional[str] = None
    timeframe: Optional[str] = Field(
        default=None,
        description="兼容旧客户端；飞行检查周期始终从策略定义 config.timeframe 读取",
    )
    dry_run: bool = True
    capital_pct: Optional[float] = None
    total_capital: Optional[float] = None


class TelegramTestBody(BaseModel):
    message: str = "QuantBase test"


class LiveInstanceBody(BaseModel):
    """可选 body：显式指定策略实例 ID，供多实例控制台调用。"""

    model_config = ConfigDict(populate_by_name=True)
    instance_id: Optional[int] = None
    clear_metrics: bool = False


class PaperPositionCloseBody(BaseModel):
    """模拟盘持仓平仓请求；只允许作用于 paper broker。"""

    model_config = ConfigDict(populate_by_name=True)
    instance_id: Optional[int] = None
    symbol: str
    side: Optional[str] = None
    market_type: Optional[str] = None


class PromoteToLiveBody(BaseModel):
    """模拟转实盘请求；/promote 保持旧克隆兼容，/live-real 使用订阅执行。"""

    model_config = ConfigDict(populate_by_name=True)

    source_strategy_id: int = Field(..., ge=1)
    account_id: Optional[str] = "default"
    exchange: str = "okx"
    initial_equity: Optional[float] = Field(None, gt=0)
    loop_interval: int = Field(60, ge=5)
    start_immediately: bool = True
    confirm_paper_reviewed: bool = False
    confirm_live_risk: bool = False
    risk_config: Optional[Dict[str, Any]] = None


class LiveStrategySettingBody(BaseModel):
    """实盘工作台中间列表设置；加入不会触发真实下单。"""

    model_config = ConfigDict(populate_by_name=True)

    added: Optional[bool] = True
    account_id: Optional[str] = "default"
    bind_account: Optional[bool] = None
    risk_config: Optional[Dict[str, Any]] = None


class LiveStrategyPreflightBody(BaseModel):
    """实盘工作台预检配置。"""

    model_config = ConfigDict(populate_by_name=True)

    account_id: Optional[str] = "default"
    exchange: str = "okx"
    initial_equity: Optional[float] = Field(None, gt=0)
    loop_interval: int = Field(60, ge=5)
    start_immediately: bool = True
    risk_config: Optional[Dict[str, Any]] = None


class LiveStrategyDeployBody(LiveStrategyPreflightBody):
    """实盘工作台部署配置。"""

    confirm_paper_reviewed: bool = False
    confirm_live_risk: bool = False


class LiveStrategySubscriptionControlBody(BaseModel):
    """实盘订阅控制配置；只影响实盘分发，不影响源模拟策略。"""

    model_config = ConfigDict(populate_by_name=True)
    account_id: Optional[str] = "default"


class LiveAccountCreateBody(BaseModel):
    """新增 OKX 实盘账户。密钥只保存于服务端。"""

    model_config = ConfigDict(populate_by_name=True)

    name: str
    api_key: str
    api_secret: str
    passphrase: Optional[str] = None
    testnet: bool = False


class LivePositionCloseBody(BaseModel):
    """实盘持仓平仓请求。"""

    model_config = ConfigDict(populate_by_name=True)

    symbol: Optional[str] = None
    side: Optional[str] = None
    close_all: bool = False
    confirm_live_risk: bool = False


def _parse_strategy_id(raw: str) -> int:
    s = str(raw).strip()
    if not s.isdigit():
        raise BadRequestError("strategy_type / strategy 必须是数字策略 ID")
    return int(s)


def _resolve_target_strategy_id() -> Optional[int]:
    if _active_strategy_id is not None:
        return _active_strategy_id
    ids = strategy_engine.list_running_or_paused_ids()
    return ids[0] if ids else None


def _resolve_instance_sid(
    body: Optional[LiveInstanceBody],
    *,
    query_id: Optional[int] = None,
) -> Optional[int]:
    if query_id is not None:
        return int(query_id)
    if body is not None and body.instance_id is not None:
        return int(body.instance_id)
    return _resolve_target_strategy_id()


def _is_superpnl_strategy(row: Dict[str, Any], cfg: Dict[str, Any]) -> bool:
    strategy_key = str(
        cfg.get("strategy_key")
        or row.get("strategy_key")
        or ""
    ).strip()
    name = str(row.get("name") or "")
    return strategy_key == _SUPERPNL_STRATEGY_KEY or "SuperPnL" in name


def _row_symbols(row: Dict[str, Any]) -> List[str]:
    raw = row.get("symbols") or []
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except Exception:
            parsed = [raw]
        raw = parsed
    if not isinstance(raw, list):
        return []
    return [str(s).strip() for s in raw if str(s).strip()]


def _config_symbols(cfg: Dict[str, Any]) -> List[str]:
    for key in ("symbols", "strategy_symbols"):
        symbols = _row_symbols({"symbols": cfg.get(key)})
        if symbols:
            return symbols
    return []


def _defined_symbols(
    row: Dict[str, Any],
    cfg: Dict[str, Any],
    fallback_symbol: Optional[str] = None,
) -> List[str]:
    symbols = _row_symbols(row)
    if not symbols:
        symbols = _config_symbols(cfg)
    if _is_superpnl_strategy(row, cfg) and len(symbols) < 2:
        symbols = list(_SUPERPNL_DEFAULT_SYMBOLS)
    if not symbols and fallback_symbol:
        symbols = [str(fallback_symbol).strip()]
    return [s for s in symbols if s]


def _runtime_strategy_symbols(strategy_cls: Any, exchange_name: str, cfg: Dict[str, Any]) -> List[str]:
    resolver = getattr(strategy_cls, "resolve_runtime_symbols", None)
    if not callable(resolver):
        return []
    try:
        raw = resolver(exchange_name, cfg)
    except Exception as exc:
        logger.warning("动态策略运行币池解析失败 %s: %s", getattr(strategy_cls, "__name__", strategy_cls), exc)
        return []
    if isinstance(raw, str):
        values = [raw]
    elif isinstance(raw, (list, tuple, set)):
        values = list(raw)
    else:
        return []
    return _row_symbols({"symbols": values})


def _configured_symbols(
    row: Dict[str, Any],
    cfg: Dict[str, Any],
    selected_symbol: Optional[str] = None,
) -> List[str]:
    symbols = _defined_symbols(row, cfg, selected_symbol)
    if not symbols:
        symbols = ["BTC/USDT"]
    cfg.pop("selected_symbol", None)
    cfg["symbol_scope"] = (
        "superpnl_top20_universe" if _is_superpnl_strategy(row, cfg) else "strategy_symbols"
    )
    return symbols


def _strategy_defined_timeframe(
    row: Optional[Dict[str, Any]],
    cfg: Optional[Dict[str, Any]] = None,
    *,
    fallback: str = "1m",
) -> str:
    """Resolve execution timeframe from the strategy definition, never from launch UI input."""
    source_cfg: Dict[str, Any] = {}
    if isinstance(cfg, dict):
        source_cfg = cfg
    elif row:
        raw_cfg = row.get("config") or {}
        if isinstance(raw_cfg, str):
            try:
                raw_cfg = json.loads(raw_cfg)
            except Exception:
                raw_cfg = {}
        if isinstance(raw_cfg, dict):
            source_cfg = raw_cfg

    raw = source_cfg.get("timeframe") if isinstance(source_cfg, dict) else None
    if not raw and row:
        raw = row.get("timeframe")
    value = str(raw or fallback).strip()
    return value or fallback


def _uptime_str(started_at: Optional[str]) -> str:
    if not started_at:
        return "-"
    try:
        dt = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - dt
        return _format_duration_seconds(int(delta.total_seconds()))
    except Exception:
        return "-"


def _format_duration_seconds(seconds: int) -> str:
    seconds = max(0, int(seconds))
    total_minutes = seconds // 60
    hours, minute = divmod(total_minutes, 60)
    days, hour = divmod(hours, 24)
    if days:
        return f"{days}D {hour}H {minute}M"
    if hour:
        return f"{hour}H {minute}M"
    return f"{minute}M"


def _paper_positions_from_status(st: Optional[Dict[str, Any]]) -> tuple[List[Dict[str, Any]], float]:
    """从引擎 PaperBroker 状态得到持仓列表与浮动盈亏合计。"""
    if not st:
        return [], 0.0
    unrealized_total = float(st.get("unrealized_pnl") or 0)
    raw = st.get("positions") or {}
    if not isinstance(raw, dict):
        return [], unrealized_total
    out: List[Dict[str, Any]] = []
    for psym, pos in raw.items():
        if not isinstance(pos, dict):
            continue
        try:
            contracts = float(pos.get("contracts") or 0)
        except (TypeError, ValueError):
            contracts = 0.0
        try:
            base_qty = float(pos.get("base_qty") or 0)
        except (TypeError, ValueError):
            base_qty = 0.0
        try:
            sz = float(pos.get("size") or base_qty or contracts or 0)
        except (TypeError, ValueError):
            sz = 0.0
        if max(sz, contracts, base_qty) <= 1e-12:
            continue
        upnl = float(pos.get("unrealized_pnl") or 0)
        out.append(
            {
                "symbol": str(pos.get("symbol") or psym),
                "side": str(pos.get("side") or "long"),
                "pos_side": pos.get("pos_side"),
                "size": sz,
                "contracts": contracts,
                "base_qty": base_qty,
                "notional_usdt": pos.get("notional_usdt"),
                "margin": pos.get("margin"),
                "leverage": pos.get("leverage"),
                "liq_price": pos.get("liq_price"),
                "funding_fee": pos.get("funding_fee"),
                "realized_pnl": pos.get("realized_pnl"),
                "entry_price": float(pos.get("entry_price") or 0),
                "mark_price": float(pos.get("mark_price") or 0),
                "unrealized_pnl": upnl,
                "unrealized_pnl_pct": None,
            }
        )
    return out, unrealized_total


def _normalize_ccxt_positions(positions: List[Dict[str, Any]]) -> tuple[List[Dict[str, Any]], float]:
    """将 CCXT fetch_positions 结果转为统一结构，并汇总未实现盈亏。"""
    out: List[Dict[str, Any]] = []
    total_upnl = 0.0
    for p in positions:
        if not p:
            continue
        try:
            contracts = p.get("contracts")
            if contracts is None:
                contracts = p.get("contractsSize")
            c = float(contracts or 0)
        except (TypeError, ValueError):
            c = 0.0
        if abs(c) < 1e-12:
            continue
        side = str(p.get("side") or "").lower()
        if side not in ("long", "short"):
            side = "long" if c >= 0 else "short"
        sym = str(p.get("symbol") or "")
        try:
            entry = float(p.get("entryPrice") or p.get("entry_price") or 0)
        except (TypeError, ValueError):
            entry = 0.0
        try:
            mark = float(p.get("markPrice") or p.get("mark_price") or entry)
        except (TypeError, ValueError):
            mark = entry
        try:
            upnl = float(p.get("unrealizedPnl") or p.get("unrealized_pnl") or 0)
        except (TypeError, ValueError):
            upnl = 0.0
        total_upnl += upnl
        pct_raw = p.get("percentage")
        try:
            pct_f = float(pct_raw) if pct_raw is not None else None
        except (TypeError, ValueError):
            pct_f = None
        out.append(
            {
                "symbol": sym,
                "side": side,
                "size": abs(c),
                "entry_price": entry,
                "mark_price": mark,
                "unrealized_pnl": upnl,
                "unrealized_pnl_pct": pct_f,
            }
        )
    return out, total_upnl


def _spot_positions_from_balances(
    balances: List[Dict[str, Any]],
    *,
    exchange_name: str,
    symbol: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Represent non-USDT spot balances as read-only live account holdings."""
    requested_base = ""
    if symbol:
        requested_base = str(symbol).replace("-", "/").split("/", 1)[0].strip().upper()

    out: List[Dict[str, Any]] = []
    for row in balances or []:
        currency = str(row.get("currency") or "").strip().upper()
        if not currency or currency == "USDT":
            continue
        if requested_base and requested_base != currency:
            continue
        total = _float_value(row.get("total"), 0.0)
        free = _float_value(row.get("free"), 0.0)
        used = _float_value(row.get("used"), 0.0)
        if max(abs(total), abs(free), abs(used)) <= 1e-12:
            continue
        notional = _float_value(
            row.get("notional_usdt")
            or row.get("notional")
            or row.get("equity_usd")
            or row.get("usd_value"),
            0.0,
        )
        out.append(
            {
                "exchange": exchange_name,
                "symbol": f"{currency}/USDT",
                "currency": currency,
                "asset_type": "spot",
                "side": "spot",
                "pos_side": "spot",
                "amount": total,
                "free": free,
                "used": used,
                "notional_usdt": notional if notional > 0 else None,
                "unrealized_pnl": None,
            }
        )
    return out


def _refresh_paper_marks(strategy_id: int, exchange_name: str, symbols: List[str]) -> None:
    """模拟盘展示前用最新 ticker 刷新持仓标记价。"""
    if not symbols:
        return
    ex = exchange_manager.get_exchange(exchange_name or "okx")
    if not ex:
        return
    prices: Dict[str, float] = {}
    for sym in symbols:
        try:
            ticker = ex.fetch_ticker(sym)
            px = float(ticker.get("last") or 0)
        except Exception as e:
            logger.debug("refresh paper mark failed: %s %s", sym, e)
            continue
        if px > 0:
            prices[sym] = px
    if prices:
        strategy_engine.refresh_paper_marks(strategy_id, prices)


def _position_symbols_from_status(st: Optional[Dict[str, Any]]) -> List[str]:
    if not st:
        return []
    raw = st.get("positions") or {}
    if not isinstance(raw, dict):
        return []
    out: List[str] = []
    for sym, pos in raw.items():
        if not isinstance(pos, dict):
            continue
        try:
            size = float(pos.get("size") or 0)
        except (TypeError, ValueError):
            size = 0.0
        symbol = str(pos.get("symbol") or sym).strip()
        if abs(size) > 1e-12 and symbol:
            out.append(symbol)
    return out


def _feishu_dashboard_slice() -> Dict[str, Any]:
    return {
        "enabled": bool(feishu_notifier.is_ready()),
        "webhook_configured": bool(feishu_notifier.has_webhook()),
        "messages_sent": len(getattr(feishu_notifier, "_history", []) or []),
    }


def _append_equity_sample(strategy_id: int, ts: float, equity: float) -> None:
    if equity <= 0:
        return
    ts_ms = int(ts * 1000)
    try:
        if hasattr(db, "insert_strategy_equity_sample"):
            db.insert_strategy_equity_sample(
                strategy_id,
                ts_ms,
                equity,
                source="dashboard",
            )
    except Exception as exc:
        logger.debug("Persist equity sample failed for %s: %s", strategy_id, exc)
    seq = _equity_curve_samples.setdefault(strategy_id, [])
    sample = {
        "timestamp": ts_ms,
        "equity": equity,
        "time": datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).isoformat(),
    }
    if seq and int(seq[-1].get("timestamp") or 0) == ts_ms:
        seq[-1] = sample
    else:
        seq.append(sample)
    if len(seq) > _EQUITY_MAX:
        del seq[: len(seq) - _EQUITY_MAX]


def _load_persisted_equity_samples(strategy_id: int) -> List[Dict[str, Any]]:
    if not hasattr(db, "get_strategy_equity_samples"):
        return _equity_curve_samples.get(strategy_id, [])
    try:
        rows = db.get_strategy_equity_samples(strategy_id, _EQUITY_MAX)
    except Exception as exc:
        logger.debug("Load persisted equity samples failed for %s: %s", strategy_id, exc)
        return _equity_curve_samples.get(strategy_id, [])
    if rows:
        _equity_curve_samples[strategy_id] = list(rows)
        return _equity_curve_samples[strategy_id]
    return _equity_curve_samples.get(strategy_id, [])


def _float_value(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(out):
        return default
    return out


def _timeframe_seconds(timeframe: str) -> int:
    value = str(timeframe or "").strip().lower()
    if not value:
        return 60
    unit = value[-1]
    amount = _float_value(value[:-1], 1.0)
    if amount <= 0:
        amount = 1.0
    if unit == "m":
        return int(amount * 60)
    if unit == "h":
        return int(amount * 3600)
    if unit == "d":
        return int(amount * 86400)
    return int(amount)


def _kline_timestamp_ms(bar: Any) -> Optional[int]:
    raw: Any = None
    if isinstance(bar, dict):
        raw = bar.get("timestamp")
    elif isinstance(bar, (list, tuple)) and bar:
        raw = bar[0]
    ts = _float_value(raw, 0.0)
    if ts <= 0:
        return None
    if ts < 1_000_000_000_000:
        ts *= 1000
    return int(ts)


def _configured_initial_capital(cfg: Dict[str, Any]) -> float:
    for key in ("initial_capital", "initialCapital", "initial_equity", "initialEquity"):
        value = _float_value(cfg.get(key), 0.0)
        if value > 0:
            return value
    return 10000.0


def _git_commit_ref() -> str:
    return (
        os.getenv("GITHUB_SHA")
        or os.getenv("QUANTBASE_BUILD_COMMIT")
        or os.getenv("COMMIT_SHA")
        or "unknown"
    )


def _unique_strategy_name(base_name: str) -> str:
    existing_names = {str(item.get("name") or "") for item in db.get_strategies()}
    if base_name not in existing_names:
        return base_name
    suffix = 2
    while f"{base_name} #{suffix}" in existing_names:
        suffix += 1
    return f"{base_name} #{suffix}"


def _cap_fraction(value: Any, default: float, cap: float) -> float:
    raw = _float_value(value, default)
    if raw > 1:
        raw = raw / 100.0
    if raw <= 0:
        raw = default
    return max(0.0, min(raw, cap))


def _build_promoted_live_config(
    source_row: Dict[str, Any],
    *,
    initial_equity: float,
    loop_interval: int,
    risk_config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    source_cfg = source_row.get("config") or {}
    if not isinstance(source_cfg, dict):
        source_cfg = {}
    cfg = dict(source_cfg)
    risk = risk_config or {}
    now = datetime.now(timezone.utc).isoformat()
    cfg["is_paper_trading"] = False
    cfg["initial_capital"] = float(initial_equity)
    cfg["loop_interval_sec"] = int(loop_interval)
    cfg["risk_per_trade_pct"] = _cap_fraction(risk.get("risk_per_trade_pct"), 0.005, 0.01)
    cfg["max_daily_loss_pct"] = _cap_fraction(risk.get("max_daily_loss_pct"), 0.01, 0.02)
    cfg["max_total_loss_pct"] = _cap_fraction(risk.get("max_total_loss_pct"), 0.03, 0.05)

    if "max_position_pct" in cfg:
        cfg["max_position_pct"] = _cap_fraction(cfg.get("max_position_pct"), 0.02, 0.03)
    if "max_total_position_pct" in cfg:
        cfg["max_total_position_pct"] = _cap_fraction(cfg.get("max_total_position_pct"), 0.05, 0.08)
    if "max_position_per_symbol" in cfg:
        cfg["max_position_per_symbol"] = _cap_fraction(cfg.get("max_position_per_symbol"), 0.02, 0.03)
    if "max_total_position" in cfg:
        cfg["max_total_position"] = _cap_fraction(cfg.get("max_total_position"), 0.05, 0.08)
    if "entry_equity_pct" in cfg:
        cfg["entry_equity_pct"] = _cap_fraction(cfg.get("entry_equity_pct"), 0.01, 0.02)
    if "entry_quote_usdt" in cfg:
        cfg["entry_quote_usdt"] = min(_float_value(cfg.get("entry_quote_usdt"), 10.0), max(5.0, initial_equity * 0.02))
    if "quote_per_order" in cfg:
        cfg["quote_per_order"] = min(_float_value(cfg.get("quote_per_order"), 10.0), max(5.0, initial_equity * 0.02))

    cfg["promotion"] = {
        "type": "paper_to_live_trial",
        "source_strategy_id": int(source_row.get("id") or 0),
        "source_strategy_name": source_row.get("name") or "",
        "promoted_at": now,
        "code_commit": _git_commit_ref(),
        "trial": True,
        "trial_initial_equity": float(initial_equity),
        "operator_confirmed_paper_review": True,
        "operator_confirmed_live_risk": True,
    }
    return cfg


def _config_trade_symbols(cfg: Dict[str, Any]) -> List[str]:
    for key in ("trade_symbols", "tradeSymbols"):
        symbols = _row_symbols({"symbols": cfg.get(key)})
        if symbols:
            return symbols
    return []


def _is_contract_live_candidate(cfg: Dict[str, Any], symbols: List[str]) -> bool:
    market_type = str((cfg or {}).get("market_type") or "spot").strip().lower()
    contract_markets = {"swap", "future", "futures", "perp", "perpetual", "contract", "derivative", "derivatives"}
    if market_type in contract_markets:
        return True
    symbol_candidates = list(symbols)
    for key in ("trade_symbols", "tradeSymbols", "contract_trade_symbols", "contractTradeSymbols"):
        symbol_candidates.extend(_row_symbols({"symbols": cfg.get(key)}))
    return any(":USDT" in str(sym).upper() or str(sym).upper().endswith("-SWAP") for sym in symbol_candidates)


def _preview_symbols(symbols: List[str], *, max_items: int = 6) -> str:
    if not symbols:
        return "未定义"
    shown = ", ".join(symbols[:max_items])
    return shown if len(symbols) <= max_items else f"{shown} 等 {len(symbols)} 个"


def _promotion_account_id_from_config(cfg: Dict[str, Any]) -> str:
    promotion = cfg.get("promotion")
    account_id = cfg.get("live_account_id")
    if not account_id and isinstance(promotion, dict):
        account_id = promotion.get("account_id")
    return live_account_service.normalize_account_id(str(account_id or "default"))


def _live_promotion_conflicts(
    source_strategy_id: int,
    *,
    account_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    normalized_account = (
        live_account_service.normalize_account_id(account_id)
        if account_id is not None
        else None
    )
    conflicts: List[Dict[str, Any]] = []
    for item in db.get_strategies():
        try:
            sid = int(item.get("id") or 0)
        except (TypeError, ValueError):
            sid = 0
        cfg = item.get("config") or {}
        if not isinstance(cfg, dict) or cfg.get("is_paper_trading") is not False:
            continue
        promotion = cfg.get("promotion")
        if not isinstance(promotion, dict):
            continue
        try:
            promoted_from = int(promotion.get("source_strategy_id") or 0)
        except (TypeError, ValueError):
            promoted_from = 0
        if promoted_from != int(source_strategy_id):
            continue
        if normalized_account is not None and _promotion_account_id_from_config(cfg) != normalized_account:
            continue
        status = str(item.get("status") or "").lower()
        engine_status = strategy_engine.get_strategy_status(sid) if sid > 0 else None
        engine_state = str((engine_status or {}).get("status") or "").lower()
        active_states = {"running", "paused", "starting", "stopping"}
        if status in active_states or engine_state in active_states:
            conflicts.append(
                {
                    "id": sid,
                    "name": item.get("name") or "",
                    "status": status or engine_state or "unknown",
                }
            )
    return conflicts


def _live_deployment_is_stopped(row: Dict[str, Any]) -> bool:
    try:
        sid = int(row.get("id") or 0)
    except (TypeError, ValueError):
        sid = 0
    status = str(row.get("status") or "").lower()
    try:
        engine_status = strategy_engine.get_strategy_status(sid) if sid > 0 else None
    except Exception:
        engine_status = None
    engine_state = str((engine_status or {}).get("status") or "").lower()
    active_states = {"running", "paused", "starting", "stopping"}
    if engine_state in active_states:
        return False
    return status == "stopped"


def _ensure_live_strategy_settings_table() -> None:
    live_signal_execution_service.ensure_schema()
    conn = db.get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS live_strategy_settings (
            strategy_id INTEGER PRIMARY KEY,
            added INTEGER NOT NULL DEFAULT 0,
            account_id TEXT DEFAULT 'default',
            deployment_strategy_id INTEGER,
            status TEXT DEFAULT 'added',
            risk_config TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (strategy_id) REFERENCES strategies(id)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS live_strategy_account_bindings (
            strategy_id INTEGER NOT NULL,
            account_id TEXT NOT NULL,
            added INTEGER NOT NULL DEFAULT 1,
            deployment_strategy_id INTEGER,
            status TEXT DEFAULT 'added',
            risk_config TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (strategy_id, account_id),
            FOREIGN KEY (strategy_id) REFERENCES strategies(id)
        )
        """
    )
    conn.commit()
    conn.close()


def _live_strategy_settings_by_id() -> Dict[int, Dict[str, Any]]:
    _ensure_live_strategy_settings_table()
    conn = db.get_connection()
    rows = conn.execute(
        """
        SELECT strategy_id, added, account_id, deployment_strategy_id, status,
               risk_config, created_at, updated_at
        FROM live_strategy_settings
        """
    ).fetchall()
    conn.close()
    settings: Dict[int, Dict[str, Any]] = {}
    for row in rows:
        item = dict(row)
        try:
            item["strategy_id"] = int(item["strategy_id"])
        except (TypeError, ValueError):
            continue
        item["added"] = bool(int(item.get("added") or 0))
        item["risk_config"] = _json_dict(item.get("risk_config"))
        settings[int(item["strategy_id"])] = item
    return settings


def _live_strategy_setting(strategy_id: int) -> Optional[Dict[str, Any]]:
    return _live_strategy_settings_by_id().get(int(strategy_id))


def _live_strategy_account_bindings_by_strategy() -> Dict[int, Dict[str, Dict[str, Any]]]:
    _ensure_live_strategy_settings_table()
    conn = db.get_connection()
    rows = conn.execute(
        """
        SELECT strategy_id, account_id, added, deployment_strategy_id, status,
               risk_config, created_at, updated_at
        FROM live_strategy_account_bindings
        """
    ).fetchall()
    conn.close()
    out: Dict[int, Dict[str, Dict[str, Any]]] = {}
    for row in rows:
        item = dict(row)
        try:
            strategy_id = int(item.get("strategy_id") or 0)
        except (TypeError, ValueError):
            continue
        account_id = live_account_service.normalize_account_id(item.get("account_id"))
        item["strategy_id"] = strategy_id
        item["account_id"] = account_id
        item["added"] = bool(int(item.get("added") or 0))
        item["risk_config"] = _json_dict(item.get("risk_config"))
        out.setdefault(strategy_id, {})[account_id] = item

    for strategy_id, setting in _live_strategy_settings_by_id().items():
        if not setting.get("added"):
            continue
        account_id = live_account_service.normalize_account_id(setting.get("account_id"))
        bindings = out.setdefault(strategy_id, {})
        if account_id not in bindings:
            bindings[account_id] = {
                "strategy_id": strategy_id,
                "account_id": account_id,
                "added": True,
                "deployment_strategy_id": setting.get("deployment_strategy_id"),
                "status": setting.get("status") or "added",
                "risk_config": setting.get("risk_config") or {},
                "created_at": setting.get("created_at"),
                "updated_at": setting.get("updated_at"),
            }
    return out


def _upsert_live_strategy_account_binding(
    strategy_id: int,
    *,
    account_id: str,
    added: bool = True,
    risk_config: Optional[Dict[str, Any]] = None,
    deployment_strategy_id: Optional[int] = None,
    clear_deployment_strategy_id: bool = False,
    status: Optional[str] = None,
) -> Dict[str, Any]:
    if not db.get_strategy_by_id(int(strategy_id)):
        raise NotFoundError("Strategy not found")
    _ensure_live_strategy_settings_table()
    normalized_account = (
        live_account_service.validate_live_deployable_account_id(account_id)
        if added
        else live_account_service.validate_account_id(account_id)
    )
    now = datetime.now(timezone.utc).isoformat()
    existing = _live_strategy_account_bindings_by_strategy().get(int(strategy_id), {}).get(normalized_account) or {}
    if clear_deployment_strategy_id:
        next_deployment_id = None
    elif deployment_strategy_id is not None:
        next_deployment_id = int(deployment_strategy_id)
    else:
        next_deployment_id = existing.get("deployment_strategy_id")
    next_status = status or ("added" if added else "removed")
    conn = db.get_connection()
    conn.execute(
        """
        INSERT INTO live_strategy_account_bindings (
            strategy_id, account_id, added, deployment_strategy_id, status,
            risk_config, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(strategy_id, account_id) DO UPDATE SET
            added = excluded.added,
            deployment_strategy_id = COALESCE(excluded.deployment_strategy_id, live_strategy_account_bindings.deployment_strategy_id),
            status = excluded.status,
            risk_config = excluded.risk_config,
            updated_at = excluded.updated_at
        """,
        (
            int(strategy_id),
            normalized_account,
            1 if added else 0,
            next_deployment_id,
            next_status,
            json.dumps(risk_config or existing.get("risk_config") or {}, ensure_ascii=False),
            existing.get("created_at") or now,
            now,
        ),
    )
    conn.commit()
    if clear_deployment_strategy_id:
        conn.execute(
            """
            UPDATE live_strategy_account_bindings
            SET deployment_strategy_id = NULL,
                updated_at = ?
            WHERE strategy_id = ? AND account_id = ?
            """,
            (now, int(strategy_id), normalized_account),
        )
        conn.commit()
    conn.close()
    return _live_strategy_account_bindings_by_strategy().get(int(strategy_id), {}).get(normalized_account) or {}


def _mark_live_strategy_account_bindings_removed(strategy_id: int) -> None:
    _ensure_live_strategy_settings_table()
    conn = db.get_connection()
    conn.execute(
        """
        UPDATE live_strategy_account_bindings
        SET added = 0, status = 'removed', updated_at = ?
        WHERE strategy_id = ?
        """,
        (datetime.now(timezone.utc).isoformat(), int(strategy_id)),
    )
    conn.commit()
    conn.close()


def _clear_live_execution_deployment(live_strategy_id: int) -> None:
    _ensure_live_strategy_settings_table()
    now = datetime.now(timezone.utc).isoformat()
    conn = db.get_connection()
    conn.execute(
        """
        UPDATE live_strategy_account_bindings
        SET deployment_strategy_id = NULL,
            status = CASE WHEN added = 1 THEN 'added' ELSE 'removed' END,
            updated_at = ?
        WHERE deployment_strategy_id = ?
        """,
        (now, int(live_strategy_id)),
    )
    conn.execute(
        """
        UPDATE live_strategy_settings
        SET deployment_strategy_id = NULL,
            status = CASE WHEN added = 1 THEN 'added' ELSE 'removed' END,
            updated_at = ?
        WHERE deployment_strategy_id = ?
        """,
        (now, int(live_strategy_id)),
    )
    conn.commit()
    conn.close()


def _json_dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _upsert_live_strategy_setting(
    strategy_id: int,
    *,
    added: bool,
    account_id: str = "default",
    risk_config: Optional[Dict[str, Any]] = None,
    deployment_strategy_id: Optional[int] = None,
    clear_deployment_strategy_id: bool = False,
    status: Optional[str] = None,
) -> Dict[str, Any]:
    if not db.get_strategy_by_id(int(strategy_id)):
        raise NotFoundError("Strategy not found")
    _ensure_live_strategy_settings_table()
    now = datetime.now(timezone.utc).isoformat()
    setting = _live_strategy_setting(int(strategy_id)) or {}
    next_status = status or ("added" if added else "removed")
    if clear_deployment_strategy_id:
        next_deployment_id = None
    elif deployment_strategy_id is not None:
        next_deployment_id = int(deployment_strategy_id)
    else:
        next_deployment_id = setting.get("deployment_strategy_id")
    conn = db.get_connection()
    conn.execute(
        """
        INSERT INTO live_strategy_settings (
            strategy_id, added, account_id, deployment_strategy_id, status,
            risk_config, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(strategy_id) DO UPDATE SET
            added = excluded.added,
            account_id = excluded.account_id,
            deployment_strategy_id = COALESCE(excluded.deployment_strategy_id, live_strategy_settings.deployment_strategy_id),
            status = excluded.status,
            risk_config = excluded.risk_config,
            updated_at = excluded.updated_at
        """,
        (
            int(strategy_id),
            1 if added else 0,
            (account_id or "default").strip() or "default",
            next_deployment_id,
            next_status,
            json.dumps(risk_config or {}, ensure_ascii=False),
            setting.get("created_at") or now,
            now,
        ),
    )
    conn.commit()
    if clear_deployment_strategy_id:
        conn.execute(
            """
            UPDATE live_strategy_settings
            SET deployment_strategy_id = NULL,
                updated_at = ?
            WHERE strategy_id = ?
            """,
            (now, int(strategy_id)),
        )
        conn.commit()
    conn.close()
    if not added:
        _mark_live_strategy_account_bindings_removed(int(strategy_id))
    return _live_strategy_setting(int(strategy_id)) or {}


def _deployed_live_strategy_for_source(
    source_strategy_id: int,
    *,
    account_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    normalized_account = (
        live_account_service.normalize_account_id(account_id)
        if account_id is not None
        else None
    )
    if normalized_account is not None:
        binding = _live_strategy_account_bindings_by_strategy().get(int(source_strategy_id), {}).get(normalized_account)
        if binding and binding.get("deployment_strategy_id"):
            deployed = db.get_strategy_by_id(int(binding["deployment_strategy_id"]))
            if deployed and not _live_deployment_is_stopped(deployed):
                return deployed
    setting = _live_strategy_setting(int(source_strategy_id))
    setting_account = live_account_service.normalize_account_id((setting or {}).get("account_id"))
    if (
        setting
        and setting.get("deployment_strategy_id")
        and (normalized_account is None or setting_account == normalized_account)
    ):
        deployed = db.get_strategy_by_id(int(setting["deployment_strategy_id"]))
        if deployed and not _live_deployment_is_stopped(deployed):
            return deployed
    conflicts = _live_promotion_conflicts(int(source_strategy_id), account_id=normalized_account)
    if conflicts:
        return db.get_strategy_by_id(int(conflicts[0]["id"]))
    return None


def _live_subscription_for_source(
    source_strategy_id: int,
    *,
    account_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    statuses = sorted(live_signal_execution_service.DEPLOYED_STATUSES)
    if account_id is not None:
        subscription = live_signal_execution_service.get_subscription(
            int(source_strategy_id),
            live_account_service.normalize_account_id(account_id),
        )
        if subscription and str(subscription.get("status") or "").lower() in statuses:
            return subscription
        return None
    subscriptions = live_signal_execution_service.list_subscriptions(
        source_strategy_id=int(source_strategy_id),
        statuses=statuses,
    )
    return subscriptions[0] if subscriptions else None


def _is_live_workspace_source(row: Dict[str, Any]) -> bool:
    cfg = row.get("config") or {}
    if not isinstance(cfg, dict):
        cfg = {}
    if cfg.get("is_paper_trading") is False:
        return False
    name = str(row.get("name") or "")
    if "[实盘" in name or "实盘试运行" in name:
        return False
    return True


def _is_live_workspace_candidate(row: Dict[str, Any]) -> bool:
    if not _is_live_workspace_source(row):
        return False
    try:
        resolved = resolve_unified_base_strategy_class(row)
    except Exception:
        resolved = None
    return resolved is not None


def _has_live_workspace_state(
    strategy_id: int,
    settings: Dict[int, Dict[str, Any]],
    bindings_by_strategy: Dict[int, Dict[str, Dict[str, Any]]],
) -> bool:
    setting = settings.get(int(strategy_id)) or {}
    if setting.get("added") or setting.get("deployment_strategy_id"):
        return True
    bindings = bindings_by_strategy.get(int(strategy_id), {})
    return any(
        bool(binding.get("added")) or bool(binding.get("deployment_strategy_id"))
        for binding in bindings.values()
    )


def _runtime_strategy_metrics(strategy_id: int) -> Dict[str, Optional[float]]:
    try:
        status = strategy_engine.get_strategy_status(int(strategy_id))
    except Exception:
        status = None
    if not isinstance(status, dict):
        return {"total_pnl": None, "return_pct": None}
    return {
        "total_pnl": _float_value(status.get("pnl"), None),
        "return_pct": _float_value(status.get("return_pct"), None),
    }


def _live_execution_strategy_payload(
    row: Dict[str, Any],
    settings: Optional[Dict[int, Dict[str, Any]]] = None,
    bindings_by_strategy: Optional[Dict[int, Dict[str, Dict[str, Any]]]] = None,
) -> Dict[str, Any]:
    settings = settings if settings is not None else _live_strategy_settings_by_id()
    bindings_by_strategy = (
        bindings_by_strategy
        if bindings_by_strategy is not None
        else _live_strategy_account_bindings_by_strategy()
    )
    strategy_id = int(row.get("id") or 0)
    setting = settings.get(strategy_id) or {}
    account_meta = {
        str(account.get("account_id")): account
        for account in live_account_service.list_accounts()
    }
    raw_bindings = bindings_by_strategy.get(strategy_id, {})
    cfg = row.get("config") or {}
    if not isinstance(cfg, dict):
        cfg = {}
    account_bindings: List[Dict[str, Any]] = []
    for account_id, binding in sorted(raw_bindings.items(), key=lambda item: (item[0] != "default", item[0])):
        subscription_for_account = _live_subscription_for_source(strategy_id, account_id=account_id)
        deployed_for_account = None if subscription_for_account else _deployed_live_strategy_for_source(strategy_id, account_id=account_id)
        deployed_id = (
            int(deployed_for_account["id"]) if deployed_for_account else None
        )
        deployed_status = (
            str(subscription_for_account.get("status") or "")
            if subscription_for_account
            else str(deployed_for_account.get("status") or "") if deployed_for_account else None
        )
        meta = account_meta.get(account_id) or {}
        added_binding = bool(binding.get("added")) or deployed_for_account is not None or subscription_for_account is not None
        if not added_binding:
            continue
        is_deployed = subscription_for_account is not None or deployed_for_account is not None
        account_bindings.append(
            {
                "account_id": account_id,
                "account_name": meta.get("name") or account_id,
                "exchange": meta.get("exchange") or "okx",
                "exchange_alias": meta.get("exchange_alias") or live_account_service.exchange_alias_for_account(account_id),
                "masked_api_key": meta.get("masked_api_key"),
                "testnet": bool(meta.get("testnet")),
                "added": added_binding,
                "deployed": is_deployed,
                "live_subscription_id": int(subscription_for_account["id"]) if subscription_for_account else None,
                "deployment_strategy_id": deployed_id,
                "deployment_status": deployed_status,
                "status": binding.get("status") or ("deployed" if is_deployed else "added"),
                "risk_config": binding.get("risk_config") or {},
                "created_at": binding.get("created_at"),
                "updated_at": binding.get("updated_at"),
            }
        )

    account_ids = [item["account_id"] for item in account_bindings]
    subscription = _live_subscription_for_source(strategy_id)
    deployed = None if subscription else _deployed_live_strategy_for_source(strategy_id)
    deployed_id = int(deployed["id"]) if deployed else None
    deployed_status = (
        str(subscription.get("status") or "")
        if subscription
        else str(deployed.get("status") or "") if deployed else None
    )
    symbols = _defined_symbols(row, cfg, None)
    metrics = _runtime_strategy_metrics(strategy_id)
    has_deployment = subscription is not None or deployed is not None
    added = bool(setting.get("added")) or bool(account_bindings) or has_deployment
    status = str(setting.get("status") or ("deployed" if has_deployment else "added" if added else "available"))
    if has_deployment and status in {"available", "added"}:
        status = "deployed"
    primary_account_id = live_account_service.normalize_account_id(
        setting.get("account_id") or (account_ids[0] if account_ids else "default")
    )
    return {
        "strategy_id": strategy_id,
        "strategy_name": str(row.get("name") or f"策略 #{strategy_id}"),
        "added": added,
        "deployable": _is_live_workspace_candidate(row),
        "deployed": has_deployment,
        "live_subscription_id": int(subscription["id"]) if subscription else None,
        "deployment_strategy_id": deployed_id,
        "deployment_strategy_name": None if subscription else deployed.get("name") if deployed else None,
        "deployment_status": deployed_status,
        "status": str(row.get("status") or ""),
        "workspace_status": status,
        "exchange": str(row.get("exchange") or "okx"),
        "account_id": primary_account_id,
        "account_ids": account_ids,
        "account_bindings": account_bindings,
        "symbols": symbols,
        "trade_symbols": _config_trade_symbols(cfg),
        "market_type": str(cfg.get("market_type") or "spot"),
        "risk_config": setting.get("risk_config") or {},
        "total_pnl": metrics["total_pnl"],
        "return_pct": metrics["return_pct"],
        "created_at": row.get("created_at"),
        "updated_at": setting.get("updated_at") or row.get("updated_at"),
    }


def _live_execution_body_to_promote(
    strategy_id: int,
    body: LiveStrategyPreflightBody,
    *,
    confirm_paper_reviewed: bool = False,
    confirm_live_risk: bool = False,
) -> PromoteToLiveBody:
    account_id = live_account_service.validate_live_deployable_account_id(body.account_id or "default")
    return PromoteToLiveBody(
        source_strategy_id=int(strategy_id),
        account_id=account_id,
        exchange=live_account_service.exchange_alias_for_account(account_id),
        initial_equity=body.initial_equity,
        loop_interval=body.loop_interval,
        start_immediately=body.start_immediately,
        confirm_paper_reviewed=confirm_paper_reviewed,
        confirm_live_risk=confirm_live_risk,
        risk_config=body.risk_config,
    )


def _prepare_promoted_live_candidate(body: PromoteToLiveBody) -> Dict[str, Any]:
    account_id = live_account_service.validate_live_deployable_account_id(body.account_id or "default")
    body.account_id = account_id
    body.exchange = live_account_service.exchange_alias_for_account(account_id)
    source = db.get_strategy_by_id(int(body.source_strategy_id))
    if not source:
        raise NotFoundError("未找到来源模拟策略")

    source_cfg = source.get("config") or {}
    if not isinstance(source_cfg, dict):
        source_cfg = {}

    symbols = _defined_symbols(source, dict(source_cfg), None)
    symbol_scope = "strategy_symbols"
    requested_initial_equity = _float_value(body.initial_equity, 0.0)
    try:
        live_cfg = json.loads(json.dumps(source_cfg, ensure_ascii=False))
    except Exception:
        live_cfg = dict(source_cfg)
    live_cfg["is_paper_trading"] = False
    live_cfg["dry_run"] = False
    live_cfg["loop_interval_sec"] = int(body.loop_interval)
    if requested_initial_equity > 0:
        live_cfg["initial_capital"] = requested_initial_equity
        live_cfg["initial_capital_source"] = "request"
    if requested_initial_equity <= 0:
        live_cfg.setdefault("initial_capital", source_cfg.get("initial_capital", 1.0))
        live_cfg["initial_capital_source"] = "live_account_free_usdt"
    live_cfg["live_account_id"] = account_id
    live_cfg["exchange"] = body.exchange
    promotion = live_cfg.get("promotion")
    if not isinstance(promotion, dict):
        promotion = {}
        live_cfg["promotion"] = promotion
    promotion.update(
        {
            "mode": "live_signal_subscription",
            "source_strategy_id": int(body.source_strategy_id),
            "account_id": account_id,
            "exchange_alias": body.exchange,
            "loop_interval_sec": int(body.loop_interval),
        }
    )
    if isinstance(promotion, dict):
        if requested_initial_equity <= 0:
            promotion["trial_initial_equity"] = None
            promotion["trial_initial_equity_source"] = "live_account_free_usdt"
    candidate_row = {**source, "config": live_cfg, "symbols": symbols, "exchange": body.exchange}
    if not symbols:
        resolved = resolve_unified_base_strategy_class(candidate_row)
        if resolved:
            runtime_symbols = _runtime_strategy_symbols(resolved[0], body.exchange, live_cfg)
            if runtime_symbols:
                symbols = runtime_symbols
                symbol_scope = "dynamic_runtime_symbols"
                candidate_row["symbols"] = symbols
                live_cfg["symbol_scope"] = symbol_scope
    timeframe = str(live_cfg.get("timeframe") or source_cfg.get("timeframe") or "1m")

    return {
        "source": source,
        "source_cfg": source_cfg,
        "symbols": symbols,
        "symbol_scope": symbol_scope,
        "live_cfg": live_cfg,
        "candidate_row": candidate_row,
        "timeframe": timeframe,
        "requested_initial_equity": requested_initial_equity,
    }


def _account_equity(account: Optional[Dict[str, Any]]) -> float:
    if not isinstance(account, dict):
        return 0.0
    free = _float_value(account.get("free_usdt"), 0.0)
    return free if free > 0 else 0.0


def _apply_live_account_equity(
    prepared: Dict[str, Any],
    account: Optional[Dict[str, Any]],
) -> None:
    equity = _account_equity(account)
    if equity <= 0:
        return
    live_cfg = prepared["live_cfg"]
    live_cfg["initial_capital"] = equity
    live_cfg["initial_capital_source"] = "live_account_free_usdt"
    live_cfg["detected_live_account"] = account
    promotion = live_cfg.get("promotion")
    if isinstance(promotion, dict):
        promotion["trial_initial_equity"] = equity
        promotion["trial_initial_equity_source"] = "live_account_free_usdt"
        promotion["detected_live_account"] = account
    prepared["account"] = account


def _configured_min_order_notional(cfg: Dict[str, Any]) -> float:
    candidates = [
        _float_value(cfg.get("min_order_notional_usdt"), 0.0),
        _float_value(cfg.get("min_order_value"), 0.0),
        10.0,
    ]
    return max(v for v in candidates if v > 0)


def _configured_order_quote(cfg: Dict[str, Any], equity: float) -> float:
    explicit: List[float] = []
    for key in ("entry_quote_usdt", "quote_per_order", "order_quote_usdt"):
        value = _float_value(cfg.get(key), 0.0)
        if value > 0:
            explicit.append(value)
    for key in ("entry_equity_pct", "entry_balance_pct"):
        pct = _float_value(cfg.get(key), 0.0)
        if pct > 1:
            pct /= 100.0
        if pct > 0 and equity > 0:
            explicit.append(equity * pct)
    if explicit:
        return min(explicit)
    if equity > 0:
        return min(equity, max(5.0, equity * 0.02))
    return 0.0


def _has_explicit_order_quote(cfg: Dict[str, Any]) -> bool:
    for key in ("entry_quote_usdt", "quote_per_order", "order_quote_usdt"):
        if _float_value(cfg.get(key), 0.0) > 0:
            return True
    for key in ("entry_equity_pct", "entry_balance_pct"):
        if _float_value(cfg.get(key), 0.0) > 0:
            return True
    return False


def _promotion_account_sizing_checks(prepared: Dict[str, Any]) -> List[Dict[str, Any]]:
    live_cfg = prepared["live_cfg"]
    account = prepared.get("account")
    equity = _account_equity(account)
    min_notional = _configured_min_order_notional(live_cfg)
    planned_quote = _configured_order_quote(live_cfg, equity)
    has_explicit_quote = _has_explicit_order_quote(live_cfg)
    if equity <= 0:
        return [
            {
                "item": "订单名义金额可执行",
                "passed": False,
                "detail": "未读取到可用 USDT，无法确认最小下单资金和单笔名义金额",
            }
        ]

    passed = equity >= min_notional and (not has_explicit_quote or planned_quote >= min_notional)
    if not has_explicit_quote:
        detail = (
            f"可用资金 {equity:.2f} USDT，满足最小可执行名义 {min_notional:.2f} USDT；"
            "策略未显式配置单笔名义，实际下单仍由策略逻辑和风控引擎校验"
            if passed
            else f"可用资金 {equity:.2f} USDT，低于最小可执行名义 {min_notional:.2f} USDT"
        )
    else:
        detail = (
            f"可用资金 {equity:.2f} USDT；计划单笔名义约 {planned_quote:.2f} USDT；"
            f"最小可执行名义 {min_notional:.2f} USDT"
            if passed
            else (
                f"可用资金 {equity:.2f} USDT；计划单笔名义约 {planned_quote:.2f} USDT，"
                f"低于最小可执行名义 {min_notional:.2f} USDT；请提高实盘可用资金或调低试运行限制后重审"
            )
        )
    return [
        {
            "item": "订单名义金额可执行",
            "passed": passed,
            "detail": detail,
        }
    ]


def _market_rules_check(exchange: Any, symbols: List[str]) -> Dict[str, Any]:
    if not hasattr(exchange, "load_markets") or not getattr(exchange, "exchange", None):
        return {
            "item": "交易规则与市场状态",
            "passed": True,
            "detail": "交易所封装未暴露市场元数据，已由行情和 K 线检查兜底",
        }
    try:
        exchange.load_markets()
        markets = getattr(exchange.exchange, "markets", {}) or {}
        missing: List[str] = []
        inactive: List[str] = []
        min_costs: List[float] = []
        for sym in symbols:
            market = markets.get(sym)
            if not market:
                missing.append(sym)
                continue
            if market.get("active") is False:
                inactive.append(sym)
            limits = market.get("limits") if isinstance(market, dict) else {}
            cost = (limits or {}).get("cost") if isinstance(limits, dict) else {}
            min_cost = _float_value((cost or {}).get("min") if isinstance(cost, dict) else None, 0.0)
            if min_cost > 0:
                min_costs.append(min_cost)
        passed = not missing and not inactive
        detail = (
            "交易对均存在且处于 active"
            if passed
            else (
                (f"缺失市场：{_preview_symbols(missing)}" if missing else "")
                + ("；" if missing and inactive else "")
                + (f"非 active：{_preview_symbols(inactive)}" if inactive else "")
            )
        )
        if min_costs and passed:
            detail += f"；交易所最小名义约 {max(min_costs):.2f} USDT"
        return {"item": "交易规则与市场状态", "passed": passed, "detail": detail}
    except Exception as e:
        return {"item": "交易规则与市场状态", "passed": False, "detail": f"市场规则读取失败：{e}"}


def _dynamic_preflight_min_symbols(cfg: Dict[str, Any], total: int) -> int:
    if total <= 0:
        return 0
    configured = int(_float_value((cfg or {}).get("live_preflight_min_symbols"), 0.0))
    if configured > 0:
        return max(1, min(configured, total))
    return max(1, min(10, total))


async def _order_book_liquidity_check(
    exchange: Any,
    symbols: List[str],
    *,
    allow_dynamic_filter: bool = False,
    min_remaining_symbols: int = 0,
) -> Dict[str, Any]:
    if not hasattr(exchange, "fetch_order_book"):
        return {
            "item": "订单簿点差与深度",
            "passed": True,
            "detail": "交易所封装未暴露订单簿接口，已由行情和 K 线检查兜底",
            "eligible_symbols": symbols,
            "excluded_symbols": [],
        }
    max_spread_pct = 0.005
    min_top_depth_usdt = 20.0
    failed: List[str] = []
    failed_symbols: List[str] = []
    checked = symbols if allow_dynamic_filter else symbols[:8]
    for sym in checked:
        try:
            book = await asyncio.get_running_loop().run_in_executor(
                None,
                lambda s=sym: exchange.fetch_order_book(s, limit=5),
            )
            bids = book.get("bids") if isinstance(book, dict) else []
            asks = book.get("asks") if isinstance(book, dict) else []
            if not bids or not asks:
                failed.append(f"{sym}: 买卖盘为空")
                failed_symbols.append(sym)
                continue
            best_bid = _float_value(bids[0][0] if bids[0] else None, 0.0)
            best_ask = _float_value(asks[0][0] if asks[0] else None, 0.0)
            mid = (best_bid + best_ask) / 2 if best_bid > 0 and best_ask > 0 else 0.0
            spread = (best_ask - best_bid) / mid if mid > 0 else 1.0
            bid_depth = sum(
                _float_value(level[0] if level else None, 0.0)
                * _float_value(level[1] if len(level) > 1 else None, 0.0)
                for level in bids[:5]
            )
            ask_depth = sum(
                _float_value(level[0] if level else None, 0.0)
                * _float_value(level[1] if len(level) > 1 else None, 0.0)
                for level in asks[:5]
            )
            if spread > max_spread_pct:
                failed.append(f"{sym}: 点差 {spread:.2%}")
                failed_symbols.append(sym)
            elif min(bid_depth, ask_depth) < min_top_depth_usdt:
                failed.append(f"{sym}: 前5档深度不足 {min_top_depth_usdt:.0f} USDT")
                failed_symbols.append(sym)
        except Exception as e:
            failed.append(f"{sym}: {e}")
            failed_symbols.append(sym)
    excluded_set = set(failed_symbols)
    eligible_symbols = [sym for sym in symbols if sym not in excluded_set]
    min_remaining = max(0, int(min_remaining_symbols or 0))
    if allow_dynamic_filter and failed:
        passed = len(eligible_symbols) >= min_remaining
    else:
        passed = not failed
    detail = (
        f"已抽检 {_preview_symbols(checked)}，点差<=0.50% 且前5档深度充足"
        if passed and not failed
        else (
            f"已剔除 {len(failed_symbols)} 个低流动性标的：{_preview_symbols(failed_symbols)}；"
            f"剩余 {len(eligible_symbols)}/{len(symbols)} 个标的通过，最低要求 {min_remaining} 个"
            if allow_dynamic_filter and passed
            else "；".join(failed[:5]) + (f"；另有 {len(failed) - 5} 个失败" if len(failed) > 5 else "")
        )
    )
    return {
        "item": "订单簿点差与深度",
        "passed": passed,
        "detail": detail,
        "eligible_symbols": eligible_symbols if passed else [],
        "excluded_symbols": failed_symbols,
    }


def _apply_dynamic_live_symbol_filter(
    prepared: Dict[str, Any],
    runtime: Dict[str, Any],
) -> None:
    if prepared.get("symbol_scope") != "dynamic_runtime_symbols":
        return
    eligible = runtime.get("eligible_symbols")
    if not isinstance(eligible, list) or not eligible:
        return
    excluded = runtime.get("excluded_symbols") if isinstance(runtime.get("excluded_symbols"), list) else []
    prepared["symbols"] = eligible
    prepared["filtered_live_symbols"] = eligible
    prepared["excluded_live_symbols"] = excluded
    prepared["candidate_row"]["symbols"] = eligible
    prepared["live_cfg"]["live_preflight_allowed_symbols"] = eligible
    prepared["live_cfg"]["live_preflight_excluded_symbols"] = excluded


def _promotion_plan(
    prepared: Dict[str, Any],
    *,
    body: PromoteToLiveBody,
) -> Dict[str, Any]:
    source = prepared["source"]
    live_cfg = prepared["live_cfg"]
    symbols = prepared["symbols"]
    return {
        "source_strategy_id": int(source.get("id") or body.source_strategy_id),
        "source_strategy_name": source.get("name") or "",
        "account_id": body.account_id or "default",
        "exchange": body.exchange,
        "mode": "live",
        "dry_run": False,
        "timeframe": prepared["timeframe"],
        "initial_equity": _float_value(live_cfg.get("initial_capital"), 0.0),
        "initial_equity_source": live_cfg.get("initial_capital_source") or "request",
        "loop_interval_sec": int(body.loop_interval),
        "symbols": symbols,
        "trade_symbols": _config_trade_symbols(live_cfg),
        "excluded_symbols": prepared.get("excluded_live_symbols") or [],
        "symbol_scope": prepared.get("symbol_scope") or "strategy_symbols",
        "start_immediately": bool(body.start_immediately),
        "account": prepared.get("account"),
    }


def _promotion_matching_checks(prepared: Dict[str, Any]) -> List[Dict[str, Any]]:
    source = prepared["source"]
    source_cfg = prepared["source_cfg"]
    live_cfg = prepared["live_cfg"]
    symbols = prepared["symbols"]
    symbol_scope = prepared.get("symbol_scope") or "strategy_symbols"
    candidate_row = prepared["candidate_row"]
    trade_symbols = _config_trade_symbols(live_cfg)

    checks: List[Dict[str, Any]] = []
    source_is_paper = source_cfg.get("is_paper_trading") is not False
    checks.append(
        {
            "item": "来源策略为模拟盘",
            "passed": source_is_paper,
            "detail": (
                "来源模拟策略保持运行，实盘订阅复用同一份策略信号"
                if source_is_paper
                else "来源策略已是实盘模式，不能作为模拟转实盘来源"
            ),
        }
    )

    live_mode = live_cfg.get("is_paper_trading") is False
    checks.append(
        {
            "item": "真实交易路径配置",
            "passed": live_mode,
            "detail": (
                "候选配置为 is_paper_trading=false，将使用 LiveBroker 真实下单"
                if live_mode
                else "候选配置仍是模拟模式"
            ),
        }
    )

    is_contract_live = _is_contract_live_candidate(live_cfg, symbols)
    checks.append(
        {
            "item": "实盘合约执行支持",
            "passed": True,
            "detail": (
                "当前策略为 OKX USDT 永续合约，将使用 LiveContractBroker 通过 OKX Trade API 执行"
                if is_contract_live
                else "当前策略是现货实盘候选，当前引擎可继续预检"
            ),
        }
    )

    missing_trade_symbols = [sym for sym in trade_symbols if sym not in symbols]
    symbols_match = bool(symbols) and not missing_trade_symbols
    checks.append(
        {
            "item": "策略交易对匹配",
            "passed": symbols_match,
            "detail": (
                f"策略币池：{_preview_symbols(symbols)}"
                + (
                    f"；交易子池：{_preview_symbols(trade_symbols)}"
                    if trade_symbols
                    else "；动态运行币池由策略解析" if symbol_scope == "dynamic_runtime_symbols" else "；未单独配置交易子池"
                )
                if symbols_match
                else (
                    "策略未定义可继承的交易对，不能隐式改用默认币种"
                    if not symbols
                    else f"交易子池不在策略币池内：{_preview_symbols(missing_trade_symbols)}"
                )
            ),
        }
    )

    resolved = resolve_unified_base_strategy_class(candidate_row)
    checks.append(
        {
            "item": "策略运行合约匹配",
            "passed": resolved is not None,
            "detail": (
                f"已解析为 {resolved[0].__name__}，可由当前实盘引擎加载"
                if resolved
                else "策略无法解析为 BaseStrategy，请补全 strategy_key、module_path/class_name 或 script_content"
            ),
        }
    )

    subscription_conflict = _live_subscription_for_source(
        int(source.get("id") or 0),
        account_id=live_cfg.get("live_account_id") or "default",
    )
    conflicts = [] if subscription_conflict else _live_promotion_conflicts(
        int(source.get("id") or 0),
        account_id=live_cfg.get("live_account_id") or "default",
    )
    checks.append(
        {
            "item": "重复实盘实例冲突",
            "passed": subscription_conflict is None and not conflicts,
            "detail": (
                "未发现同源同账户运行/暂停中的实盘订阅"
                if subscription_conflict is None and not conflicts
                else (
                    f"已有同源同账户实盘订阅：#{subscription_conflict['id']} ({subscription_conflict['status']})"
                    if subscription_conflict
                    else "已有同源实盘实例："
                    + "；".join(
                        f"#{item['id']} {item['name']} ({item['status']})" for item in conflicts[:5]
                    )
                )
            ),
        }
    )

    initial = _float_value(live_cfg.get("initial_capital"), 0.0)
    account_pending = live_cfg.get("initial_capital_source") == "live_account_free_usdt"
    risk_per_trade = _float_value(live_cfg.get("risk_per_trade_pct"), 0.0)
    max_daily_loss = _float_value(live_cfg.get("max_daily_loss_pct"), 0.0)
    max_total_loss = _float_value(live_cfg.get("max_total_loss_pct"), 0.0)
    risk_detail = (
        (
            "实盘订阅资金将按实盘 USDT 可用余额写入预检计划；"
            if account_pending
            else f"预检资金 {initial:.2f} USDT；"
        )
        + f"单笔风险 {risk_per_trade:.2%}；"
        f"日亏损上限 {max_daily_loss:.2%}；"
        f"总亏损上限 {max_total_loss:.2%}"
    )
    checks.append(
        {
            "item": "源策略参数完整继承",
            "passed": source_is_paper and (account_pending or initial > 0),
            "detail": (
                risk_detail
                if source_is_paper and (account_pending or initial > 0)
                else (
                    "实盘订阅必须来自有效模拟策略，并保留原策略交易逻辑/风控参数；"
                    "只覆盖 is_paper_trading=false、live_account_id 和交易所账户别名"
                )
            ),
        }
    )
    loop_interval = int(live_cfg.get("loop_interval_sec") or 0)
    min_interval = 60 if len(symbols) >= 10 else 15 if len(symbols) >= 5 else 5
    checks.append(
        {
            "item": "调度频率与币池规模",
            "passed": loop_interval >= min_interval,
            "detail": (
                f"{len(symbols)} 个策略交易对，轮询间隔 {loop_interval}s，满足上线前限频要求"
                if loop_interval >= min_interval
                else f"{len(symbols)} 个策略交易对至少需要 {min_interval}s 轮询间隔，当前 {loop_interval}s 过高频"
            ),
        }
    )
    return checks


def _live_contract_symbols(cfg: Dict[str, Any], symbols: List[str]) -> List[str]:
    if not _is_contract_live_candidate(cfg, symbols):
        return []
    raw: List[str] = []
    for key in ("contract_trade_symbols", "contractTradeSymbols", "trade_symbols", "tradeSymbols"):
        values = _row_symbols({"symbols": (cfg or {}).get(key)})
        if values:
            raw.extend(values)
            break
    if not raw:
        raw.extend(symbols)
    out: List[str] = []
    seen: set[str] = set()
    for symbol in raw:
        normalized = normalize_contract_symbol(str(symbol))
        if not normalized or normalized in seen:
            continue
        out.append(normalized)
        seen.add(normalized)
    return out


def _okx_position_mode_from_response(response: Any) -> str:
    data = response.get("data") if isinstance(response, dict) else None
    first = data[0] if isinstance(data, list) and data else {}
    raw = str(first.get("posMode") or first.get("positionMode") or "").strip().lower()
    if raw in {"long_short_mode", "longshort", "hedge", "hedge_mode"}:
        return "long_short_mode"
    if raw in {"net", "net_mode"}:
        return "net_mode"
    raise ValueError(f"无法识别 OKX 持仓模式：{raw or response}")


async def _live_contract_account_precheck(
    *,
    exchange: str,
    live_cfg: Dict[str, Any],
    symbols: List[str],
) -> Dict[str, Any]:
    contract_symbols = _live_contract_symbols(live_cfg, symbols)
    if not contract_symbols:
        return {
            "item": "账户合约交易能力",
            "passed": True,
            "detail": "非合约策略，跳过 OKX SWAP 账户预检查",
        }

    td_mode = str(live_cfg.get("td_mode") or live_cfg.get("mgn_mode") or "isolated").lower()
    if td_mode not in {"cross", "isolated"}:
        td_mode = "isolated"

    def probe() -> Dict[str, Any]:
        ex = exchange_manager.get_exchange(exchange)
        if not ex or not getattr(ex, "exchange", None):
            raise ValueError(f"交易所实例不可用：{exchange}")
        native = ex.exchange
        if not hasattr(native, "privateGetAccountConfig"):
            raise ValueError("OKX account config endpoint unavailable")
        account_config = native.privateGetAccountConfig({})
        position_mode = _okx_position_mode_from_response(account_config)
        instruments = load_contract_instruments(exchange, contract_symbols[:1], live_cfg)
        inst = instruments[contract_symbols[0]]
        payload: Dict[str, Any] = {
            "instId": inst.inst_id,
            "tdMode": td_mode,
            "side": "buy",
            "ordType": "market",
            "sz": str(inst.min_sz),
        }
        if position_mode == "long_short_mode":
            payload["posSide"] = "long"

        response: Optional[Dict[str, Any]] = None
        if hasattr(native, "privatePostTradeOrderPrecheck"):
            response = native.privatePostTradeOrderPrecheck(payload)
        elif hasattr(native, "request"):
            response = native.request("trade/order-precheck", "private", "POST", payload)

        if response is None:
            return {
                "position_mode": position_mode,
                "inst_id": inst.inst_id,
                "precheck_available": False,
            }
        code = str(response.get("code") or "0") if isinstance(response, dict) else "0"
        data = response.get("data") if isinstance(response, dict) and isinstance(response.get("data"), list) else []
        first = data[0] if data else {}
        sub_code = str(first.get("sCode") or "0")
        if code not in {"", "0"} or sub_code not in {"", "0"}:
            raise ValueError(first.get("sMsg") or response.get("msg") or str(response))
        return {
            "position_mode": position_mode,
            "inst_id": inst.inst_id,
            "precheck_available": True,
        }

    try:
        result = await asyncio.get_running_loop().run_in_executor(None, probe)
        mode = result.get("position_mode")
        mode_label = "双向持仓" if mode == "long_short_mode" else "单向持仓"
        if result.get("precheck_available"):
            detail = f"OKX {mode_label}模式；{result.get('inst_id')} 最小张数非成交 order-precheck 通过"
        else:
            detail = f"OKX {mode_label}模式；当前客户端未暴露 order-precheck，已完成账户配置检查"
        return {"item": "账户合约交易能力", "passed": True, "detail": detail}
    except Exception as exc:
        return {
            "item": "账户合约交易能力",
            "passed": False,
            "detail": f"OKX 合约账户预检查失败：{exc}",
        }


async def _run_promote_preflight(
    body: PromoteToLiveBody,
    prepared: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    if prepared is None:
        prepared = _prepare_promoted_live_candidate(body)
    checks = _promotion_matching_checks(prepared)
    if all(c["passed"] for c in checks):
        checks.append(await _live_account_trade_permission_check(body.account_id or "default"))
    if all(c["passed"] for c in checks):
        checks.append(
            await _live_contract_account_precheck(
                exchange=body.exchange,
                live_cfg=prepared["live_cfg"],
                symbols=prepared["symbols"],
            )
        )
    if all(c["passed"] for c in checks):
        runtime = await _run_preflight_checks(
            strategy_id=int(body.source_strategy_id),
            row=prepared["candidate_row"],
            exchange=body.exchange,
            timeframe=prepared["timeframe"],
            dry_run=False,
            symbol=None,
            symbol_scope=prepared.get("symbol_scope") or "strategy_symbols",
        )
        _apply_dynamic_live_symbol_filter(prepared, runtime)
        _apply_live_account_equity(prepared, runtime.get("account"))
        checks.extend(runtime["checks"])
        checks.extend(_promotion_account_sizing_checks(prepared))

    return {
        "all_passed": all(c["passed"] for c in checks),
        "checks": checks,
        "plan": _promotion_plan(prepared, body=body),
        "account": prepared.get("account"),
    }


async def _live_account_trade_permission_check(account_id: str) -> Dict[str, Any]:
    try:
        result = await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: live_account_service.validate_account_trade_permission(account_id),
        )
        can_trade = bool(result.get("can_trade"))
        return {
            "item": "账户交易权限",
            "passed": can_trade,
            "detail": (
                str(result.get("detail") or "读取权限和交易权限测试通过")
                if can_trade
                else "当前 API Key 未通过 Trade 权限测试"
            ),
        }
    except Exception as exc:
        return {
            "item": "账户交易权限",
            "passed": False,
            "detail": str(exc),
        }


def _insert_promoted_strategy(
    source_row: Dict[str, Any],
    *,
    exchange: str,
    symbols: List[str],
    config: Dict[str, Any],
) -> int:
    name = _unique_strategy_name(_promoted_live_strategy_name(source_row))
    description = (
        f"小资金实盘试运行克隆自模拟策略 #{source_row.get('id')}：{source_row.get('name') or ''}。"
        "该策略由模拟转实盘流程生成，独立于原模拟策略。"
    )
    conn = db.get_connection()
    cur = conn.cursor()
    now = datetime.now(timezone.utc).isoformat()
    cur.execute(
        """
        INSERT INTO strategies
        (name, description, script_content, config, status, exchange, symbols, created_at, updated_at)
        VALUES (?, ?, ?, ?, 'stopped', ?, ?, ?, ?)
        """,
        (
            name,
            description,
            source_row.get("script_content") or "",
            json.dumps(config, ensure_ascii=False),
            exchange,
            json.dumps(symbols, ensure_ascii=False),
            now,
            now,
        ),
    )
    new_id = int(cur.lastrowid)
    conn.commit()
    conn.close()
    return new_id


def _asset_prefix_for_config(config: Dict[str, Any] | None) -> str:
    market_type = str((config or {}).get("market_type") or "spot").strip().lower()
    contract_markets = {"swap", "future", "futures", "perp", "perpetual", "contract", "derivative", "derivatives"}
    return "[合约]" if market_type in contract_markets else "[现货]"


def _strip_asset_prefix(name: str) -> str:
    value = str(name or "").strip()
    for prefix in ("[现货]", "[合约]"):
        if value.startswith(prefix):
            return value[len(prefix):].strip()
    return value


def _promoted_live_strategy_name(source_row: Dict[str, Any]) -> str:
    raw_name = str(source_row.get("name") or source_row.get("id") or "策略")
    prefix = "[合约]" if raw_name.strip().startswith("[合约]") else _asset_prefix_for_config(source_row.get("config") or {})
    base_name = _strip_asset_prefix(raw_name)
    return f"{prefix} [实盘试运行] {base_name}"


def _latest_positive_equity_sample(strategy_id: int) -> Optional[float]:
    for item in reversed(_equity_curve_samples.get(strategy_id, [])):
        value = _float_value(item.get("equity"), 0.0)
        if value > 0:
            return value
    if hasattr(db, "get_latest_strategy_equity_sample"):
        try:
            item = db.get_latest_strategy_equity_sample(strategy_id)
        except Exception as exc:
            logger.debug("Load latest persisted equity sample failed for %s: %s", strategy_id, exc)
            item = None
        if isinstance(item, dict):
            value = _float_value(item.get("equity"), 0.0)
            if value > 0:
                _load_persisted_equity_samples(strategy_id)
                return value
    return None


def _performance_metrics(
    strategy_id: int,
    *,
    initial: float,
    equity_cur: float,
    total_trades: int,
    run_started_at: Optional[str],
) -> Dict[str, float | int]:
    """用本轮成交与权益采样计算实时监控指标，供详情页和实例卡片共用。"""
    total_pnl = equity_cur - initial
    total_pnl_pct = (total_pnl / initial * 100) if initial > 0 else 0.0

    closing_trades = 0
    winning_closes = 0
    gross_profit = 0.0
    gross_loss = 0.0
    since_ms = 0
    if run_started_at:
        try:
            dt = datetime.fromisoformat(str(run_started_at).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            since_ms = int(dt.timestamp() * 1000)
        except Exception:
            since_ms = 0
    try:
        rows = db.get_strategy_trades_since(strategy_id, since_ms) if since_ms > 0 else []
    except Exception:
        rows = []
    run_trade_count = len(rows) if rows else int(total_trades)
    for row in rows:
        side = str(row.get("side") or "").strip().lower().replace("-", "_").replace(" ", "_")
        if side not in {"sell", "spot_sell", "close_long", "close_short"}:
            continue
        closing_trades += 1
        try:
            pnl = float(row.get("pnl") or 0)
        except (TypeError, ValueError):
            pnl = 0.0
        if pnl > 0:
            winning_closes += 1
            gross_profit += pnl
        elif pnl < 0:
            gross_loss += abs(pnl)
    win_rate = (winning_closes / closing_trades * 100) if closing_trades > 0 else 0.0
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else 0.0

    seq = _equity_curve_samples.get(strategy_id) or _load_persisted_equity_samples(strategy_id)
    equities: List[float] = []
    for item in seq:
        try:
            value = float(item.get("equity") or 0)
        except (TypeError, ValueError):
            value = 0.0
        if value > 0:
            equities.append(value)

    max_drawdown = 0.0
    peak = 0.0
    for value in equities:
        peak = max(peak, value)
        if peak > 0:
            max_drawdown = max(max_drawdown, (peak - value) / peak * 100)

    returns: List[float] = []
    for prev, cur in zip(equities, equities[1:]):
        if prev > 0:
            returns.append((cur - prev) / prev)
    sharpe_ratio = 0.0
    if len(returns) >= 2:
        mean_r = sum(returns) / len(returns)
        var = sum((r - mean_r) ** 2 for r in returns) / (len(returns) - 1)
        std_r = math.sqrt(var)
        if std_r > 0:
            sharpe_ratio = mean_r / std_r * math.sqrt(len(returns))

    return {
        "total_pnl": round(total_pnl, 6),
        "total_pnl_pct": round(total_pnl_pct, 6),
        "win_rate": round(win_rate, 4),
        "gross_profit": round(gross_profit, 6),
        "gross_loss": round(gross_loss, 6),
        "profit_factor": round(profit_factor, 4),
        "total_trades": int(run_trade_count),
        "max_drawdown": round(max_drawdown, 6),
        "sharpe_ratio": round(sharpe_ratio, 6),
    }


def _build_dashboard(strategy_id: int) -> Dict[str, Any]:
    row = db.get_strategy_by_id(strategy_id)
    risk = strategy_engine.get_risk_status()

    cfg = (row or {}).get("config") or {}
    if not isinstance(cfg, dict):
        cfg = {}

    dry_run = bool(cfg.get("is_paper_trading", True))
    symbol = (row or {}).get("symbols") or ["BTC/USDT"]
    if isinstance(symbol, list) and symbol:
        sym = symbol[0]
        symbols_list = [str(s) for s in symbol if s]
    else:
        sym = str(symbol)
        symbols_list = [sym] if sym else []

    st = strategy_engine.get_strategy_status(strategy_id)
    if dry_run:
        # SuperPnL 会订阅 Top20，但标记价只需要刷新当前持仓，避免详情页首屏串行拉整池 ticker。
        mark_symbols = _position_symbols_from_status(st)
        if mark_symbols:
            _refresh_paper_marks(strategy_id, (row or {}).get("exchange") or "okx", mark_symbols)
            st = strategy_engine.get_strategy_status(strategy_id)

    if st:
        state = st.get("status") or "stopped"
        runtime_symbols = st.get("symbols")
        if isinstance(runtime_symbols, list) and runtime_symbols:
            symbols_list = [str(s) for s in runtime_symbols if s]
            sym = symbols_list[0] if symbols_list else sym
        elif isinstance(runtime_symbols, str) and runtime_symbols.strip():
            sym = runtime_symbols.strip()
            symbols_list = [sym]
        initial = (
            _float_value(st.get("initial_capital"), 0.0)
            or _configured_initial_capital(cfg)
        )
        status_equity = _float_value(st.get("equity"), 0.0)
        if status_equity > 0:
            equity_cur = status_equity
        else:
            # pause/stop 会取消任务并移除 PaperBroker；此时 context 仍在，
            # get_strategy_status() 只能返回默认 0。展示层应保留最近权益，
            # 没有采样时回退到初始资金，避免误显示账户归零。
            equity_cur = _latest_positive_equity_sample(strategy_id) or initial
        ret_pct = (
            _float_value(st.get("return_pct"), 0.0)
            if status_equity > 0
            else ((equity_cur - initial) / initial * 100 if initial > 0 else 0.0)
        )
        total_trades = int(st.get("total_trades", 0) or 0)
    else:
        state = (row or {}).get("status") or "stopped"
        initial = _configured_initial_capital(cfg)
        equity_cur = _latest_positive_equity_sample(strategy_id) or initial
        ret_pct = (equity_cur - initial) / initial * 100 if initial > 0 else 0.0
        total_trades = 0

    if dry_run:
        positions_list, unrealized_total = _paper_positions_from_status(st)
    else:
        positions_list, unrealized_total = [], 0.0

    # The global account circuit breaker is backed by real exchange equity. Do not
    # project that live-account state onto paper instances in the detail UI.
    effective_circuit = bool(risk.get("circuit_breaker")) and not dry_run

    if effective_circuit:
        ui_state = "circuit_breaker"
    else:
        ui_state = state
    perf = _performance_metrics(
        strategy_id,
        initial=initial,
        equity_cur=equity_cur,
        total_trades=total_trades,
        run_started_at=(row or {}).get("run_started_at"),
    )

    return {
        "system": {
            "state": ui_state,
            "uptime": _uptime_str(st.get("started_at") if st else None),
            "exchange": (row or {}).get("exchange") or "",
            "symbol": sym,
            "symbols": symbols_list,
            "timeframe": str(cfg.get("timeframe") or ""),
            "strategy": (row or {}).get("name") or str(strategy_id),
            "strategy_id": strategy_id,
            "dry_run": dry_run,
            "mode": "paper" if dry_run else "live",
        },
        "equity": {
            "initial": initial,
            "current": equity_cur,
            "peak": equity_cur,
            "change": equity_cur - initial,
            "change_pct": ret_pct,
        },
        "performance": {
            "total_pnl": perf["total_pnl"],
            "total_pnl_pct": perf["total_pnl_pct"] if initial > 0 else ret_pct,
            "win_rate": perf["win_rate"],
            "profit_factor": perf["profit_factor"],
            "gross_profit": perf["gross_profit"],
            "gross_loss": perf["gross_loss"],
            "total_trades": perf["total_trades"],
            "max_drawdown": perf["max_drawdown"],
            "sharpe_ratio": perf["sharpe_ratio"],
        },
        "risk": {
            "circuit_breaker": effective_circuit,
            "current_drawdown": (
                float(risk.get("current_drawdown") or 0) if effective_circuit else 0.0
            ),
            "daily_loss": 0.0,
        },
        "positions": positions_list,
        "account": {
            "unrealized_pnl": unrealized_total,
        },
        "recent_events": [],
        "feishu": _feishu_dashboard_slice(),
    }


@router.post("/configure")
async def live_configure(body: LiveConfigureBody):
    global _active_strategy_id
    sid = _parse_strategy_id(body.strategy_type)
    existing = db.get_strategy_by_id(sid)
    if not existing:
        raise NotFoundError("Strategy not found")

    eng = strategy_engine.get_strategy_status(sid)
    if eng and eng.get("status") == "running":
        raise BadRequestError("策略运行中，请先停止后再修改配置")

    cfg = dict(existing.get("config") or {})
    if not isinstance(cfg, dict):
        cfg = {}
    cfg["timeframe"] = _strategy_defined_timeframe(existing, cfg)
    cfg["initial_capital"] = float(body.initial_equity)
    cfg["is_paper_trading"] = bool(body.dry_run)
    if body.loop_interval is not None:
        cfg["loop_interval_sec"] = int(body.loop_interval)
    rc = body.risk_config or {}
    if rc:
        cfg["risk_per_trade_pct"] = rc.get("risk_per_trade_pct")
        cfg["max_daily_loss_pct"] = rc.get("max_daily_loss_pct")
        cfg["max_total_loss_pct"] = rc.get("max_total_loss_pct")

    symbols = _configured_symbols(existing, cfg, body.symbol)
    conn = db.get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE strategies SET exchange = ?, symbols = ?, config = ?, updated_at = datetime('now')
        WHERE id = ?
        """,
        (body.exchange, json.dumps(symbols), json.dumps(cfg), sid),
    )
    conn.commit()
    conn.close()

    strategy_engine.drop_cached_context(sid)
    _active_strategy_id = sid
    return ok({"configured": True, "strategy_id": sid})


@router.post("/start")
async def live_start(body: LiveInstanceBody = Body(default_factory=LiveInstanceBody)):
    sid = _resolve_instance_sid(body)
    if sid is None:
        raise BadRequestError("请先调用 /live/configure 或选择策略")
    started_ok = await strategy_engine.start_strategy(sid)
    if not started_ok:
        raise BadRequestError("策略启动失败（可能处于熔断或配置无效）")
    return ok({"started": True, "strategy_id": sid})


@router.post("/stop")
async def live_stop(body: LiveInstanceBody = Body(default_factory=LiveInstanceBody)):
    sid = _resolve_instance_sid(body)
    global _active_strategy_id
    if sid is None:
        return ok({"stopped": False})
    await strategy_engine.stop_strategy(sid, clear_metrics=bool(body.clear_metrics))
    _clear_live_execution_deployment(sid)
    if body.clear_metrics:
        _equity_curve_samples.pop(sid, None)
    if _active_strategy_id == sid:
        _active_strategy_id = None
    return ok({"stopped": True, "clear_metrics": bool(body.clear_metrics)})


@router.post("/pause")
async def live_pause(body: LiveInstanceBody = Body(default_factory=LiveInstanceBody)):
    sid = _resolve_instance_sid(body)
    if sid is None:
        raise BadRequestError("没有运行中的会话")
    await strategy_engine.pause_strategy(sid)
    return ok({"paused": True})


@router.post("/resume")
async def live_resume(body: LiveInstanceBody = Body(default_factory=LiveInstanceBody)):
    sid = _resolve_instance_sid(body)
    if sid is None:
        raise BadRequestError("没有可恢复的会话")
    resumed_ok = await strategy_engine.start_strategy(sid)
    if not resumed_ok:
        raise BadRequestError("恢复运行失败")
    return ok({"resumed": True})


@router.post("/positions/close")
async def live_close_paper_position(body: PaperPositionCloseBody):
    sid = body.instance_id if body.instance_id is not None else _resolve_target_strategy_id()
    if sid is None:
        raise BadRequestError("没有可操作的模拟盘实例")

    row = db.get_strategy_by_id(int(sid))
    if not row:
        raise NotFoundError("Strategy not found")
    cfg = row.get("config") or {}
    if not isinstance(cfg, dict):
        cfg = {}
    if cfg.get("is_paper_trading") is False:
        raise BadRequestError("仅支持模拟盘持仓平仓；实盘持仓请使用实盘工作台")

    result = await strategy_engine.close_paper_position(
        int(sid),
        symbol=body.symbol,
        side=body.side,
        market_type=body.market_type,
    )
    status = str(result.get("status") or "").lower()
    closed_count = int(result.get("closed") or 0) if str(result.get("closed") or "").isdigit() else 0
    if status != "filled" and closed_count <= 0:
        reason = result.get("reason") or result.get("error") or "当前持仓无法平仓"
        raise BadRequestError(str(reason))

    return ok({"closed": True, "strategy_id": int(sid), "result": result})


@router.get("/dashboard")
async def live_dashboard(instance_id: Optional[int] = Query(None)):
    sid = _resolve_instance_sid(None, query_id=instance_id)
    if sid is None:
        return ok(
            {
                "system": {
                    "state": "idle",
                    "uptime": "-",
                    "exchange": "",
                    "symbol": "",
                    "symbols": [],
                    "timeframe": "",
                    "strategy": "",
                    "strategy_id": None,
                    "dry_run": True,
                    "mode": "paper",
                },
                "equity": {"initial": 0, "current": 0, "peak": 0, "change": 0, "change_pct": 0},
                "performance": {
                    "total_pnl": 0,
                    "total_pnl_pct": 0,
                    "win_rate": 0,
                    "profit_factor": 0,
                    "gross_profit": 0,
                    "gross_loss": 0,
                    "total_trades": 0,
                    "max_drawdown": 0,
                    "sharpe_ratio": 0,
                },
                "risk": {
                    "circuit_breaker": bool(
                        strategy_engine.get_risk_status().get("circuit_breaker")
                    ),
                    "current_drawdown": 0,
                    "daily_loss": 0,
                },
                "positions": [],
                "account": {"unrealized_pnl": 0.0},
                "recent_events": [],
                "feishu": _feishu_dashboard_slice(),
            }
        )
    data = _build_dashboard(sid)
    if not data["system"].get("dry_run"):
        ex = (data["system"].get("exchange") or "").strip()
        sym = (data["system"].get("symbol") or "").strip()
        if ex and sym:
            try:
                raw = await trading_service.get_positions(ex, sym)
                if not isinstance(raw, list):
                    raw = []
                norm, total_up = _normalize_ccxt_positions(raw)
                data["positions"] = norm
                data["account"]["unrealized_pnl"] = total_up
            except Exception as e:
                logger.warning("live_dashboard fetch positions failed: %s", e)
    return ok(data)


@router.get("/events")
async def live_events(
    limit: int = 50,
    event_type: Optional[str] = None,
    instance_id: Optional[int] = Query(None),
):
    _ = event_type
    sid = _resolve_instance_sid(None, query_id=instance_id)
    if sid is None:
        return ok({"events": []})
    return ok({"events": strategy_log_store.get(sid, limit)})


@router.get("/equity_curve")
async def live_equity_curve(instance_id: Optional[int] = Query(None)):
    sid = _resolve_instance_sid(None, query_id=instance_id)
    if sid is None:
        return ok([])
    return ok(_load_persisted_equity_samples(sid))


@router.get("/instances")
async def live_instances_probe():
    """兼容手工探测 / 旧脚本；当前与引擎会话一致时返回占位结构。"""
    ids = strategy_engine.list_running_or_paused_ids()
    return ok(
        {
            "instances": [
                {"id": str(i), "strategy_id": i, "status": "running"} for i in ids
            ]
        }
    )


@router.get("/strategies")
async def list_live_execution_strategies():
    settings = _live_strategy_settings_by_id()
    bindings_by_strategy = _live_strategy_account_bindings_by_strategy()
    strategies = [
        _live_execution_strategy_payload(row, settings, bindings_by_strategy)
        for row in db.get_strategies()
        if _is_live_workspace_candidate(row)
        or (
            _is_live_workspace_source(row)
            and _has_live_workspace_state(int(row.get("id") or 0), settings, bindings_by_strategy)
        )
    ]
    strategies.sort(
        key=lambda item: (
            0 if item.get("added") else 1,
            -int(item.get("strategy_id") or 0),
        )
    )
    return ok({"strategies": strategies})


@router.patch("/strategies/{strategy_id}")
async def patch_live_execution_strategy(strategy_id: int, body: LiveStrategySettingBody):
    row = db.get_strategy_by_id(int(strategy_id))
    if not row:
        raise NotFoundError("Strategy not found")
    if not _is_live_workspace_candidate(row):
        raise BadRequestError("该策略不是可部署的模拟策略")
    if body.added is False:
        raise BadRequestError("已加入实盘策略列表的策略不能从实盘策略列表删除，请保留审计记录并在实盘面板管理订阅状态")
    if body.bind_account is not False and body.added is not False:
        account_id = live_account_service.validate_live_deployable_account_id(body.account_id or "default")
    else:
        account_id = live_account_service.validate_account_id(body.account_id or "default")
    if body.bind_account is False:
        current_setting = _live_strategy_setting(int(strategy_id)) or {}
        _upsert_live_strategy_account_binding(
            int(strategy_id),
            account_id=account_id,
            added=False,
            risk_config=body.risk_config or {},
            status="removed",
        )
        bindings = _live_strategy_account_bindings_by_strategy().get(int(strategy_id), {})
        still_added = any(bool(item.get("added")) for item in bindings.values())
        keep_strategy_added = bool(current_setting.get("added"))
        next_account_id = next(
            (item.get("account_id") for item in bindings.values() if item.get("added")),
            account_id,
        )
        setting = _upsert_live_strategy_setting(
            int(strategy_id),
            added=keep_strategy_added or still_added,
            account_id=str(next_account_id or account_id),
            risk_config=body.risk_config or {},
            status="added" if (keep_strategy_added or still_added) else "removed",
        )
        return ok({"strategy": _live_execution_strategy_payload(row, {int(strategy_id): setting})})

    setting = _upsert_live_strategy_setting(
        int(strategy_id),
        added=bool(body.added),
        account_id=account_id,
        risk_config=body.risk_config or {},
    )
    if body.added is not False:
        _upsert_live_strategy_account_binding(
            int(strategy_id),
            account_id=account_id,
            added=True,
            risk_config=body.risk_config or {},
        )
    payload = _live_execution_strategy_payload(row, {int(strategy_id): setting})
    return ok({"strategy": payload})


def _require_added_live_strategy(strategy_id: int, account_id: Optional[str] = None) -> Dict[str, Any]:
    row = db.get_strategy_by_id(int(strategy_id))
    if not row:
        raise NotFoundError("Strategy not found")
    if not _is_live_workspace_candidate(row):
        raise BadRequestError("该策略不是可部署的模拟策略")
    setting = _live_strategy_setting(int(strategy_id))
    if not setting or not setting.get("added"):
        raise BadRequestError("请先将策略加入实盘策略列表")
    if account_id is not None:
        normalized_account = live_account_service.validate_account_id(account_id)
        binding = _live_strategy_account_bindings_by_strategy().get(int(strategy_id), {}).get(normalized_account)
        legacy_account = live_account_service.normalize_account_id(setting.get("account_id"))
        if not binding and legacy_account == normalized_account:
            binding = {
                "added": True,
                "account_id": normalized_account,
            }
        if not binding or not binding.get("added"):
            raise BadRequestError("请先将该账户绑定到当前实盘策略")
    return row


def _require_live_subscription_control(
    strategy_id: int,
    account_id: str,
    *,
    allow_stopped: bool = False,
) -> tuple[Dict[str, Any], Dict[str, Any]]:
    row = db.get_strategy_by_id(int(strategy_id))
    if not row:
        raise NotFoundError("Strategy not found")
    if not _is_live_workspace_source(row):
        raise BadRequestError("该策略不是可控制的模拟来源策略")
    subscription = live_signal_execution_service.get_subscription(int(strategy_id), account_id)
    if not subscription:
        raise BadRequestError("当前账户尚未部署该实盘订阅")
    if not allow_stopped and str(subscription.get("status") or "").lower() == "stopped":
        raise BadRequestError("当前账户尚未部署该实盘订阅")
    return row, subscription


@router.post("/strategies/{strategy_id}/preflight")
async def preflight_live_execution_strategy(strategy_id: int, body: LiveStrategyPreflightBody):
    account_id = live_account_service.validate_live_deployable_account_id(body.account_id or "default")
    row = _require_added_live_strategy(int(strategy_id), account_id)
    promote_body = _live_execution_body_to_promote(int(strategy_id), body)
    preflight = await _run_promote_preflight(promote_body)
    _upsert_live_strategy_account_binding(
        int(strategy_id),
        account_id=promote_body.account_id or "default",
        added=True,
        risk_config=body.risk_config or {},
        status="preflight_passed" if preflight.get("all_passed") else "preflight_failed",
    )
    _upsert_live_strategy_setting(
        int(strategy_id),
        added=True,
        account_id=promote_body.account_id or "default",
        risk_config=body.risk_config or {},
        status="preflight_passed" if preflight.get("all_passed") else "preflight_failed",
    )
    return ok({"strategy": _live_execution_strategy_payload(row), "preflight": preflight})


@router.post("/strategies/{strategy_id}/deploy")
async def deploy_live_execution_strategy(strategy_id: int, body: LiveStrategyDeployBody):
    if not body.confirm_paper_reviewed or not body.confirm_live_risk:
        raise BadRequestError("部署实盘需要确认已复核模拟盘表现，并确认真实资金风险")

    account_id = live_account_service.validate_live_deployable_account_id(body.account_id or "default")
    row = _require_added_live_strategy(int(strategy_id), account_id)
    promote_body = _live_execution_body_to_promote(
        int(strategy_id),
        body,
        confirm_paper_reviewed=True,
        confirm_live_risk=True,
    )
    prepared = _prepare_promoted_live_candidate(promote_body)
    source_cfg = prepared["source_cfg"]
    if source_cfg.get("is_paper_trading") is False:
        raise BadRequestError("来源策略已经是实盘策略，不能再次部署")

    preflight = await _run_promote_preflight(promote_body, prepared=prepared)
    if not preflight["all_passed"]:
        _upsert_live_strategy_setting(
            int(strategy_id),
            added=True,
            account_id=promote_body.account_id or "default",
            risk_config=body.risk_config or {},
            status="preflight_failed",
        )
        _upsert_live_strategy_account_binding(
            int(strategy_id),
            account_id=promote_body.account_id or "default",
            added=True,
            risk_config=body.risk_config or {},
            status="preflight_failed",
        )
        return ok(
            {
                "deployed": False,
                "started": False,
                "source_strategy_id": int(strategy_id),
                "strategy": _live_execution_strategy_payload(row),
                "preflight": preflight,
            }
        )

    subscription_status = "running" if body.start_immediately else "paused"
    subscription_risk_config = dict(body.risk_config or {})
    plan = preflight.get("plan") if isinstance(preflight.get("plan"), dict) else {}
    if plan.get("symbol_scope") == "dynamic_runtime_symbols":
        allowed_symbols = plan.get("symbols") if isinstance(plan.get("symbols"), list) else []
        excluded_symbols = plan.get("excluded_symbols") if isinstance(plan.get("excluded_symbols"), list) else []
        if allowed_symbols:
            subscription_risk_config["allowed_live_symbols"] = allowed_symbols
        if excluded_symbols:
            subscription_risk_config["excluded_live_symbols"] = excluded_symbols
    subscription = live_signal_execution_service.upsert_subscription(
        source_strategy_id=int(strategy_id),
        account_id=promote_body.account_id or "default",
        status=subscription_status,
        risk_config=subscription_risk_config,
    )
    _upsert_live_strategy_setting(
        int(strategy_id),
        added=True,
        account_id=promote_body.account_id or "default",
        risk_config=subscription_risk_config,
        deployment_strategy_id=None,
        clear_deployment_strategy_id=True,
        status="deployed" if body.start_immediately else "paused",
    )
    _upsert_live_strategy_account_binding(
        int(strategy_id),
        account_id=promote_body.account_id or "default",
        added=True,
        risk_config=subscription_risk_config,
        deployment_strategy_id=None,
        clear_deployment_strategy_id=True,
        status="deployed" if body.start_immediately else "paused",
    )
    return ok(
        {
            "deployed": True,
            "started": body.start_immediately,
            "source_strategy_id": int(strategy_id),
            "live_strategy_id": None,
            "live_subscription_id": subscription["id"],
            "strategy": _live_execution_strategy_payload(row),
            "preflight": preflight,
        }
    )


@router.post("/strategies/{strategy_id}/pause")
async def pause_live_strategy_subscription(strategy_id: int, body: LiveStrategySubscriptionControlBody):
    account_id = live_account_service.validate_account_id(body.account_id or "default")
    row, _ = _require_live_subscription_control(int(strategy_id), account_id)
    subscription = live_signal_execution_service.set_subscription_status(
        source_strategy_id=int(strategy_id),
        account_id=account_id,
        status="paused",
    )
    _upsert_live_strategy_account_binding(
        int(strategy_id),
        account_id=account_id,
        added=True,
        risk_config=subscription.get("risk_config") or {},
        deployment_strategy_id=None,
        clear_deployment_strategy_id=True,
        status="deployed",
    )
    _upsert_live_strategy_setting(
        int(strategy_id),
        added=True,
        account_id=account_id,
        risk_config=subscription.get("risk_config") or {},
        deployment_strategy_id=None,
        clear_deployment_strategy_id=True,
        status="deployed",
    )
    return ok(
        {
            "paused": True,
            "source_strategy_id": int(strategy_id),
            "live_subscription_id": subscription["id"],
            "strategy": _live_execution_strategy_payload(row),
        }
    )


@router.post("/strategies/{strategy_id}/resume")
async def resume_live_strategy_subscription(strategy_id: int, body: LiveStrategySubscriptionControlBody):
    account_id = live_account_service.validate_account_id(body.account_id or "default")
    row, _ = _require_live_subscription_control(int(strategy_id), account_id)
    subscription = live_signal_execution_service.set_subscription_status(
        source_strategy_id=int(strategy_id),
        account_id=account_id,
        status="running",
    )
    _upsert_live_strategy_account_binding(
        int(strategy_id),
        account_id=account_id,
        added=True,
        risk_config=subscription.get("risk_config") or {},
        deployment_strategy_id=None,
        clear_deployment_strategy_id=True,
        status="deployed",
    )
    _upsert_live_strategy_setting(
        int(strategy_id),
        added=True,
        account_id=account_id,
        risk_config=subscription.get("risk_config") or {},
        deployment_strategy_id=None,
        clear_deployment_strategy_id=True,
        status="deployed",
    )
    return ok(
        {
            "resumed": True,
            "source_strategy_id": int(strategy_id),
            "live_subscription_id": subscription["id"],
            "strategy": _live_execution_strategy_payload(row),
        }
    )


@router.post("/strategies/{strategy_id}/stop")
async def stop_live_strategy_subscription(strategy_id: int, body: LiveStrategySubscriptionControlBody):
    account_id = live_account_service.validate_account_id(body.account_id or "default")
    row, _ = _require_live_subscription_control(int(strategy_id), account_id, allow_stopped=True)
    subscription = live_signal_execution_service.set_subscription_status(
        source_strategy_id=int(strategy_id),
        account_id=account_id,
        status="stopped",
    )
    still_deployed = bool(
        live_signal_execution_service.list_subscriptions(
            source_strategy_id=int(strategy_id),
            statuses=sorted(live_signal_execution_service.DEPLOYED_STATUSES),
        )
    )
    _upsert_live_strategy_account_binding(
        int(strategy_id),
        account_id=account_id,
        added=True,
        risk_config=subscription.get("risk_config") or {},
        deployment_strategy_id=None,
        clear_deployment_strategy_id=True,
        status="added",
    )
    _upsert_live_strategy_setting(
        int(strategy_id),
        added=True,
        account_id=account_id,
        risk_config=subscription.get("risk_config") or {},
        deployment_strategy_id=None,
        clear_deployment_strategy_id=True,
        status="deployed" if still_deployed else "added",
    )
    return ok(
        {
            "stopped": True,
            "source_strategy_id": int(strategy_id),
            "live_subscription_id": subscription["id"],
            "strategy": _live_execution_strategy_payload(row),
        }
    )


@router.get("/accounts")
async def list_live_accounts():
    return ok({"accounts": live_account_service.list_accounts()})


@router.post("/accounts")
async def create_live_account(body: LiveAccountCreateBody):
    account = live_account_service.create_account(
        name=body.name,
        api_key=body.api_key,
        api_secret=body.api_secret,
        passphrase=body.passphrase,
        testnet=body.testnet,
    )
    return ok({"account": account})


def _live_account_exchange_alias(account_id: str) -> tuple[str, str]:
    normalized = live_account_service.validate_live_deployable_account_id(account_id)
    return normalized, live_account_service.exchange_alias_for_account(normalized)


def _optional_float(value: Any) -> Optional[float]:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _normalize_watch_symbol(symbol: str) -> str:
    normalized = normalize_contract_symbol(str(symbol or "").strip())
    if not normalized:
        raise BadRequestError("symbol 不能为空")
    return normalized


def _okx_inst_id(symbol: str) -> str:
    normalized = _normalize_watch_symbol(symbol)
    base = normalized.split("/", 1)[0]
    return f"{base}-USDT-SWAP"


def _point(ts: Any, value: Any, **extra: Any) -> Dict[str, Any]:
    return {"timestamp": int(_float_value(ts, 0.0)), "value": _optional_float(value), **extra}


def _okx_public_api(exchange_name: str) -> Any:
    try:
        ex = exchange_manager.get_exchange(exchange_name) or exchange_manager.get_exchange("okx")
        return getattr(ex, "exchange", ex)
    except Exception:
        return None


def _extract_okx_rows(raw: Any) -> List[Dict[str, Any]]:
    if isinstance(raw, dict):
        data = raw.get("data")
        if isinstance(data, list):
            return [row for row in data if isinstance(row, dict)]
        return [raw]
    if isinstance(raw, list):
        return [row for row in raw if isinstance(row, dict)]
    return []


def _timeframe_to_okx_period(timeframe: str) -> str:
    value = str(timeframe or "15m").strip().lower()
    return {
        "5m": "5m",
        "15m": "15m",
        "1h": "1H",
        "4h": "4H",
        "1d": "1D",
        "1m": "1m",
    }.get(value, "15m")


async def _call_okx_public_method(exchange_name: str, names: List[str], params: Dict[str, Any]) -> Optional[Any]:
    api = _okx_public_api(exchange_name)
    if api is None:
        return None
    for name in names:
        method = getattr(api, name, None)
        if not callable(method):
            continue
        try:
            return await asyncio.to_thread(method, params)
        except Exception as exc:
            logger.debug("OKX public stat method %s unavailable: %s", name, exc)
    return None


async def _okx_open_interest_points(exchange_name: str, symbol: str, timeframe: str, limit: int) -> Optional[List[Dict[str, Any]]]:
    raw = await _call_okx_public_method(
        exchange_name,
        [
            "publicGetRubikStatContractsOpenInterestVolume",
            "public_get_rubik_stat_contracts_open_interest_volume",
        ],
        {
            "ccy": symbol.split("/", 1)[0],
            "instType": "SWAP",
            "period": _timeframe_to_okx_period(timeframe),
            "limit": str(limit),
        },
    )
    rows = _extract_okx_rows(raw)
    if not rows:
        return None
    points = [
        _point(
            row.get("ts") or row.get("timestamp"),
            row.get("oi") or row.get("openInterest") or row.get("open_interest"),
            volume=_optional_float(row.get("vol") or row.get("volume")),
        )
        for row in rows
    ]
    return [p for p in points if p["timestamp"]]


async def _okx_long_short_ratio_points(exchange_name: str, symbol: str, timeframe: str, limit: int) -> Optional[List[Dict[str, Any]]]:
    raw = await _call_okx_public_method(
        exchange_name,
        [
            "publicGetRubikStatContractsLongShortAccountRatio",
            "public_get_rubik_stat_contracts_long_short_account_ratio",
        ],
        {
            "ccy": symbol.split("/", 1)[0],
            "period": _timeframe_to_okx_period(timeframe),
            "limit": str(limit),
        },
    )
    rows = _extract_okx_rows(raw)
    if not rows:
        return None
    points = [
        _point(
            row.get("ts") or row.get("timestamp"),
            row.get("longShortRatio") or row.get("ratio") or row.get("value"),
            long_account_ratio=_optional_float(row.get("longAccount") or row.get("longAccountRatio")),
            short_account_ratio=_optional_float(row.get("shortAccount") or row.get("shortAccountRatio")),
        )
        for row in rows
    ]
    return [p for p in points if p["timestamp"]]


async def _okx_taker_volume_points(exchange_name: str, symbol: str, timeframe: str, limit: int) -> Optional[List[Dict[str, Any]]]:
    raw = await _call_okx_public_method(
        exchange_name,
        [
            "publicGetRubikStatTakerVolume",
            "public_get_rubik_stat_taker_volume",
            "publicGetRubikStatContractsTakerVolume",
        ],
        {
            "ccy": symbol.split("/", 1)[0],
            "instType": "SWAP",
            "period": _timeframe_to_okx_period(timeframe),
            "limit": str(limit),
        },
    )
    rows = _extract_okx_rows(raw)
    if not rows:
        return None
    points = []
    for row in rows:
        buy = _optional_float(row.get("buyVol") or row.get("buyVolume") or row.get("takerBuyVolume"))
        sell = _optional_float(row.get("sellVol") or row.get("sellVolume") or row.get("takerSellVolume"))
        points.append(
            _point(
                row.get("ts") or row.get("timestamp"),
                (buy or 0.0) - (sell or 0.0) if buy is not None or sell is not None else None,
                buy=buy,
                sell=sell,
            )
        )
    return [p for p in points if p["timestamp"]]


async def _okx_basis_points(exchange_name: str, symbol: str, timeframe: str, limit: int) -> Optional[List[Dict[str, Any]]]:
    inst_id = _okx_inst_id(symbol)
    raw = await _call_okx_public_method(
        exchange_name,
        [
            "publicGetRubikStatContractsBasis",
            "public_get_rubik_stat_contracts_basis",
            "publicGetMarketIndexComponents",
        ],
        {
            "instId": inst_id,
            "period": _timeframe_to_okx_period(timeframe),
            "limit": str(limit),
        },
    )
    rows = _extract_okx_rows(raw)
    if not rows:
        return None
    points = []
    for row in rows:
        basis = _optional_float(row.get("basis") or row.get("premium"))
        index_price = _optional_float(row.get("indexPx") or row.get("index_price"))
        contract_price = _optional_float(row.get("contractPx") or row.get("markPx") or row.get("mark_price"))
        if basis is None and contract_price is not None and index_price is not None:
            basis = contract_price - index_price
        basis_rate = _optional_float(row.get("basisRate") or row.get("rate"))
        if basis_rate is None and basis is not None and index_price:
            basis_rate = basis / index_price
        points.append(
            _point(
                row.get("ts") or row.get("timestamp"),
                basis,
                basis_rate=basis_rate,
                index_price=index_price,
                contract_price=contract_price,
            )
        )
    return [p for p in points if p["timestamp"]]


@router.get("/watchlist")
async def live_watchlist(
    account_id: str = Query("default", description="实盘账户 ID"),
    limit: int = Query(100, ge=1, le=500),
):
    normalized, exchange = _live_account_exchange_alias(account_id)
    candidate_limit = min(2000, max(int(limit) * 5, int(limit)))
    items = live_signal_execution_service.list_watchlist_items(account_id=normalized, limit=candidate_limit)
    positions = await _cached_live_positions(exchange)
    open_symbols = _live_open_position_symbols(positions)
    items = [
        item
        for item in items
        if normalize_contract_symbol(str(item.get("symbol") or "")) in open_symbols
    ][: int(limit)]
    return ok({"account_id": normalized, "exchange": exchange, "items": items})


@router.get("/watchlist/markers")
async def live_watch_markers(
    account_id: str = Query("default", description="实盘账户 ID"),
    symbol: str = Query(..., description="合约交易对"),
    start: Optional[int] = Query(None, description="开始时间戳 ms"),
    end: Optional[int] = Query(None, description="结束时间戳 ms"),
    limit: int = Query(500, ge=1, le=2000),
):
    normalized, exchange = _live_account_exchange_alias(account_id)
    markers = live_signal_execution_service.list_trade_markers(
        account_id=normalized,
        symbol=_normalize_watch_symbol(symbol),
        start=start,
        end=end,
        limit=limit,
    )
    return ok({"account_id": normalized, "exchange": exchange, "symbol": _normalize_watch_symbol(symbol), "markers": markers})


@router.get("/watchlist/market")
async def live_watch_market(
    account_id: str = Query("default", description="实盘账户 ID"),
    symbol: str = Query(..., description="合约交易对"),
    timeframe: str = Query("15m", description="K线周期"),
    limit: int = Query(240, ge=20, le=1000),
):
    normalized, exchange = _live_account_exchange_alias(account_id)
    normalized_symbol = _normalize_watch_symbol(symbol)
    public_exchange = "okx"
    ticker, klines, orderbook, trades, positions = await asyncio.gather(
        market_domain_service.get_ticker(public_exchange, normalized_symbol),
        market_domain_service.get_klines(public_exchange, normalized_symbol, timeframe=timeframe, limit=limit),
        market_domain_service.get_orderbook(public_exchange, normalized_symbol, limit=20),
        market_domain_service.get_trades(public_exchange, normalized_symbol, limit=50),
        _cached_live_positions(exchange, normalized_symbol),
    )
    return ok(
        {
            "account_id": normalized,
            "exchange": exchange,
            "symbol": normalized_symbol,
            "timeframe": timeframe,
            "ticker": ticker,
            "klines": klines,
            "orderbook": orderbook,
            "recent_trades": trades,
            "positions": positions,
        }
    )


@router.get("/watchlist/derivatives-data")
async def live_watch_derivatives_data(
    account_id: str = Query("default", description="实盘账户 ID"),
    symbol: str = Query(..., description="合约交易对"),
    timeframe: str = Query("15m", description="统计周期"),
    limit: int = Query(120, ge=20, le=500),
):
    normalized, exchange = _live_account_exchange_alias(account_id)
    normalized_symbol = _normalize_watch_symbol(symbol)
    public_exchange = "okx"
    funding_history, open_interest, long_short_ratio, taker_volume, basis = await asyncio.gather(
        funding_domain_service.get_funding_history(public_exchange, normalized_symbol, limit=limit),
        _okx_open_interest_points(public_exchange, normalized_symbol, timeframe, limit),
        _okx_long_short_ratio_points(public_exchange, normalized_symbol, timeframe, limit),
        _okx_taker_volume_points(public_exchange, normalized_symbol, timeframe, limit),
        _okx_basis_points(public_exchange, normalized_symbol, timeframe, limit),
    )
    funding_points = [
        _point(
            row.get("timestamp") or row.get("funding_time") or row.get("fundingTime"),
            row.get("funding_rate") or row.get("rate") or row.get("current_rate"),
            mark_price=_optional_float(row.get("mark_price") or row.get("markPrice")),
        )
        for row in (funding_history or [])
        if isinstance(row, dict)
    ]
    return ok(
        {
            "account_id": normalized,
            "exchange": exchange,
            "symbol": normalized_symbol,
            "timeframe": timeframe,
            "open_interest": {"points": open_interest},
            "funding_rate": {"points": [p for p in funding_points if p["timestamp"]]},
            "long_short_ratio": {"points": long_short_ratio},
            "taker_volume": {"points": taker_volume},
            "basis": {"points": basis},
        }
    )


def _position_info(row: Dict[str, Any]) -> Dict[str, Any]:
    info = row.get("info")
    return info if isinstance(info, dict) else {}


def _live_position_symbol(row: Dict[str, Any]) -> str:
    info = _position_info(row)
    raw = (
        row.get("symbol")
        or row.get("instId")
        or row.get("instrument_id")
        or info.get("instId")
        or ""
    )
    return normalize_contract_symbol(str(raw)) if raw else ""


def _live_position_size(row: Dict[str, Any]) -> float:
    info = _position_info(row)
    for key in ("contracts", "size", "amount", "pos", "base_amount"):
        if key in row:
            size = _float_value(row.get(key), 0.0)
            if abs(size) > 1e-12:
                return size
    for key in ("pos", "availPos"):
        if key in info:
            size = _float_value(info.get(key), 0.0)
            if abs(size) > 1e-12:
                return size
    return 0.0


def _live_position_side(row: Dict[str, Any]) -> str:
    info = _position_info(row)
    raw_pos_side = (
        row.get("pos_side")
        or row.get("posSide")
        or row.get("position_side")
        or info.get("posSide")
        or ""
    )
    pos_side = str(raw_pos_side).strip().lower()
    raw_side = row.get("side") or info.get("side") or ""
    side = str(raw_side).strip().lower()
    if pos_side in {"long", "short"}:
        return pos_side
    if side in {"long", "short"}:
        return side
    if side == "buy":
        return "long"
    if side == "sell":
        return "short"
    if pos_side == "net":
        signed = _live_position_size(row)
        return "long" if signed > 0 else "short" if signed < 0 else ""
    signed = _live_position_size(row)
    if signed > 0:
        return "long"
    if signed < 0:
        return "short"
    return ""


def _live_contract_position_targets(
    positions: List[Dict[str, Any]],
    requested_symbol: Optional[str] = None,
) -> List[Dict[str, str]]:
    normalized_requested = normalize_contract_symbol(requested_symbol) if requested_symbol else None
    seen: set[tuple[str, str]] = set()
    targets: List[Dict[str, str]] = []
    for row in positions or []:
        symbol = _live_position_symbol(row)
        if not symbol:
            continue
        if normalized_requested and symbol != normalized_requested:
            continue
        if abs(_live_position_size(row)) <= 1e-12:
            continue
        side = _live_position_side(row)
        if side not in {"long", "short"}:
            continue
        key = (symbol, side)
        if key in seen:
            continue
        seen.add(key)
        targets.append({"symbol": symbol, "side": side})
    return targets


def _live_open_position_symbols(positions: List[Dict[str, Any]]) -> set[str]:
    symbols: set[str] = set()
    for row in positions or []:
        if abs(_live_position_size(row)) <= 1e-12:
            continue
        symbol = _live_position_symbol(row)
        if symbol:
            symbols.add(symbol)
    return symbols


@router.get("/accounts/{account_id}/balance")
async def live_account_balance(account_id: str):
    normalized, exchange = _live_account_exchange_alias(account_id)
    balance = await _cached_live_balance(exchange)
    return ok({"account_id": normalized, "exchange": exchange, "balance": balance})


@router.get("/accounts/{account_id}/balance/detail")
async def live_account_balance_detail(account_id: str):
    normalized, exchange = _live_account_exchange_alias(account_id)
    detail, return_rates = await asyncio.gather(
        _cached_live_balance_detail(exchange),
        _cached_live_return_rates(exchange),
    )
    return ok({"account_id": normalized, "exchange": exchange, **detail, "return_rates": return_rates})


@router.get("/accounts/{account_id}/positions")
async def live_account_positions(
    account_id: str,
    symbol: Optional[str] = Query(None, description="交易对"),
):
    normalized, exchange = _live_account_exchange_alias(account_id)
    positions, balances = await asyncio.gather(
        _cached_live_positions(exchange, symbol),
        _cached_live_balance(exchange),
    )
    positions = [
        *positions,
        *_spot_positions_from_balances(balances, exchange_name=exchange, symbol=symbol),
    ]
    return ok({"account_id": normalized, "exchange": exchange, "positions": positions})


@router.post("/accounts/{account_id}/positions/close")
async def live_account_close_position(account_id: str, body: LivePositionCloseBody):
    if not body.confirm_live_risk:
        raise BadRequestError("平仓需要二次确认 confirm_live_risk=true")
    normalized, exchange = _live_account_exchange_alias(account_id)
    symbol = normalize_contract_symbol(body.symbol) if body.symbol else ""

    if body.close_all:
        positions = await trading_service.get_positions(exchange, symbol or None)
        targets = _live_contract_position_targets(positions, symbol or None)
        if not targets:
            raise BadRequestError("当前账户没有可平的合约持仓")
    else:
        side = str(body.side or "").strip().lower()
        if not symbol or side not in {"long", "short"}:
            raise BadRequestError("平仓需要指定合约 symbol 和方向 side=long/short")
        targets = [{"symbol": symbol, "side": side}]

    broker = LiveContractBroker(
        strategy_id=0,
        exchange_name=exchange,
        symbols=sorted({target["symbol"] for target in targets}),
        config={
            "is_paper_trading": False,
            "market_type": "swap",
            "live_order_type": "market",
        },
    )
    results: List[Dict[str, Any]] = []
    for target in targets:
        result = await broker.close_contract(target["symbol"], target["side"], ratio=1.0)
        results.append(dict(result))
    closed = sum(1 for result in results if str(result.get("status") or "").lower() in {"filled", "closed", "submitted", "open"})
    _clear_live_private_read_cache(exchange)
    return ok(
        {
            "account_id": normalized,
            "exchange": exchange,
            "closed": closed,
            "results": results,
        }
    )


@router.get("/accounts/{account_id}/orders/open")
async def live_account_open_orders(
    account_id: str,
    symbol: Optional[str] = Query(None, description="交易对"),
):
    normalized, exchange = _live_account_exchange_alias(account_id)
    orders = await _cached_live_open_orders(exchange, symbol)
    return ok({"account_id": normalized, "exchange": exchange, "orders": orders})


def _order_history_sort_ms(order: Dict[str, Any]) -> int:
    for key in (
        "timestamp",
        "updated_timestamp",
        "created_timestamp",
        "fill_timestamp",
        "uTime",
        "cTime",
        "fillTime",
    ):
        value = order.get(key)
        try:
            if value is not None and value != "":
                return int(float(value))
        except (TypeError, ValueError):
            continue
    for key in ("updated_datetime", "created_datetime", "fill_datetime", "datetime"):
        value = order.get(key)
        if not value:
            continue
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return int(parsed.timestamp() * 1000)
        except ValueError:
            continue
    return 0


def _merge_live_order_history(
    exchange_orders: List[Dict[str, Any]],
    execution_failure_orders: List[Dict[str, Any]],
    limit: int,
) -> List[Dict[str, Any]]:
    merged = [*exchange_orders, *execution_failure_orders]
    merged.sort(key=_order_history_sort_ms, reverse=True)
    return merged[: int(max(1, limit))]


@router.get("/accounts/{account_id}/orders/history")
async def live_account_order_history(
    account_id: str,
    symbol: Optional[str] = Query(None, description="交易对"),
    limit: int = Query(50, ge=1, le=200),
):
    normalized, exchange = _live_account_exchange_alias(account_id)
    orders = await _cached_live_order_history(exchange, symbol, limit)
    orders = live_signal_execution_service.enrich_orders_with_attribution(
        account_id=normalized,
        orders=orders,
    )
    failed_execution_orders = live_signal_execution_service.list_failed_execution_orders(
        account_id=normalized,
        symbol=symbol,
        limit=limit,
    )
    orders = _merge_live_order_history(orders, failed_execution_orders, limit)
    return ok({"account_id": normalized, "exchange": exchange, "orders": orders})


async def _run_preflight_checks(
    *,
    strategy_id: int,
    row: Optional[Dict[str, Any]],
    exchange: str,
    timeframe: str,
    dry_run: bool,
    symbol: Optional[str] = None,
    symbol_scope: str = "strategy_symbols",
) -> Dict[str, Any]:
    checks: List[Dict[str, Any]] = []
    account: Optional[Dict[str, Any]] = None
    eligible_symbols: List[str] = []
    excluded_symbols: List[str] = []
    ex = exchange_manager.get_exchange(exchange)
    checks.append(
        {
            "item": "策略存在性",
            "passed": row is not None,
            "detail": None if row else f"未找到策略 #{strategy_id}",
        }
    )

    exchange_probe = ex is not None
    checks.append(
        {
            "item": f"行情连接 ({exchange})",
            "passed": exchange_probe,
            "detail": None if exchange_probe else "交易所实例不可用（请检查代理与 API）",
        }
    )

    if not dry_run:
        risk = strategy_engine.get_risk_status()
        circuit = bool(risk.get("circuit_breaker"))
        checks.append(
            {
                "item": "全局风控熔断状态",
                "passed": not circuit,
                "detail": (
                    "当前未触发全局熔断"
                    if not circuit
                    else f"全局熔断中：{risk.get('circuit_breaker_reason') or '未提供原因'}"
                ),
            }
        )

    probe_symbols: List[str] = []
    row_cfg: Dict[str, Any] = {}
    if row:
        row_cfg = row.get("config") or {}
        if not isinstance(row_cfg, dict):
            row_cfg = {}
        probe_symbols = _defined_symbols(row, row_cfg, symbol)
    if not probe_symbols:
        probe_symbols = [symbol or "BTC/USDT"]

    if ex:
        checks.append(
            await asyncio.get_running_loop().run_in_executor(
                None,
                lambda: _market_rules_check(ex, probe_symbols),
            )
        )
        failed: List[str] = []
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        max_stale_ms = max(_timeframe_seconds(timeframe) * 5 * 1000, 300_000)
        for sym in probe_symbols:
            try:
                ohlcv = await asyncio.get_running_loop().run_in_executor(
                    None,
                    lambda s=sym: ex.fetch_ohlcv(s, timeframe, limit=3),
                )
                if not ohlcv or len(ohlcv) < 1:
                    failed.append(f"{sym}: 无数据")
                    continue
                latest_ts = _kline_timestamp_ms(ohlcv[-1])
                if latest_ts is None:
                    failed.append(f"{sym}: K 线缺少时间戳")
                elif now_ms - latest_ts > max_stale_ms:
                    stale_min = (now_ms - latest_ts) / 60_000
                    failed.append(f"{sym}: 最新 K 线滞后 {stale_min:.0f} 分钟")
            except Exception as e:
                failed.append(f"{sym}: {e}")
        has_bar = not failed
        bar_err = "；".join(failed[:5])
        if len(failed) > 5:
            bar_err += f"；另有 {len(failed) - 5} 个失败"
        checks.append(
            {
                "item": f"K 线拉取（策略定义 {len(probe_symbols)} 个交易对）",
                "passed": has_bar,
                "detail": None if has_bar else (bar_err or "无数据"),
            }
        )
        if not dry_run:
            dynamic_filter = symbol_scope == "dynamic_runtime_symbols"
            order_book_check = await _order_book_liquidity_check(
                ex,
                probe_symbols,
                allow_dynamic_filter=dynamic_filter,
                min_remaining_symbols=(
                    _dynamic_preflight_min_symbols(row_cfg, len(probe_symbols)) if dynamic_filter else 0
                ),
            )
            checks.append(order_book_check)
            if dynamic_filter and order_book_check.get("passed"):
                raw_eligible = order_book_check.get("eligible_symbols")
                raw_excluded = order_book_check.get("excluded_symbols")
                if isinstance(raw_eligible, list) and raw_eligible:
                    eligible_symbols = [str(sym) for sym in raw_eligible if str(sym).strip()]
                    probe_symbols = eligible_symbols
                if isinstance(raw_excluded, list):
                    excluded_symbols = [str(sym) for sym in raw_excluded if str(sym).strip()]
    else:
        checks.append({"item": "K 线拉取", "passed": False, "detail": "跳过（无交易所实例）"})

    if not dry_run:
        try:
            balances = await trading_service.get_balance(exchange)
            usdt = next(
                (
                    item
                    for item in balances
                    if isinstance(item, dict) and str(item.get("currency", "")).upper() == "USDT"
                ),
                None,
            )
            free_usdt = float((usdt or {}).get("free") or 0.0)
            total_usdt = float((usdt or {}).get("total") or 0.0)
            used_usdt = float((usdt or {}).get("used") or max(total_usdt - free_usdt, 0.0))
            account = {
                "exchange": exchange,
                "currency": "USDT",
                "free_usdt": free_usdt,
                "total_usdt": total_usdt,
                "used_usdt": used_usdt,
            }
            checks.append(
                {
                    "item": "实盘账户权限与 USDT 余额",
                    "passed": free_usdt > 0,
                    "detail": (
                        f"USDT 可用 {free_usdt:.2f} / 总额 {total_usdt:.2f}"
                        if free_usdt > 0
                        else "未读取到可用 USDT，请检查 OKX API Key、权限和账户资金"
                    ),
                    "account": account,
                }
            )
            min_cash = _configured_min_order_notional(row_cfg)
            checks.append(
                {
                    "item": "实盘最小下单资金",
                    "passed": free_usdt >= min_cash,
                    "detail": (
                        f"USDT 可用 {free_usdt:.2f}，满足最小下单资金 {min_cash:.2f}"
                        if free_usdt >= min_cash
                        else f"USDT 可用 {free_usdt:.2f}，低于最小下单资金 {min_cash:.2f}"
                    ),
                    "account": account,
                }
            )
        except Exception as e:
            checks.append(
                {
                    "item": "实盘账户权限与 USDT 余额",
                    "passed": False,
                    "detail": f"余额读取失败：{e}",
                }
            )
        try:
            conflicts: List[str] = []
            for sym in probe_symbols:
                orders = await trading_service.get_open_orders(exchange, sym)
                active_orders = [
                    order
                    for order in (orders or [])
                    if isinstance(order, dict)
                    and str(order.get("status") or "open").lower()
                    not in {"closed", "canceled", "cancelled"}
                ]
                if active_orders:
                    conflicts.append(f"{sym}: {len(active_orders)} 个未成交挂单")
            checks.append(
                {
                    "item": "实盘未成交挂单冲突",
                    "passed": not conflicts,
                    "detail": (
                        "策略交易对当前无未成交挂单"
                        if not conflicts
                        else "；".join(conflicts[:5])
                        + (f"；另有 {len(conflicts) - 5} 个交易对存在挂单" if len(conflicts) > 5 else "")
                    ),
                }
            )
        except Exception as e:
            checks.append(
                {
                    "item": "实盘未成交挂单冲突",
                    "passed": False,
                    "detail": f"未成交挂单读取失败：{e}",
                }
            )

    all_passed = all(c["passed"] for c in checks)
    result: Dict[str, Any] = {"all_passed": all_passed, "checks": checks, "account": account}
    if eligible_symbols:
        result["eligible_symbols"] = eligible_symbols
    if excluded_symbols:
        result["excluded_symbols"] = excluded_symbols
    return result


@router.post("/pre_flight")
async def live_pre_flight(body: PreFlightBody):
    sid = _parse_strategy_id(body.strategy)
    row = db.get_strategy_by_id(sid)
    timeframe = _strategy_defined_timeframe(row)
    return ok(
        await _run_preflight_checks(
            strategy_id=sid,
            row=row,
            exchange=body.exchange,
            timeframe=timeframe,
            dry_run=body.dry_run,
            symbol=body.symbol,
        )
    )


@router.post("/promote/preflight")
async def promote_to_live_preflight(body: PromoteToLiveBody):
    return ok(await _run_promote_preflight(body))


@router.post("/promote")
async def promote_to_live(body: PromoteToLiveBody):
    if not body.confirm_paper_reviewed or not body.confirm_live_risk:
        raise BadRequestError("部署实盘需要确认已复核模拟盘表现，并确认真实资金风险")

    prepared = _prepare_promoted_live_candidate(body)
    source = prepared["source"]
    source_cfg = prepared["source_cfg"]
    if source_cfg.get("is_paper_trading") is False:
        raise BadRequestError("来源策略已经是实盘策略，不能再次部署")

    eng = strategy_engine.get_strategy_status(int(body.source_strategy_id))
    if eng and eng.get("status") == "running" and source.get("status") == "running":
        # 允许从正在运行的模拟盘复制；只是不复用该 row，不污染模拟盘。
        pass

    preflight = await _run_promote_preflight(body, prepared=prepared)
    if not preflight["all_passed"]:
        return ok(
            {
                "promoted": False,
                "started": False,
                "source_strategy_id": int(body.source_strategy_id),
                "preflight": preflight,
            }
        )

    symbols = prepared["symbols"]
    live_cfg = prepared["live_cfg"]
    live_id = _insert_promoted_strategy(
        source,
        exchange=body.exchange,
        symbols=symbols,
        config=live_cfg,
    )
    strategy_engine.drop_cached_context(live_id)
    started = False
    if body.start_immediately:
        started = await strategy_engine.start_strategy(live_id)
        if not started:
            db.update_strategy_status(live_id, "stopped")
            raise BadRequestError("小资金实盘试运行已创建，但启动失败（可能处于熔断或配置无效）")

    global _active_strategy_id
    _active_strategy_id = live_id
    return ok(
        {
            "promoted": True,
            "started": started,
            "source_strategy_id": int(body.source_strategy_id),
            "live_strategy_id": live_id,
            "preflight": preflight,
            "trial": {
                "initial_equity": _float_value(live_cfg.get("initial_capital"), 0.0),
                "initial_equity_source": live_cfg.get("initial_capital_source") or "request",
                "account": prepared.get("account"),
                "loop_interval_sec": int(body.loop_interval),
                "symbols": symbols,
            },
        }
    )


@router.post("/test_telegram")
async def live_test_telegram(body: TelegramTestBody):
    sent = await telegram_notifier.send_message(body.message)
    return ok({"sent": sent})
