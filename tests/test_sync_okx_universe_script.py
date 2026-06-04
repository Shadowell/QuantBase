from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "sync_okx_universe.py"

spec = importlib.util.spec_from_file_location("sync_okx_universe", SCRIPT_PATH)
module = importlib.util.module_from_spec(spec)
assert spec is not None and spec.loader is not None
spec.loader.exec_module(module)


def test_select_supported_usdt_symbols_keeps_only_active_usdt_spot_and_swap() -> None:
    markets = [
        {"symbol": "BTC/USDT", "active": True, "spot": True, "quote": "USDT"},
        {"symbol": "BTC/USDT:USDT", "active": True, "swap": True, "quote": "USDT", "settle": "USDT"},
        {"symbol": "ETH/USDC", "active": True, "spot": True, "quote": "USDC"},
        {"symbol": "DOGE/USDT:USDC", "active": True, "swap": True, "quote": "USDT", "settle": "USDC"},
        {"symbol": "SOL/USDT", "active": False, "spot": True, "quote": "USDT"},
        {"symbol": "XRP/USDT:USDT", "active": True, "future": True, "quote": "USDT", "settle": "USDT"},
    ]

    assert module.select_supported_usdt_symbols(markets) == [
        "BTC/USDT",
        "BTC/USDT:USDT",
    ]


def test_normalize_timeframe_sequence_preserves_required_sync_order() -> None:
    requested = ["15m", "1d", "1m", "4h", "30m", "1h", "1d", "5m"]

    assert module.normalize_timeframe_sequence(requested) == [
        "1d",
        "4h",
        "1h",
        "30m",
        "15m",
        "1m",
        "5m",
    ]
