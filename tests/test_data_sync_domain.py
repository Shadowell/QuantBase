from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = PROJECT_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.domain.sync import service as sync_service_module  # noqa: E402
from app.db.local_db import LocalDatabase  # noqa: E402
from app.services.data_sync_service import DataSyncService, SyncJobResult, SyncProgress, SyncStatus  # noqa: E402
from app.services import data_sync_service as data_sync_module  # noqa: E402


def test_sync_domain_config_merges_custom_symbols(monkeypatch) -> None:
    stored = {
        sync_service_module.CUSTOM_SYMBOLS_SETTING_KEY: json.dumps(["PEPE/USDT", "ETH/USDT"]),
        sync_service_module.REMOVED_DEFAULT_SYMBOLS_SETTING_KEY: "[]",
    }

    monkeypatch.setattr(
        sync_service_module.db,
        "get_app_setting",
        lambda key, default=None: stored.get(key, default),
    )

    result = sync_service_module.SyncDomainService().config()

    assert result["default_symbols"][: len(sync_service_module.DEFAULT_SYMBOLS)] == sync_service_module.DEFAULT_SYMBOLS
    assert result["default_timeframes"] == ["1m", "5m", "15m", "30m", "1h", "4h", "1d"]
    assert result["default_symbols"].count("ETH/USDT") == 1
    assert result["default_symbols"][-1] == "PEPE/USDT"


def test_sync_domain_add_symbol_persists_normalized_usdt_symbol(monkeypatch) -> None:
    stored = {
        sync_service_module.CUSTOM_SYMBOLS_SETTING_KEY: "[]",
        sync_service_module.REMOVED_DEFAULT_SYMBOLS_SETTING_KEY: "[]",
    }

    monkeypatch.setattr(
        sync_service_module.db,
        "get_app_setting",
        lambda key, default=None: stored.get(key, default),
    )
    monkeypatch.setattr(
        sync_service_module.db,
        "set_app_setting",
        lambda key, value: stored.update({"key": key, key: value}),
    )

    result = sync_service_module.SyncDomainService().add_symbol({"symbol": "pepe"})
    duplicate = sync_service_module.SyncDomainService().add_symbol({"symbol": "PEPE/USDT"})

    assert result["symbol"] == "PEPE/USDT"
    assert result["added"] is True
    assert duplicate["symbol"] == "PEPE/USDT"
    assert duplicate["added"] is False
    assert json.loads(stored[sync_service_module.CUSTOM_SYMBOLS_SETTING_KEY]) == ["PEPE/USDT"]


def test_sync_domain_add_symbol_persists_contract_symbol(monkeypatch) -> None:
    stored = {
        sync_service_module.CUSTOM_SYMBOLS_SETTING_KEY: "[]",
        sync_service_module.REMOVED_DEFAULT_SYMBOLS_SETTING_KEY: "[]",
    }

    monkeypatch.setattr(
        sync_service_module.db,
        "get_app_setting",
        lambda key, default=None: stored.get(key, default),
    )
    monkeypatch.setattr(
        sync_service_module.db,
        "set_app_setting",
        lambda key, value: stored.update({"key": key, key: value}),
    )

    result = sync_service_module.SyncDomainService().add_symbol({"symbol": "openai/usdt:usdt"})
    duplicate = sync_service_module.SyncDomainService().add_symbol({"symbol": "OPENAI-USDT-SWAP"})

    assert result["symbol"] == "OPENAI/USDT:USDT"
    assert result["added"] is True
    assert duplicate["symbol"] == "OPENAI/USDT:USDT"
    assert duplicate["added"] is False
    assert json.loads(stored[sync_service_module.CUSTOM_SYMBOLS_SETTING_KEY]) == ["OPENAI/USDT:USDT"]

def test_sync_domain_remove_symbol_persists_custom_symbol_removal(monkeypatch) -> None:
    stored = {
        sync_service_module.CUSTOM_SYMBOLS_SETTING_KEY: json.dumps(["PEPE/USDT", "OPENAI/USDT:USDT"]),
        sync_service_module.REMOVED_DEFAULT_SYMBOLS_SETTING_KEY: "[]",
    }

    monkeypatch.setattr(
        sync_service_module.db,
        "get_app_setting",
        lambda key, default=None: stored.get(key, default),
    )
    monkeypatch.setattr(
        sync_service_module.db,
        "set_app_setting",
        lambda key, value: stored.update({"key": key, key: value}),
    )

    result = sync_service_module.SyncDomainService().remove_symbol({"symbol": "OPENAI-USDT-SWAP"})

    assert result["symbol"] == "OPENAI/USDT:USDT"
    assert result["removed"] is True
    assert json.loads(stored[sync_service_module.CUSTOM_SYMBOLS_SETTING_KEY]) == ["PEPE/USDT"]
    assert "OPENAI/USDT:USDT" not in result["default_symbols"]


def test_sync_domain_remove_and_restore_default_symbol(monkeypatch) -> None:
    default_symbol = sync_service_module.DEFAULT_SYMBOLS[0]
    stored = {
        sync_service_module.CUSTOM_SYMBOLS_SETTING_KEY: "[]",
        sync_service_module.REMOVED_DEFAULT_SYMBOLS_SETTING_KEY: "[]",
    }

    monkeypatch.setattr(
        sync_service_module.db,
        "get_app_setting",
        lambda key, default=None: stored.get(key, default),
    )
    monkeypatch.setattr(
        sync_service_module.db,
        "set_app_setting",
        lambda key, value: stored.update({"key": key, key: value}),
    )

    removed = sync_service_module.SyncDomainService().remove_symbol({"symbol": default_symbol})

    assert removed["removed"] is True
    assert default_symbol not in removed["default_symbols"]
    assert json.loads(stored[sync_service_module.REMOVED_DEFAULT_SYMBOLS_SETTING_KEY]) == [default_symbol]

    restored = sync_service_module.SyncDomainService().add_symbol({"symbol": default_symbol})

    assert json.loads(stored[sync_service_module.REMOVED_DEFAULT_SYMBOLS_SETTING_KEY]) == []
    assert restored["added"] is True
    assert default_symbol in restored["default_symbols"]


def test_data_sync_end_date_is_inclusive_for_date_only_ranges() -> None:
    end_ms = data_sync_module._sync_end_date_ms("2026-05-13")

    assert end_ms == int(datetime(2026, 5, 14).timestamp() * 1000)


def test_sync_klines_skips_non_retryable_missing_market_errors(monkeypatch) -> None:
    class MissingMarketExchange:
        def __init__(self) -> None:
            self.calls = 0

        def fetch_ohlcv(self, *args, **kwargs):
            self.calls += 1
            raise RuntimeError("okx does not have market symbol ESP/USDT")

    exchange = MissingMarketExchange()
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(data_sync_module.exchange_manager, "get_exchange", lambda name: exchange)
    monkeypatch.setattr(data_sync_module.db, "update_sync_metadata", lambda *args, **kwargs: None)
    monkeypatch.setattr(data_sync_module.kline_store, "get_stats", lambda *args, **kwargs: {"record_count": 0})
    monkeypatch.setattr(data_sync_module.asyncio, "sleep", fake_sleep)

    progress = asyncio.run(
        DataSyncService().sync_klines(
            "okx",
            "ESP/USDT",
            "1m",
            start_date="2026-05-09",
            end_date="2026-05-16",
        )
    )

    assert exchange.calls == 1
    assert sleeps == []
    assert progress.status == SyncStatus.ERROR
    assert "不可同步交易对" in (progress.error or "")


def test_sync_domain_schedule_accepts_configured_contract_symbols(monkeypatch) -> None:
    stored = {"value": "{}"}

    monkeypatch.setattr(
        sync_service_module.db,
        "get_app_setting",
        lambda key, default=None: stored["value"],
    )
    monkeypatch.setattr(
        sync_service_module.db,
        "set_app_setting",
        lambda key, value: stored.update({"key": key, "value": value}),
    )
    monkeypatch.setattr(
        sync_service_module.SyncDomainService,
        "_configured_symbols",
        lambda self: ["BTC/USDT", "OPENAI/USDT:USDT"],
    )

    result = sync_service_module.SyncDomainService().update_schedule_config({
        "enabled": True,
        "symbols": ["OPENAI-USDT-SWAP", "BTC/USDT", "SPCX/USDT:USDT"],
        "timeframes": ["15m"],
    })

    assert result["symbols"] == ["OPENAI/USDT:USDT", "BTC/USDT"]


def test_sync_domain_schedule_config_normalizes_and_persists(monkeypatch) -> None:
    stored = {"value": "{}"}

    monkeypatch.setattr(
        sync_service_module.db,
        "get_app_setting",
        lambda key, default=None: stored["value"],
    )
    monkeypatch.setattr(
        sync_service_module.db,
        "set_app_setting",
        lambda key, value: stored.update({"key": key, "value": value}),
    )
    monkeypatch.setattr(
        sync_service_module.SyncDomainService,
        "_configured_symbols",
        lambda self: ["BTC/USDT", "ETH/USDT"],
    )

    result = sync_service_module.SyncDomainService().update_schedule_config({
        "enabled": True,
        "interval_minutes": 1,
        "history_days": 999,
        "symbols": ["btc", "PEPE/USDT", "ETH/USDT", "BTC/USDT"],
        "timeframes": ["1m", "bad", "1h", "1m"],
    })

    assert stored["key"] == sync_service_module.SCHEDULE_SETTING_KEY
    assert result["enabled"] is True
    assert result["interval_minutes"] == 5
    assert result["history_days"] == 365
    assert result["symbols"] == ["BTC/USDT", "ETH/USDT"]
    assert result["timeframes"] == ["1m", "1h"]
    assert result["next_run_at"] is not None


def test_sync_domain_scheduled_run_creates_resumable_job_when_due(monkeypatch) -> None:
    stored = {
        "value": json.dumps(
            {
                "enabled": True,
                "interval_minutes": 5,
                "history_days": 7,
                "symbols": ["BTC/USDT"],
                "timeframes": ["1m"],
            }
        )
    }
    created: list[dict] = []

    monkeypatch.setattr(
        sync_service_module.db,
        "get_app_setting",
        lambda key, default=None: stored["value"],
    )
    monkeypatch.setattr(
        sync_service_module.db,
        "set_app_setting",
        lambda key, value: stored.update({"key": key, "value": value}),
    )
    monkeypatch.setattr(
        sync_service_module.SyncDomainService,
        "_configured_symbols",
        lambda self: ["BTC/USDT", "ETH/USDT"],
    )

    service = sync_service_module.SyncDomainService()
    monkeypatch.setattr(service, "is_running", lambda: False)

    def fake_create_job(payload, *, exchange=None, history_days=365):
        created.append({"payload": payload, "exchange": exchange, "history_days": history_days})
        return {"job_id": "job-scheduled-1"}

    async def fake_run_job(job_id):
        assert job_id == "job-scheduled-1"
        return {
            "job_id": job_id,
            "exchange": "okx",
            "status": "completed",
            "total_fetched": 10,
            "total_inserted": 9,
            "errors": 0,
        }

    monkeypatch.setattr(service, "create_job", fake_create_job)
    monkeypatch.setattr(service, "run_job", fake_run_job)

    import asyncio

    result = asyncio.run(service.run_scheduled_if_due())

    assert result["started"] is True
    assert result["job_id"] == "job-scheduled-1"
    assert created == [
        {
            "payload": {
                "exchange": "okx",
                "symbols": ["BTC/USDT"],
                "timeframes": ["1m"],
                "history_days": 7,
            },
            "exchange": "okx",
            "history_days": 7,
        }
    ]
    persisted = json.loads(stored["value"])
    assert persisted["last_job_id"] == "job-scheduled-1"
    assert persisted["last_run_at"] is not None
    assert persisted["last_finished_at"] is not None
    assert persisted["last_error"] is None


def test_sync_status_exposes_progress_elapsed_seconds(monkeypatch) -> None:
    started = datetime(2026, 5, 9, 10, 0, 0)
    ended = started + timedelta(seconds=12.5)
    service = DataSyncService()
    service._current_job = SyncJobResult(
        exchange="okx",
        started_at=started,
        completed_at=ended,
        total_symbols=1,
        total_timeframes=1,
        total_records_fetched=300,
        total_records_inserted=288,
    )
    service._current_job.progress.append(
        SyncProgress(
            exchange="okx",
            symbol="ETH/USDT",
            timeframe="1h",
            status=SyncStatus.COMPLETED,
            total_fetched=300,
            total_inserted=288,
            start_time=started,
            end_time=ended,
        )
    )

    monkeypatch.setattr(data_sync_module.db, "get_all_sync_metadata", lambda exchange=None: [])

    result = service.get_sync_status()

    assert result["current_job"]["elapsed_seconds"] == 12.5
    assert result["current_job"]["total_items"] == 1
    assert result["current_job"]["completed_items"] == 1
    assert result["current_job"]["progress"] == [
        {
            "exchange": "okx",
            "symbol": "ETH/USDT",
            "timeframe": "1h",
            "status": "completed",
            "total_fetched": 300,
            "total_inserted": 288,
            "started_at": "2026-05-09 10:00:00",
            "ended_at": "2026-05-09 10:00:12",
            "elapsed_seconds": 12.5,
            "error": None,
        }
    ]


def test_sync_status_recovers_persisted_running_job_after_restart(monkeypatch, tmp_path) -> None:
    temp_db = LocalDatabase(str(tmp_path / "sync_resume.db"))
    temp_db.init_db()
    service = DataSyncService()
    monkeypatch.setattr(data_sync_module, "db", temp_db)

    job = service.create_sync_job(
        exchange_name="okx",
        symbols=["ETH/USDT"],
        timeframes=["1m"],
        history_days=30,
    )

    conn = temp_db.get_connection()
    conn.execute(
        """
        UPDATE sync_jobs
        SET status = 'running', started_at = '2026-05-09 10:00:00'
        WHERE id = ?
        """,
        (job["job_id"],),
    )
    conn.execute(
        """
        UPDATE sync_job_items
        SET status = 'running',
            total_fetched = 300,
            total_inserted = 288,
            checkpoint_timestamp = 1772560000000,
            started_at = '2026-05-09 10:00:02'
        WHERE job_id = ?
        """,
        (job["job_id"],),
    )
    conn.commit()

    restarted_service = DataSyncService()
    result = restarted_service.get_sync_status()

    assert result["is_running"] is True
    assert result["current_job"]["job_id"] == job["job_id"]
    assert result["current_job"]["status"] == "running"
    assert result["current_job"]["total_items"] == 1
    assert result["current_job"]["completed_items"] == 0
    assert result["current_job"]["progress"][0]["status"] == "syncing"
    assert result["current_job"]["progress"][0]["total_fetched"] == 300
    assert result["current_job"]["progress"][0]["checkpoint_timestamp"] == 1772560000000


def test_run_persisted_job_resumes_from_item_checkpoint(monkeypatch, tmp_path) -> None:
    temp_db = LocalDatabase(str(tmp_path / "sync_resume_checkpoint.db"))
    temp_db.init_db()
    service = DataSyncService()
    monkeypatch.setattr(data_sync_module, "db", temp_db)

    job = service.create_sync_job(
        exchange_name="okx",
        symbols=["ETH/USDT", "BTC/USDT"],
        timeframes=["1m"],
        history_days=30,
        start_date="2026-05-01",
        end_date="2026-05-09",
    )

    conn = temp_db.get_connection()
    conn.execute(
        """
        UPDATE sync_job_items
        SET status = 'completed', total_fetched = 300, total_inserted = 300,
            checkpoint_timestamp = 1772560000000,
            started_at = '2026-05-09 09:00:00',
            ended_at = '2026-05-09 09:01:00'
        WHERE job_id = ? AND symbol = 'ETH/USDT'
        """,
        (job["job_id"],),
    )
    conn.execute(
        """
        UPDATE sync_job_items
        SET status = 'running', total_fetched = 600, total_inserted = 590,
            checkpoint_timestamp = 1772600000000,
            started_at = '2026-05-09 09:01:00'
        WHERE job_id = ? AND symbol = 'BTC/USDT'
        """,
        (job["job_id"],),
    )
    conn.commit()

    calls = []

    async def fake_sync_klines(**kwargs):
        calls.append(kwargs)
        return SyncProgress(
            exchange=kwargs["exchange_name"],
            symbol=kwargs["symbol"],
            timeframe=kwargs["timeframe"],
            status=SyncStatus.COMPLETED,
            total_fetched=5,
            total_inserted=4,
            start_time=datetime(2026, 5, 9, 10, 0, 0),
            end_time=datetime(2026, 5, 9, 10, 0, 5),
        )

    monkeypatch.setattr(service, "sync_klines", fake_sync_klines)

    import asyncio

    result = asyncio.run(service.run_sync_job(job["job_id"]))

    assert [call["symbol"] for call in calls] == ["BTC/USDT"]
    assert calls[0]["resume_from_timestamp"] == 1772600000000
    assert calls[0]["start_date"] == "2026-05-01"
    assert result.status == "completed"

    rows = temp_db.get_connection().execute(
        "SELECT symbol, status FROM sync_job_items WHERE job_id = ? ORDER BY symbol",
        (job["job_id"],),
    ).fetchall()
    assert [(row["symbol"], row["status"]) for row in rows] == [
        ("BTC/USDT", "completed"),
        ("ETH/USDT", "completed"),
    ]
    status = service.get_sync_status()
    assert status["current_job"]["job_id"] == job["job_id"]
    assert status["current_job"]["status"] == "completed"
    assert status["current_job"]["completed_items"] == 2


def test_list_sync_jobs_returns_current_and_history_detail(monkeypatch, tmp_path) -> None:
    temp_db = LocalDatabase(str(tmp_path / "sync_jobs_history.db"))
    temp_db.init_db()
    service = DataSyncService()
    monkeypatch.setattr(data_sync_module, "db", temp_db)

    historical = service.create_sync_job(
        exchange_name="okx",
        symbols=["ETH/USDT", "BTC/USDT"],
        timeframes=["1m"],
        history_days=30,
        start_date="2026-05-01",
        end_date="2026-05-09",
    )
    conn = temp_db.get_connection()
    conn.execute(
        """
        UPDATE sync_jobs
        SET status = 'completed_with_errors',
            started_at = '2026-05-09 09:00:00',
            completed_at = '2026-05-09 09:02:30',
            total_records_fetched = 500,
            total_records_inserted = 480,
            error_count = 1,
            error_message = 'BTC/USDT 1m: timeout'
        WHERE id = ?
        """,
        (historical["job_id"],),
    )
    conn.execute(
        """
        UPDATE sync_job_items
        SET status = 'completed',
            total_fetched = 300,
            total_inserted = 288,
            checkpoint_timestamp = 1772600000000,
            started_at = '2026-05-09 09:00:00',
            ended_at = '2026-05-09 09:01:00'
        WHERE job_id = ? AND symbol = 'ETH/USDT'
        """,
        (historical["job_id"],),
    )
    conn.execute(
        """
        UPDATE sync_job_items
        SET status = 'error',
            total_fetched = 200,
            total_inserted = 192,
            checkpoint_timestamp = 1772603600000,
            started_at = '2026-05-09 09:01:00',
            ended_at = '2026-05-09 09:02:30',
            error_message = 'timeout'
        WHERE job_id = ? AND symbol = 'BTC/USDT'
        """,
        (historical["job_id"],),
    )
    conn.commit()

    current = service.create_sync_job(
        exchange_name="okx",
        symbols=["SOL/USDT"],
        timeframes=["1h"],
        history_days=7,
    )
    conn.execute(
        """
        UPDATE sync_jobs
        SET status = 'running',
            started_at = '2026-05-09 10:00:00'
        WHERE id = ?
        """,
        (current["job_id"],),
    )
    conn.execute(
        """
        UPDATE sync_job_items
        SET status = 'running',
            total_fetched = 120,
            total_inserted = 118,
            checkpoint_timestamp = 1772607200000,
            started_at = '2026-05-09 10:00:01'
        WHERE job_id = ?
        """,
        (current["job_id"],),
    )
    conn.commit()

    result = service.list_sync_jobs(limit=10)

    assert [job["job_id"] for job in result["jobs"]] == [current["job_id"], historical["job_id"]]
    assert result["jobs"][0]["status"] == "running"
    assert result["jobs"][0]["running_items"] == 1
    assert result["jobs"][0]["items"][0]["checkpoint_timestamp"] == 1772607200000

    historical_job = result["jobs"][1]
    assert historical_job["status"] == "completed_with_errors"
    assert historical_job["symbols"] == ["ETH/USDT", "BTC/USDT"]
    assert historical_job["timeframes"] == ["1m"]
    assert historical_job["completed_items"] == 1
    assert historical_job["error_items"] == 1
    assert historical_job["progress_percent"] == 100.0
    assert historical_job["total_fetched"] == 500
    assert historical_job["total_inserted"] == 480
    assert historical_job["elapsed_seconds"] == 150.0
    assert historical_job["items"][1]["error_message"] == "timeout"

    summary_only = service.list_sync_jobs(limit=10, include_items=False)
    summary_historical_job = summary_only["jobs"][1]
    assert "items" not in summary_historical_job
    assert summary_historical_job["completed_items"] == 1
    assert summary_historical_job["error_items"] == 1
    assert summary_historical_job["progress_percent"] == 100.0


def test_sync_domain_table_stats_uses_file_store_metadata(monkeypatch) -> None:
    metadata = [
        {
            "exchange": "okx",
            "symbol": "ETH/USDT",
            "timeframe": "1m",
            "data_type": "kline",
            "total_records": 300,
            "first_timestamp": 1_772_560_000_000,
            "last_timestamp": 1_772_577_940_000,
        },
        {
            "exchange": "okx",
            "symbol": "OPENAI/USDT:USDT",
            "timeframe": "15m",
            "data_type": "kline",
            "total_records": 700,
            "first_timestamp": 1_772_560_000_000,
            "last_timestamp": 1_772_577_940_000,
        }
    ]

    monkeypatch.setattr(sync_service_module.db, "get_all_sync_metadata", lambda exchange=None: metadata)
    monkeypatch.setattr(sync_service_module.db, "get_kline_table_stats", lambda: [])

    class FakeKlineStore:
        def get_stats(self, exchange: str, symbol: str, timeframe: str):
            raise AssertionError("table_stats should use sync_metadata without reading K-line files")

    monkeypatch.setattr(sync_service_module, "kline_store", FakeKlineStore(), raising=False)

    result = sync_service_module.SyncDomainService().table_stats()

    assert result["total_records"] == 1000
    assert result["total_pairs"] == 2
    assert result["market_stats"] == {
        "swap": {"total_records": 700, "total_pairs": 1, "total_symbols": 1},
        "spot": {"total_records": 300, "total_pairs": 1, "total_symbols": 1},
    }
    assert result["tables"] == [
        {
            "table_name": "kline_file_store",
            "timeframe": "1m",
            "exchange": "okx",
            "symbol": "ETH/USDT",
            "record_count": 300,
            "first_timestamp": 1_772_560_000_000,
            "last_timestamp": 1_772_577_940_000,
        },
        {
            "table_name": "kline_file_store",
            "timeframe": "15m",
            "exchange": "okx",
            "symbol": "OPENAI/USDT:USDT",
            "record_count": 700,
            "first_timestamp": 1_772_560_000_000,
            "last_timestamp": 1_772_577_940_000,
        }
    ]


def test_sync_domain_table_stats_uses_short_ttl_cache(monkeypatch) -> None:
    metadata = [
        {
            "exchange": "okx",
            "symbol": "ETH/USDT",
            "timeframe": "1m",
            "data_type": "kline",
            "total_records": 300,
            "first_timestamp": 1,
            "last_timestamp": 2,
        }
    ]
    calls = {"metadata": 0, "stats": 0, "sqlite": 0}

    def fake_metadata(exchange=None):
        calls["metadata"] += 1
        return metadata

    monkeypatch.setattr(sync_service_module.db, "get_all_sync_metadata", fake_metadata)
    monkeypatch.setattr(
        sync_service_module.db,
        "get_kline_table_stats",
        lambda: calls.__setitem__("sqlite", calls["sqlite"] + 1) or [],
    )

    class FakeKlineStore:
        def get_stats(self, exchange: str, symbol: str, timeframe: str):
            calls["stats"] += 1
            raise AssertionError("cached table_stats should not read K-line files")

    monkeypatch.setattr(sync_service_module, "kline_store", FakeKlineStore(), raising=False)

    service = sync_service_module.SyncDomainService()
    first = service.table_stats()
    second = service.table_stats()

    assert first == second
    assert calls == {"metadata": 1, "stats": 0, "sqlite": 0}


def test_sync_domain_table_stats_skips_sqlite_scan_when_file_metadata_exists(monkeypatch) -> None:
    metadata = [
        {
            "exchange": "okx",
            "symbol": "BTC/USDT:USDT",
            "timeframe": "1m",
            "data_type": "kline",
            "total_records": 42,
            "first_timestamp": 1,
            "last_timestamp": 2,
        }
    ]

    monkeypatch.setattr(sync_service_module.db, "get_all_sync_metadata", lambda exchange=None: metadata)

    def fail_sqlite_scan():
        raise AssertionError("legacy SQLite K-line scan should not run when file metadata exists")

    monkeypatch.setattr(sync_service_module.db, "get_kline_table_stats", fail_sqlite_scan)

    class FakeKlineStore:
        def get_stats(self, exchange: str, symbol: str, timeframe: str):
            raise AssertionError("table_stats should not read K-line files when metadata has stats")

    monkeypatch.setattr(sync_service_module, "kline_store", FakeKlineStore(), raising=False)

    result = sync_service_module.SyncDomainService().table_stats()

    assert result["total_records"] == 42
    assert result["total_pairs"] == 1


def test_delete_klines_clears_file_store_and_metadata(monkeypatch) -> None:
    metadata = [
        {"exchange": "okx", "symbol": "ETH/USDT", "timeframe": "1m", "data_type": "kline"},
        {"exchange": "okx", "symbol": "ETH/USDT", "timeframe": "5m", "data_type": "kline"},
        {"exchange": "okx", "symbol": "BTC/USDT", "timeframe": "1m", "data_type": "kline"},
    ]
    updates: list[tuple[tuple, dict]] = []

    monkeypatch.setattr(data_sync_module.kline_store, "delete", lambda exchange, symbol, timeframe=None: 2)
    monkeypatch.setattr(data_sync_module.db, "get_all_sync_metadata", lambda exchange=None: metadata)
    monkeypatch.setattr(
        data_sync_module.db,
        "update_sync_metadata",
        lambda *args, **kwargs: updates.append((args, kwargs)),
    )

    result = DataSyncService().delete_klines("okx", "ETH/USDT")

    assert result == {
        "message": "已删除 2 个K线数据文件",
        "deleted": 2,
        "deleted_files": 2,
    }
    assert updates == [
        (
            ("okx", "ETH/USDT", "1m", "kline"),
            {
                "first_timestamp": None,
                "last_timestamp": None,
                "total_records": 0,
                "status": "idle",
                "last_sync_at": None,
                "error_message": None,
            },
        ),
        (
            ("okx", "ETH/USDT", "5m", "kline"),
            {
                "first_timestamp": None,
                "last_timestamp": None,
                "total_records": 0,
                "status": "idle",
                "last_sync_at": None,
                "error_message": None,
            },
        ),
    ]
