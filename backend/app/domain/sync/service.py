"""Data sync domain service."""
from __future__ import annotations

import copy
import json
import re
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from app.db.local_db import db_instance as db
from app.services.data_sync_service import (
    data_sync_service,
    DEFAULT_SYMBOLS,
    DEFAULT_TIMEFRAMES,
    SyncProgress,
    SyncStatus,
)
from app.services.kline_file_store import kline_store


CUSTOM_SYMBOLS_SETTING_KEY = "data_sync_custom_symbols"
REMOVED_DEFAULT_SYMBOLS_SETTING_KEY = "data_sync_removed_default_symbols"
SCHEDULE_SETTING_KEY = "data_sync_schedule_config"
SPOT_USDT_SYMBOL_RE = re.compile(r"^[A-Z0-9]{1,30}/USDT$")
CONTRACT_USDT_SYMBOL_RE = re.compile(r"^[A-Z0-9]{1,30}/USDT:USDT$")
MIN_SCHEDULE_INTERVAL_MINUTES = 5
MAX_SCHEDULE_INTERVAL_MINUTES = 24 * 60
TABLE_STATS_CACHE_TTL_SEC = 15.0


def _market_type_for_symbol(symbol: Any) -> str:
    normalized = str(symbol or "").upper()
    if normalized.endswith(":USDT") or normalized.endswith("-USDT-SWAP"):
        return "swap"
    return "spot"


def _dedupe_symbols(symbols: List[str]) -> List[str]:
    result: List[str] = []
    seen = set()
    for symbol in symbols:
        if symbol in seen:
            continue
        seen.add(symbol)
        result.append(symbol)
    return result


class SyncDomainService:
    def __init__(self) -> None:
        self._table_stats_cache: Optional[tuple[float, Dict[str, Any]]] = None

    def _clear_table_stats_cache(self) -> None:
        self._table_stats_cache = None

    def _get_table_stats_cache(self) -> Optional[Dict[str, Any]]:
        cached = self._table_stats_cache
        if not cached:
            return None
        cached_at, payload = cached
        if time.monotonic() - cached_at > TABLE_STATS_CACHE_TTL_SEC:
            self._table_stats_cache = None
            return None
        return copy.deepcopy(payload)

    def _set_table_stats_cache(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        self._table_stats_cache = (time.monotonic(), copy.deepcopy(payload))
        return payload

    def _schedule_defaults(self) -> Dict[str, Any]:
        return {
            "enabled": False,
            "interval_minutes": 240,
            "history_days": 7,
            "symbols": [],
            "timeframes": DEFAULT_TIMEFRAMES,
            "last_run_at": None,
            "last_started_at": None,
            "last_finished_at": None,
            "last_job_id": None,
            "last_error": None,
            "updated_at": None,
        }

    @staticmethod
    def _parse_dt(value: Any) -> Optional[datetime]:
        if not value:
            return None
        try:
            return datetime.fromisoformat(str(value))
        except ValueError:
            return None

    @staticmethod
    def _clamp_int(value: Any, default: int, minimum: int, maximum: int) -> int:
        try:
            parsed = int(float(value))
        except (TypeError, ValueError):
            parsed = default
        return max(minimum, min(maximum, parsed))

    def _normalize_schedule_timeframes(self, values: Any) -> List[str]:
        allowed = set(DEFAULT_TIMEFRAMES)
        raw_values = values if isinstance(values, list) else []
        result: List[str] = []
        for value in raw_values:
            timeframe = str(value or "").strip()
            if timeframe in allowed and timeframe not in result:
                result.append(timeframe)
        return result or list(DEFAULT_TIMEFRAMES)

    def _normalize_schedule_symbols(self, values: Any) -> List[str]:
        if not isinstance(values, list):
            return []
        result: List[str] = []
        configured = set(self._configured_symbols())
        for value in values:
            try:
                symbol = self._normalize_trade_symbol(value)
            except ValueError:
                continue
            if symbol in configured and symbol not in result:
                result.append(symbol)
        return result

    def _raw_schedule_config(self) -> Dict[str, Any]:
        raw = db.get_app_setting(SCHEDULE_SETTING_KEY, "{}")
        try:
            data = json.loads(raw or "{}")
        except (TypeError, json.JSONDecodeError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        return {**self._schedule_defaults(), **data}

    def _save_schedule_config(self, config: Dict[str, Any]) -> None:
        db.set_app_setting(
            SCHEDULE_SETTING_KEY,
            json.dumps(config, ensure_ascii=False, sort_keys=True),
        )

    def _normalize_schedule_config(self, config: Dict[str, Any]) -> Dict[str, Any]:
        normalized = {**self._schedule_defaults(), **(config or {})}
        normalized["enabled"] = bool(normalized.get("enabled"))
        normalized["interval_minutes"] = self._clamp_int(
            normalized.get("interval_minutes"),
            240,
            MIN_SCHEDULE_INTERVAL_MINUTES,
            MAX_SCHEDULE_INTERVAL_MINUTES,
        )
        normalized["history_days"] = self._clamp_int(normalized.get("history_days"), 7, 1, 365)
        normalized["symbols"] = self._normalize_schedule_symbols(normalized.get("symbols"))
        normalized["timeframes"] = self._normalize_schedule_timeframes(normalized.get("timeframes"))
        for key in ("last_run_at", "last_started_at", "last_finished_at", "last_job_id", "last_error", "updated_at"):
            normalized[key] = normalized.get(key) or None
        return normalized

    def schedule_config(self) -> Dict[str, Any]:
        config = self._normalize_schedule_config(self._raw_schedule_config())
        last_run = self._parse_dt(config.get("last_run_at"))
        next_run = last_run + timedelta(minutes=config["interval_minutes"]) if last_run else None
        if config["enabled"] and next_run is None:
            next_run = datetime.now()
        elif config["enabled"] and next_run and next_run < datetime.now():
            next_run = datetime.now()
        return {
            **config,
            "next_run_at": next_run.isoformat() if next_run else None,
        }

    def update_schedule_config(self, updates: Dict[str, Any]) -> Dict[str, Any]:
        current = self._normalize_schedule_config(self._raw_schedule_config())
        allowed = {"enabled", "interval_minutes", "history_days", "symbols", "timeframes"}
        for key, value in (updates or {}).items():
            if key in allowed:
                current[key] = value
        current["updated_at"] = datetime.now().isoformat()
        current = self._normalize_schedule_config(current)
        self._save_schedule_config(current)
        return self.schedule_config()

    def _mark_schedule(self, **updates: Any) -> Dict[str, Any]:
        current = self._normalize_schedule_config(self._raw_schedule_config())
        current.update(updates)
        self._save_schedule_config(current)
        return current

    async def run_scheduled_if_due(self) -> Dict[str, Any]:
        config = self.schedule_config()
        if not config["enabled"]:
            return {"skipped": "disabled"}
        if self.is_running():
            return {"skipped": "sync_running"}

        now = datetime.now()
        last_run = self._parse_dt(config.get("last_run_at"))
        if last_run and now - last_run < timedelta(minutes=config["interval_minutes"]):
            return {"skipped": "not_due", "next_run_at": config.get("next_run_at")}

        payload = {
            "exchange": "okx",
            "symbols": config["symbols"] or self._configured_symbols(),
            "timeframes": config["timeframes"],
            "history_days": config["history_days"],
        }
        try:
            job = self.create_job(payload, exchange="okx", history_days=config["history_days"])
            self._mark_schedule(
                last_started_at=now.isoformat(),
                last_run_at=now.isoformat(),
                last_job_id=job["job_id"],
                last_error=None,
            )
            result = await self.run_job(job["job_id"])
            self._mark_schedule(
                last_finished_at=datetime.now().isoformat(),
                last_error=None if result.get("status") == "completed" else result.get("status"),
            )
            return {"started": True, **result}
        except Exception as exc:
            self._mark_schedule(
                last_finished_at=datetime.now().isoformat(),
                last_error=str(exc),
            )
            raise

    def _normalize_trade_symbol(self, value: Any) -> str:
        raw = str(value or "").strip().upper()
        if not raw:
            raise ValueError("请输入交易对")

        if raw.endswith("-USDT-SWAP"):
            base = raw[: -len("-USDT-SWAP")]
            raw = f"{base}/USDT:USDT"
        elif raw.endswith("/USDT/SWAP"):
            base = raw[: -len("/USDT/SWAP")]
            raw = f"{base}/USDT:USDT"

        if ":" in raw:
            if not CONTRACT_USDT_SYMBOL_RE.fullmatch(raw):
                raise ValueError("合约交易对格式应为 BASE/USDT:USDT，例如 OPENAI/USDT:USDT")
            return raw

        raw = raw.replace("-", "/")
        if "/" not in raw:
            raw = f"{raw[:-4]}/USDT" if raw.endswith("USDT") and len(raw) > 4 else f"{raw}/USDT"

        parts = [p for p in raw.split("/") if p]
        if len(parts) != 2 or parts[1] != "USDT":
            raise ValueError("交易对格式应为 BASE/USDT，例如 PEPE/USDT")

        symbol = f"{parts[0]}/USDT"
        if not SPOT_USDT_SYMBOL_RE.fullmatch(symbol):
            raise ValueError("交易对只支持 USDT 现货格式，例如 PEPE/USDT")
        return symbol

    def _normalize_spot_symbol(self, value: Any) -> str:
        symbol = self._normalize_trade_symbol(value)
        if ":" in symbol:
            raise ValueError("交易对只支持 USDT 现货格式，例如 PEPE/USDT")
        return symbol

    def _custom_symbols(self) -> List[str]:
        raw = db.get_app_setting(CUSTOM_SYMBOLS_SETTING_KEY, "[]")
        try:
            values = json.loads(raw or "[]")
        except (TypeError, json.JSONDecodeError):
            values = []

        if not isinstance(values, list):
            values = []

        symbols: List[str] = []
        for value in values:
            try:
                symbol = self._normalize_trade_symbol(value)
            except ValueError:
                continue
            if symbol not in DEFAULT_SYMBOLS:
                symbols.append(symbol)
        return _dedupe_symbols(symbols)

    def _removed_default_symbols(self) -> List[str]:
        raw = db.get_app_setting(REMOVED_DEFAULT_SYMBOLS_SETTING_KEY, "[]")
        try:
            values = json.loads(raw or "[]")
        except (TypeError, json.JSONDecodeError):
            values = []

        if not isinstance(values, list):
            values = []

        symbols: List[str] = []
        for value in values:
            try:
                symbol = self._normalize_trade_symbol(value)
            except ValueError:
                continue
            if symbol in DEFAULT_SYMBOLS:
                symbols.append(symbol)
        return _dedupe_symbols(symbols)

    def _set_custom_symbols(self, symbols: List[str]) -> None:
        db.set_app_setting(
            CUSTOM_SYMBOLS_SETTING_KEY,
            json.dumps(_dedupe_symbols([s for s in symbols if s not in DEFAULT_SYMBOLS]), ensure_ascii=False),
        )

    def _set_removed_default_symbols(self, symbols: List[str]) -> None:
        db.set_app_setting(
            REMOVED_DEFAULT_SYMBOLS_SETTING_KEY,
            json.dumps(_dedupe_symbols([s for s in symbols if s in DEFAULT_SYMBOLS]), ensure_ascii=False),
        )

    def _configured_symbols(self) -> List[str]:
        removed_symbols = set(self._removed_default_symbols())
        defaults = [symbol for symbol in DEFAULT_SYMBOLS if symbol not in removed_symbols]
        return _dedupe_symbols([*defaults, *self._custom_symbols()])

    def status(self) -> Dict[str, Any]:
        return data_sync_service.get_sync_status()

    def jobs(self, *, limit: int = 20, include_items: bool = True) -> Dict[str, Any]:
        return data_sync_service.list_sync_jobs(limit=limit, include_items=include_items)

    def config(self) -> Dict[str, Any]:
        return {
            "default_symbols": self._configured_symbols(),
            "default_timeframes": DEFAULT_TIMEFRAMES,
            "default_history_days": 365,
        }

    def add_symbol(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        symbol = self._normalize_trade_symbol(payload.get("symbol"))
        custom_symbols = self._custom_symbols()
        removed_symbols = self._removed_default_symbols()
        known_symbols = set(self._configured_symbols())
        added = symbol not in known_symbols

        if symbol in DEFAULT_SYMBOLS:
            if symbol in removed_symbols:
                self._set_removed_default_symbols([s for s in removed_symbols if s != symbol])
        elif added:
            custom_symbols.append(symbol)
            self._set_custom_symbols(custom_symbols)

        return {
            "symbol": symbol,
            "added": added,
            "default_symbols": self._configured_symbols(),
        }

    def remove_symbol(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        symbol = self._normalize_trade_symbol(payload.get("symbol"))
        custom_symbols = self._custom_symbols()
        removed_symbols = self._removed_default_symbols()
        configured_symbols = set(self._configured_symbols())
        removed = symbol in configured_symbols

        if symbol in custom_symbols:
            self._set_custom_symbols([s for s in custom_symbols if s != symbol])
        elif symbol in DEFAULT_SYMBOLS and symbol not in removed_symbols:
            self._set_removed_default_symbols([*removed_symbols, symbol])

        return {
            "symbol": symbol,
            "removed": removed,
            "default_symbols": self._configured_symbols(),
        }

    def available_data(self, exchange: Optional[str] = None) -> List[Dict]:
        return data_sync_service.get_available_data(exchange)

    def is_running(self) -> bool:
        return bool(self.status().get("is_running"))

    def create_job(self, payload: Dict[str, Any], *, exchange: Optional[str] = None, history_days: int = 365) -> Dict[str, Any]:
        return data_sync_service.create_sync_job(
            exchange_name=exchange or payload.get("exchange") or "okx",
            symbols=payload.get("symbols") or self._configured_symbols(),
            timeframes=payload.get("timeframes"),
            history_days=payload.get("history_days") or history_days,
            start_date=payload.get("start_date"),
            end_date=payload.get("end_date"),
        )

    async def run_job(self, job_id: str) -> Dict[str, Any]:
        job = await data_sync_service.run_sync_job(job_id)
        return {
            "job_id": job_id,
            "exchange": job.exchange,
            "status": job.status,
            "total_fetched": job.total_records_fetched,
            "total_inserted": job.total_records_inserted,
            "errors": job.errors,
        }

    async def start(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        job = self.create_job(payload, history_days=365)
        return await self.run_job(job["job_id"])

    async def sync_one(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        job = data_sync_service.create_sync_job(
            exchange_name=payload.get("exchange") or "okx",
            symbols=[payload["symbol"]],
            timeframes=[payload["timeframe"]],
            history_days=payload.get("history_days") or 365,
            start_date=payload.get("start_date"),
            end_date=payload.get("end_date"),
        )
        result = await data_sync_service.run_sync_job(job["job_id"])
        progress = result.progress[0] if result.progress else SyncProgress(
            exchange=payload.get("exchange") or "okx",
            symbol=payload["symbol"],
            timeframe=payload["timeframe"],
            status=SyncStatus.ERROR,
            error="同步任务没有返回进度",
        )
        return {
            "job_id": job["job_id"],
            "exchange": progress.exchange,
            "symbol": progress.symbol,
            "timeframe": progress.timeframe,
            "status": progress.status.value,
            "total_fetched": progress.total_fetched,
            "total_inserted": progress.total_inserted,
            "error": progress.error,
        }

    async def daily_update(self, exchange: str = "okx", payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        payload = payload or {}
        job = self.create_job(payload, exchange=exchange, history_days=7)
        return await self.run_job(job["job_id"])

    def delete_data(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        symbol = payload.get("symbol")
        if not symbol:
            raise ValueError("仅支持按 symbol 删除数据")
        result = data_sync_service.delete_klines(
            exchange_name=payload.get("exchange") or "okx",
            symbol=symbol,
            timeframe=payload.get("timeframe"),
        )
        self._clear_table_stats_cache()
        return result

    def table_stats(self) -> Dict[str, Any]:
        cached = self._get_table_stats_cache()
        if cached is not None:
            return cached

        stats_by_key: Dict[tuple, Dict[str, Any]] = {}

        metas = [m for m in db.get_all_sync_metadata() if m.get("data_type") == "kline"]
        for meta in metas:
            exchange = meta.get("exchange")
            symbol = meta.get("symbol")
            timeframe = meta.get("timeframe")
            if not exchange or not symbol or not timeframe:
                continue
            key = (exchange, symbol, timeframe)
            stats_by_key[key] = {
                "table_name": "kline_file_store",
                "timeframe": timeframe,
                "exchange": exchange,
                "symbol": symbol,
                "record_count": int(meta.get("total_records") or 0),
                "first_timestamp": meta.get("first_timestamp"),
                "last_timestamp": meta.get("last_timestamp"),
            }

        # Compatibility fallback for older deployments before the file K-line store.
        # Once file metadata exists, avoid scanning legacy SQLite K-line tables on
        # every Data Manager load because production can contain tens of millions
        # of historical rows there.
        if not stats_by_key:
            for stat in db.get_kline_table_stats():
                key = (stat.get("exchange"), stat.get("symbol"), stat.get("timeframe"))
                if key not in stats_by_key:
                    stats_by_key[key] = stat

        stats = sorted(
            stats_by_key.values(),
            key=lambda s: (str(s.get("exchange")), str(s.get("symbol")), str(s.get("timeframe"))),
        )
        market_stats = {
            "swap": {"total_records": 0, "total_pairs": 0, "total_symbols": 0},
            "spot": {"total_records": 0, "total_pairs": 0, "total_symbols": 0},
        }
        market_pair_keys = {"swap": set(), "spot": set()}
        market_symbol_keys = {"swap": set(), "spot": set()}
        for stat in stats:
            market_type = _market_type_for_symbol(stat.get("symbol"))
            record_count = int(stat.get("record_count") or 0)
            market_stats[market_type]["total_records"] += record_count
            if record_count <= 0:
                continue
            market_pair_keys[market_type].add(
                (stat.get("exchange"), stat.get("symbol"), stat.get("timeframe"))
            )
            market_symbol_keys[market_type].add((stat.get("exchange"), stat.get("symbol")))
        for market_type in ("swap", "spot"):
            market_stats[market_type]["total_pairs"] = len(market_pair_keys[market_type])
            market_stats[market_type]["total_symbols"] = len(market_symbol_keys[market_type])

        return self._set_table_stats_cache({
            "tables": stats,
            "total_records": sum(s["record_count"] for s in stats),
            "total_pairs": len(
                set(
                    (s["exchange"], s["symbol"], s["timeframe"])
                    for s in stats
                    if s.get("record_count", 0) > 0
                )
            ),
            "market_stats": market_stats,
        })


sync_domain_service = SyncDomainService()
