from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = PROJECT_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.mcp.client import QuantBaseMcpClient, QuantBaseMcpError  # noqa: E402
from app.mcp.tools import (  # noqa: E402
    LiveConfirmationError,
    LiveTradingDisabledError,
    backtest_start_job,
    quantbase_capabilities,
    strategy_validate_code,
    sync_start_history,
    trading_spot_order,
)


class FakeHttpClient:
    def __init__(self, responses: list[httpx.Response]):
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        self.calls.append({"method": method, "url": url, **kwargs})
        if not self.responses:
            raise AssertionError("no fake response queued")
        return self.responses.pop(0)


class FakeQuantBaseClient:
    def __init__(self):
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        self.calls.append((method, path, kwargs))
        return {"ok": True, "path": path}


def test_client_unwraps_success_envelope_and_writes_redacted_audit(tmp_path: Path) -> None:
    fake_http = FakeHttpClient([httpx.Response(200, json={"success": True, "data": {"ok": True}})])
    audit_path = tmp_path / "mcp_audit.jsonl"
    client = QuantBaseMcpClient(
        base_url="http://quantbase.local/api/v2",
        audit_path=audit_path,
        http_client=fake_http,
    )

    result = client.request(
        "POST",
        "/settings/feishu-webhook",
        json={"webhook_url": "https://secret.example/hook", "api_key": "hidden"},
        tool_name="settings_feishu_webhook",
    )

    assert result == {"ok": True}
    assert fake_http.calls[0]["url"] == "http://quantbase.local/api/v2/settings/feishu-webhook"
    audit = json.loads(audit_path.read_text(encoding="utf-8").strip())
    assert audit["tool"] == "settings_feishu_webhook"
    assert audit["status"] == "success"
    assert audit["request"]["json"]["webhook_url"] == "***"
    assert audit["request"]["json"]["api_key"] == "***"
    assert "secret.example" not in json.dumps(audit, ensure_ascii=False)


def test_client_raises_error_envelope_and_audits_failure(tmp_path: Path) -> None:
    fake_http = FakeHttpClient(
        [httpx.Response(400, json={"success": False, "error": {"message": "bad request"}})]
    )
    audit_path = tmp_path / "mcp_audit.jsonl"
    client = QuantBaseMcpClient(
        base_url="http://quantbase.local/api/v2",
        audit_path=audit_path,
        http_client=fake_http,
    )

    with pytest.raises(QuantBaseMcpError) as exc:
        client.request("GET", "/system/health", tool_name="quantbase_health")

    assert "bad request" in str(exc.value)
    audit = json.loads(audit_path.read_text(encoding="utf-8").strip())
    assert audit["status"] == "error"
    assert audit["http_status"] == 400


def test_research_tools_map_to_existing_v2_routes() -> None:
    client = FakeQuantBaseClient()

    sync_start_history(
        client,
        symbols=["BTC/USDT:USDT"],
        timeframes=["15m", "1h"],
        history_days=365,
        exchange="okx",
    )
    backtest_start_job(
        client,
        strategy_id=42,
        start_date="2025-05-16",
        end_date="2026-05-15",
        initial_capital=100.0,
        timeframe_mode="matrix",
        timeframes=["15m", "30m", "1h"],
    )
    strategy_validate_code(
        code="from app.core.execution.base_strategy import BaseStrategy\n"
        "class Demo(BaseStrategy):\n"
        "    async def on_bar(self, bar):\n"
        "        return None\n"
    )

    assert client.calls[0] == (
        "POST",
        "/sync/start",
        {
            "json": {
                "exchange": "okx",
                "symbols": ["BTC/USDT:USDT"],
                "timeframes": ["15m", "1h"],
                "history_days": 365,
            },
            "tool_name": "sync_start_history",
        },
    )
    assert client.calls[1][0:2] == ("POST", "/backtest/run_job")
    assert client.calls[1][2]["json"]["timeframe_mode"] == "matrix"
    assert client.calls[1][2]["json"]["timeframes"] == ["15m", "30m", "1h"]
    assert client.calls[1][2]["tool_name"] == "backtest_start_job"


def test_live_mutation_tools_are_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("QUANTBASE_MCP_ENABLE_LIVE_TRADING", raising=False)
    client = FakeQuantBaseClient()

    with pytest.raises(LiveTradingDisabledError):
        trading_spot_order(
            client,
            symbol="BTC/USDT",
            side="buy",
            amount=0.01,
            confirm_live_risk=True,
            confirmation="I_UNDERSTAND_REAL_TRADING_RISK",
            reason="manual test",
            idempotency_key="risk-1",
        )

    assert client.calls == []
    assert quantbase_capabilities()["live_trading_enabled"] is False


def test_live_mutation_tools_require_confirmation_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QUANTBASE_MCP_ENABLE_LIVE_TRADING", "1")
    client = FakeQuantBaseClient()

    with pytest.raises(LiveConfirmationError):
        trading_spot_order(
            client,
            symbol="BTC/USDT",
            side="buy",
            amount=0.01,
            confirm_live_risk=True,
            confirmation="wrong",
            reason="manual test",
            idempotency_key="risk-2",
        )

    result = trading_spot_order(
        client,
        symbol="BTC/USDT",
        side="buy",
        amount=0.01,
        order_type="market",
        confirm_live_risk=True,
        confirmation="I_UNDERSTAND_REAL_TRADING_RISK",
        reason="manual test",
        idempotency_key="risk-3",
    )

    assert result["ok"] is True
    assert client.calls == [
        (
            "POST",
            "/trading/spot/order",
            {
                "json": {
                    "exchange": "okx",
                    "symbol": "BTC/USDT",
                    "side": "buy",
                    "type": "market",
                    "amount": 0.01,
                    "price": None,
                },
                "tool_name": "trading_spot_order",
                "audit_context": {"reason": "manual test", "idempotency_key": "risk-3"},
            },
        )
    ]
