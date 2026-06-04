from __future__ import annotations

import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = PROJECT_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.domain.market import repository as market_repository_module  # noqa: E402
from app.domain.market.repository import MarketRepository  # noqa: E402
from app.domain.market.service import MarketDomainService  # noqa: E402
from app.services import kline_file_store as kline_file_store_module  # noqa: E402
from app.services.kline_file_store import KlineFileStore, KlineStoreConfig  # noqa: E402


def _bar(ts: int, close: float) -> dict:
    return {
        "timestamp": ts,
        "open": close,
        "high": close + 1,
        "low": close - 1,
        "close": close,
        "volume": 10,
    }


def _bars(start_ts: int, count: int, step_ms: int = 60_000, close_start: float = 100) -> list[dict]:
    return [_bar(start_ts + index * step_ms, close_start + index) for index in range(count)]


def test_read_recent_klines_uses_latest_partition_when_limit_is_satisfied(tmp_path, monkeypatch) -> None:
    store = KlineFileStore(KlineStoreConfig(root_dir=tmp_path, fmt="csv"))
    store.append_klines(
        "okx",
        "ETH/USDT",
        "1m",
        [
            _bar(1_767_225_600_000, 100),  # 2026-01
            _bar(1_767_225_660_000, 101),
            _bar(1_777_579_200_000, 200),  # 2026-05
            _bar(1_777_579_260_000, 201),
            _bar(1_777_579_320_000, 202),
        ],
    )

    original_read_csv = kline_file_store_module.pd.read_csv
    read_files: list[str] = []

    def tracking_read_csv(path, *args, **kwargs):
        read_files.append(Path(path).name)
        return original_read_csv(path, *args, **kwargs)

    monkeypatch.setattr(kline_file_store_module.pd, "read_csv", tracking_read_csv)

    rows = store.read_klines("okx", "ETH/USDT", "1m", limit=2)

    assert [row["close"] for row in rows] == [201, 202]
    assert read_files == ["202605.csv"]


def test_read_bounded_klines_only_reads_matching_month_partitions(tmp_path, monkeypatch) -> None:
    store = KlineFileStore(KlineStoreConfig(root_dir=tmp_path, fmt="csv"))
    store.append_klines(
        "okx",
        "ETH/USDT",
        "1m",
        [
            _bar(1_767_225_600_000, 100),  # 2026-01
            _bar(1_767_225_660_000, 101),
            _bar(1_777_579_200_000, 200),  # 2026-05
            _bar(1_777_579_260_000, 201),
        ],
    )

    original_read_csv = kline_file_store_module.pd.read_csv
    read_files: list[str] = []

    def tracking_read_csv(path, *args, **kwargs):
        read_files.append(Path(path).name)
        return original_read_csv(path, *args, **kwargs)

    monkeypatch.setattr(kline_file_store_module.pd, "read_csv", tracking_read_csv)

    rows = store.read_klines(
        "okx",
        "ETH/USDT",
        "1m",
        start_ms=1_777_579_200_000,
        end_ms=1_777_579_260_000,
        limit=2,
    )

    assert [row["close"] for row in rows] == [200, 201]
    assert read_files == ["202605.csv"]


def test_parquet_append_quarantines_corrupt_partition_and_rewrites_atomically(tmp_path) -> None:
    pytest.importorskip("pyarrow")

    store = KlineFileStore(KlineStoreConfig(root_dir=tmp_path, fmt="parquet"))
    store.append_klines("okx", "ETH/USDT", "5m", [_bar(1_777_564_800_000, 100)])

    partition = tmp_path / "okx" / "ETH-USDT" / "5m" / "202605.parquet"
    partition.write_bytes(b"not a readable parquet file")

    inserted = store.append_klines("okx", "ETH/USDT", "5m", [_bar(1_777_565_100_000, 101)])

    repaired = kline_file_store_module.pd.read_parquet(partition)
    quarantined = sorted(partition.parent.glob("202605.parquet.corrupt-*"))

    assert inserted == 1
    assert quarantined
    assert repaired["timestamp"].tolist() == [1_777_565_100_000]


def test_market_repository_reads_file_store_before_sqlite(monkeypatch) -> None:
    expected = [_bar(1_777_579_200_000, 200), _bar(1_777_579_260_000, 201)]

    class FakeStore:
        def read_klines(self, exchange, symbol, timeframe, *, start_ms=None, end_ms=None, limit=None):
            assert (exchange, symbol, timeframe, start_ms, end_ms, limit) == (
                "okx",
                "ETH/USDT",
                "1m",
                None,
                None,
                2,
            )
            return expected

    monkeypatch.setattr(market_repository_module, "kline_store", FakeStore())
    monkeypatch.setattr(
        market_repository_module.db,
        "get_klines",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("SQLite should not be read")),
    )

    rows = MarketRepository().get_klines("okx", "ETH/USDT", "1m", 2)

    assert rows == expected


def test_market_service_refetches_full_recent_window_when_cache_has_stale_gap() -> None:
    stale_cached = _bars(1_700_000_000_000, 310, close_start=100)
    recent_start = 1_700_604_800_000
    recent_full = _bars(recent_start, 360, close_start=500)
    recent_tail = recent_full[-50:]
    inserted: list[list[dict]] = []

    class FakeRepo:
        def get_klines(self, exchange, symbol, timeframe, limit, start=None, end=None):
            assert (exchange, symbol, timeframe, limit, start, end) == (
                "okx",
                "XRP/USDT:USDT",
                "1m",
                360,
                None,
                None,
            )
            return stale_cached

        def insert_klines(self, exchange, symbol, timeframe, klines):
            inserted.append(klines)
            return len(klines)

    class FakeExchange:
        def __init__(self):
            self.calls: list[dict] = []

        def fetch_ohlcv(self, symbol, timeframe="1m", limit=100, since=None):
            self.calls.append({"symbol": symbol, "timeframe": timeframe, "limit": limit, "since": since})
            if limit == 50:
                return recent_tail
            if limit == 360:
                return recent_full
            raise AssertionError(f"unexpected limit: {limit}")

    exchange = FakeExchange()
    service = MarketDomainService(repo=FakeRepo())
    service._get_exchange = lambda exchange_name: exchange  # type: ignore[method-assign]

    rows = __import__("asyncio").run(service.get_klines("okx", "XRP/USDT:USDT", "1m", 360))

    assert [call["limit"] for call in exchange.calls] == [50, 360]
    assert len(rows) == 360
    assert rows[0]["timestamp"] == recent_full[0]["timestamp"]
    assert rows[-1]["timestamp"] == recent_full[-1]["timestamp"]
    assert inserted == [recent_tail, recent_full]


def test_market_service_returns_cached_bounded_kline_range_without_exchange_fetch() -> None:
    cached = _bars(1_777_579_200_000, 12, close_start=200)

    class FakeRepo:
        def get_klines(self, exchange, symbol, timeframe, limit, start=None, end=None):
            assert (exchange, symbol, timeframe, limit, start, end) == (
                "okx",
                "SNDK/USDT:USDT",
                "1h",
                10,
                1_777_579_200_000,
                1_777_619_200_000,
            )
            return cached

        def insert_klines(self, exchange, symbol, timeframe, klines):
            raise AssertionError("bounded cached reads should not write exchange klines")

    exchange_calls: list[str] = []
    service = MarketDomainService(repo=FakeRepo())

    def fail_get_exchange(exchange_name: str):
        exchange_calls.append(exchange_name)
        raise AssertionError("bounded cached reads should not require an exchange")

    service._get_exchange = fail_get_exchange  # type: ignore[method-assign]

    rows = __import__("asyncio").run(
        service.get_klines(
            "okx",
            "SNDK/USDT:USDT",
            "1h",
            10,
            1_777_579_200_000,
            1_777_619_200_000,
        )
    )

    assert [row["close"] for row in rows] == [202, 203, 204, 205, 206, 207, 208, 209, 210, 211]
    assert exchange_calls == []


def test_market_service_refreshes_bounded_kline_range_when_cache_is_short() -> None:
    cached = _bars(1_777_579_200_000, 2, close_start=200)
    fetched = _bars(1_777_579_200_000, 10, step_ms=3_600_000, close_start=300)
    inserted: list[list[dict]] = []

    class FakeRepo:
        def get_klines(self, exchange, symbol, timeframe, limit, start=None, end=None):
            return cached

        def insert_klines(self, exchange, symbol, timeframe, klines):
            inserted.append(klines)
            return len(klines)

    class FakeExchange:
        def __init__(self):
            self.calls: list[dict] = []

        def fetch_ohlcv(self, symbol, timeframe="1m", limit=100, since=None):
            self.calls.append({"symbol": symbol, "timeframe": timeframe, "limit": limit, "since": since})
            return fetched

    exchange = FakeExchange()
    service = MarketDomainService(repo=FakeRepo())
    service._get_exchange = lambda exchange_name: exchange  # type: ignore[method-assign]

    rows = __import__("asyncio").run(
        service.get_klines(
            "okx",
            "SNDK/USDT:USDT",
            "1h",
            10,
            1_777_579_200_000,
            1_777_619_200_000,
        )
    )

    assert [row["close"] for row in rows] == [300, 301, 302, 303, 304, 305, 306, 307, 308, 309]
    assert exchange.calls == [
        {"symbol": "SNDK/USDT:USDT", "timeframe": "1h", "limit": 10, "since": 1_777_579_200_000}
    ]
    assert inserted == [fetched]


def test_market_repository_writes_exchange_fetches_to_file_store(monkeypatch) -> None:
    rows = [_bar(1_777_579_200_000, 200)]
    calls: list[tuple] = []

    class FakeStore:
        def append_klines(self, exchange, symbol, timeframe, klines):
            calls.append((exchange, symbol, timeframe, klines))
            return len(klines)

    monkeypatch.setattr(market_repository_module, "kline_store", FakeStore())
    monkeypatch.setattr(
        market_repository_module.db,
        "insert_klines",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("SQLite should not be written")),
    )

    inserted = MarketRepository().insert_klines("okx", "ETH/USDT", "1m", rows)

    assert inserted == 1
    assert calls == [("okx", "ETH/USDT", "1m", rows)]
