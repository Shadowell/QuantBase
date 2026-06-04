import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.services import live_account_service


BINANCE_KEY = "xS7a" + ("Z" * 60) + "tUao"


def test_default_binance_account_uses_env_key_mask_without_revealing_full_key(monkeypatch):
    monkeypatch.setattr(live_account_service.settings, "BINANCE_API_KEY", BINANCE_KEY, raising=False)
    monkeypatch.setattr(live_account_service.settings, "BINANCE_API_SECRET", "", raising=False)
    monkeypatch.setattr(live_account_service.settings, "BINANCE_TESTNET", False, raising=False)

    account = live_account_service._default_binance_account_payload()

    assert account["account_id"] == "binance"
    assert account["name"] == "默认 Binance USD-M 实盘账户"
    assert account["exchange"] == "binanceusdm"
    assert account["exchange_alias"] == "binanceusdm"
    assert account["configured"] is False
    assert account["display_only"] is True
    assert account["masked_api_key"] == "xS7a****tUao"
    assert BINANCE_KEY not in str(account)


def test_exchange_alias_uses_account_exchange_for_non_okx_rows(monkeypatch):
    monkeypatch.setattr(
        live_account_service,
        "get_account",
        lambda account_id, reveal_secret=False: {
            "account_id": "binance_demo",
            "exchange": "binanceusdm",
            "enabled": True,
        },
    )

    assert live_account_service.exchange_alias_for_account("binance_demo") == "binanceusdm:binance_demo"


def test_binance_account_cannot_be_used_for_live_deployment_or_private_reads(monkeypatch):
    monkeypatch.setattr(live_account_service.settings, "BINANCE_API_KEY", BINANCE_KEY, raising=False)
    monkeypatch.setattr(live_account_service.settings, "BINANCE_API_SECRET", "", raising=False)
    monkeypatch.setattr(live_account_service.settings, "BINANCE_TESTNET", False, raising=False)

    with pytest.raises(Exception) as exc_info:
        live_account_service.validate_live_deployable_account_id("binance")

    assert "仅用于展示" in str(exc_info.value)
