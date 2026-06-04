import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.services.strategy_registry import get_base_strategy_registry


SYMBOLS = ["BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT"]

EXPECTED = {
    "contract_vwap_volume_profile_btc_eth_sol_1h_100u": {
        "name": "[合约][1H][CTA] BTC/ETH/SOL · VWAP成交量分布趋势 · 100U",
        "module_path": "app.strategies.contract_vwap_volume_profile_strategy",
        "class_name": "ContractVwapVolumeProfileStrategy",
        "timeframe": "1h",
    },
    "contract_fvg_liquidity_sweep_btc_eth_sol_15m_100u": {
        "name": "[合约][15M][CTA] BTC/ETH/SOL · FVG扫流动性结构 · 100U",
        "module_path": "app.strategies.contract_fvg_liquidity_sweep_strategy",
        "class_name": "ContractFvgLiquiditySweepStrategy",
        "timeframe": "15m",
    },
    "contract_order_flow_breakout_btc_eth_sol_5m_100u": {
        "name": "[合约][5M][CTA] BTC/ETH/SOL · Order Flow短线确认 · 100U",
        "module_path": "app.strategies.contract_order_flow_breakout_strategy",
        "class_name": "ContractOrderFlowBreakoutStrategy",
        "timeframe": "5m",
    },
}


def load_seed_entries():
    entries = json.loads((ROOT / "data" / "seed" / "strategies.json").read_text(encoding="utf-8"))
    return {entry["strategy_key"]: entry for entry in entries}


def test_technical_structure_seeds_are_paper_only_okx_btc_eth_sol_100u():
    entries = load_seed_entries()

    for key, expected in EXPECTED.items():
        entry = entries[key]
        cfg = entry["config"]
        assert entry["name"] == expected["name"]
        assert cfg["strategy_key"] == key
        assert cfg["module_path"] == expected["module_path"]
        assert cfg["class_name"] == expected["class_name"]
        assert cfg["exchange"] == "okx"
        assert entry["exchange"] == "okx"
        assert cfg["market_type"] == "swap"
        assert cfg["inst_type"] == "SWAP"
        assert cfg["td_mode"] == "isolated"
        assert cfg["position_mode"] == "long_short_mode"
        assert cfg["is_paper_trading"] is True
        assert cfg["initial_capital"] == 100
        assert cfg["timeframe"] == expected["timeframe"]
        assert cfg["symbols"] == SYMBOLS
        assert cfg["trade_symbols"] == SYMBOLS
        assert entry["symbols"] == SYMBOLS
        assert cfg["slippage_bps"] == 5
        assert cfg["taker_fee_bps"] == 5
        assert cfg["maker_fee_bps"] == 2
        assert cfg["trade_notional_pct"] > 0
        assert cfg["max_total_notional_pct"] > 0


def test_technical_structure_strategy_registry_maps_seed_keys_to_classes():
    registry = get_base_strategy_registry()

    for key, expected in EXPECTED.items():
        assert registry[key].__name__ == expected["class_name"]


def test_order_flow_seed_declares_real_tick_and_depth_gate():
    cfg = load_seed_entries()["contract_order_flow_breakout_btc_eth_sol_5m_100u"]["config"]

    assert cfg["requires_order_flow_data"] is True
    assert cfg["disable_without_order_flow_data"] is True
    assert "不得用 OHLCV 合成" in cfg["selection_logic"]
