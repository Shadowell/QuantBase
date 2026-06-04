import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.api.v2.endpoints import live
from app.core.errors import BadRequestError, register_exception_handlers
from app.db.local_db import LocalDatabase
from app.services.live_signal_execution_service import LiveSignalExecutionService
from app.services import live_account_service


def build_client() -> TestClient:
    app = FastAPI()
    app.include_router(live.router, prefix="/api/v2/live")
    register_exception_handlers(app)
    return TestClient(app, raise_server_exceptions=False)


def _temp_db(tmp_path, monkeypatch) -> LocalDatabase:
    database = LocalDatabase(str(tmp_path / "quantbase-live-execution.db"))
    database.init_db()
    if hasattr(live, "_clear_live_private_read_cache"):
        live._clear_live_private_read_cache()
    monkeypatch.setattr(live, "db", database)
    monkeypatch.setattr(live_account_service, "db", database)
    monkeypatch.setattr(live, "live_signal_execution_service", LiveSignalExecutionService(database))
    monkeypatch.setattr(live, "_git_commit_ref", lambda: "test-sha")
    monkeypatch.setattr(
        live_account_service,
        "validate_okx_account_permissions",
        lambda **kwargs: {
            "can_read": True,
            "can_trade": True,
            "checked_at": "2026-05-09T00:00:00+00:00",
            "detail": "读取权限和交易权限测试通过",
        },
    )
    monkeypatch.setattr(
        live,
        "resolve_unified_base_strategy_class",
        lambda row: None if "Broken" in str(row.get("name") or "") else (object, row),
    )
    monkeypatch.setattr(live.strategy_engine, "get_strategy_status", lambda strategy_id: None)
    return database


def test_live_execution_strategy_settings_persist_without_deploy(tmp_path, monkeypatch):
    database = _temp_db(tmp_path, monkeypatch)
    paper_id = database.save_strategy(
        "[合约] Demo Paper",
        "class Demo: pass",
        config={"strategy_key": "demo", "is_paper_trading": True, "market_type": "swap"},
        exchange="okx",
        symbols=["BTC/USDT:USDT"],
    )
    database.save_strategy(
        "[合约] [实盘试运行] Demo",
        "class Demo: pass",
        config={"strategy_key": "demo", "is_paper_trading": False, "market_type": "swap"},
        exchange="okx",
        symbols=["BTC/USDT:USDT"],
    )
    database.save_strategy(
        "[合约] Broken Paper",
        "class Broken: pass",
        config={"strategy_key": "broken", "is_paper_trading": True, "market_type": "swap"},
        exchange="okx",
        symbols=["ETH/USDT:USDT"],
    )

    client = build_client()
    listed = client.get("/api/v2/live/strategies")
    assert listed.status_code == 200
    strategies = listed.json()["data"]["strategies"]
    assert [item["strategy_name"] for item in strategies] == ["[合约] Demo Paper"]

    added = client.patch(f"/api/v2/live/strategies/{paper_id}", json={"added": True})
    assert added.status_code == 200
    assert added.json()["data"]["strategy"]["added"] is True
    assert added.json()["data"]["strategy"]["deployed"] is False

    restored = client.get("/api/v2/live/strategies")
    assert restored.json()["data"]["strategies"][0]["added"] is True
    assert len(database.get_strategies()) == 3


def test_live_execution_added_strategy_cannot_be_removed_from_workspace(tmp_path, monkeypatch):
    database = _temp_db(tmp_path, monkeypatch)
    paper_id = database.save_strategy(
        "[合约] Added Paper",
        "class Demo: pass",
        config={"strategy_key": "demo", "is_paper_trading": True, "market_type": "swap"},
        exchange="okx",
        symbols=["BTC/USDT:USDT"],
    )

    client = build_client()
    client.patch(f"/api/v2/live/strategies/{paper_id}", json={"added": True})

    removed = client.patch(f"/api/v2/live/strategies/{paper_id}", json={"added": False})
    restored = client.get("/api/v2/live/strategies").json()["data"]["strategies"][0]

    assert removed.status_code == 400
    assert "不能从实盘策略列表删除" in str(removed.json())
    assert restored["added"] is True
    assert restored["account_ids"] == ["default"]


def test_live_execution_added_strategy_restores_after_backend_restart_when_resolver_unavailable(tmp_path, monkeypatch):
    database = _temp_db(tmp_path, monkeypatch)
    paper_id = database.save_strategy(
        "[合约] Restart Paper",
        "class Demo: pass",
        config={"strategy_key": "demo", "is_paper_trading": True, "market_type": "swap"},
        exchange="okx",
        symbols=["BTC/USDT:USDT"],
    )

    client = build_client()
    added = client.patch(f"/api/v2/live/strategies/{paper_id}", json={"added": True})
    assert added.status_code == 200

    restarted = LocalDatabase(str(tmp_path / "quantbase-live-execution.db"))
    restarted.init_db()
    monkeypatch.setattr(live, "db", restarted)
    monkeypatch.setattr(live_account_service, "db", restarted)
    monkeypatch.setattr(live, "resolve_unified_base_strategy_class", lambda row: None)

    restored = build_client().get("/api/v2/live/strategies")
    assert restored.status_code == 200
    strategies = restored.json()["data"]["strategies"]
    strategy = next((item for item in strategies if item["strategy_id"] == paper_id), None)
    assert strategy is not None
    assert strategy["added"] is True
    assert strategy["deployable"] is False
    assert strategy["account_ids"] == ["default"]


def test_live_execution_strategy_can_bind_multiple_accounts(tmp_path, monkeypatch):
    database = _temp_db(tmp_path, monkeypatch)
    paper_id = database.save_strategy(
        "[合约] Multi Account Paper",
        "class Demo: pass",
        config={"strategy_key": "demo", "is_paper_trading": True, "market_type": "swap"},
        exchange="okx",
        symbols=["BTC/USDT:USDT"],
    )
    client = build_client()
    created = client.post(
        "/api/v2/live/accounts",
        json={
            "name": "Second Account",
            "api_key": "abcd1234efgh5678",
            "api_secret": "secret-value",
            "passphrase": "pass-value",
        },
    )
    account_id = created.json()["data"]["account"]["account_id"]

    first = client.patch(f"/api/v2/live/strategies/{paper_id}", json={"added": True, "account_id": "default"})
    second = client.patch(f"/api/v2/live/strategies/{paper_id}", json={"added": True, "account_id": account_id})
    listed = client.get("/api/v2/live/strategies")

    assert first.status_code == 200
    assert second.status_code == 200
    strategy = listed.json()["data"]["strategies"][0]
    assert strategy["added"] is True
    assert strategy["account_ids"] == ["default", account_id]
    assert [item["account_id"] for item in strategy["account_bindings"]] == ["default", account_id]

    removed = client.patch(
        f"/api/v2/live/strategies/{paper_id}",
        json={"account_id": "default", "bind_account": False},
    )
    remaining = removed.json()["data"]["strategy"]
    assert remaining["added"] is True
    assert remaining["account_ids"] == [account_id]

    removed_last = client.patch(
        f"/api/v2/live/strategies/{paper_id}",
        json={"account_id": account_id, "bind_account": False},
    )
    empty = removed_last.json()["data"]["strategy"]
    assert empty["added"] is True
    assert empty["account_ids"] == []
    assert empty["account_bindings"] == []

    restored = client.get("/api/v2/live/strategies")
    restored_strategy = next(
        item for item in restored.json()["data"]["strategies"] if item["strategy_id"] == paper_id
    )
    assert restored_strategy["added"] is True
    assert restored_strategy["account_ids"] == []


def test_paper_position_close_endpoint_routes_to_paper_broker(tmp_path, monkeypatch):
    database = _temp_db(tmp_path, monkeypatch)
    paper_id = database.save_strategy(
        "[合约] Paper Close Demo",
        "class Demo: pass",
        config={"strategy_key": "demo", "is_paper_trading": True, "market_type": "swap"},
        exchange="okx",
        symbols=["BTC/USDT:USDT"],
    )
    calls = []

    async def fake_close_paper_position(strategy_id, *, symbol, side=None, market_type=None):
        calls.append(
            {
                "strategy_id": strategy_id,
                "symbol": symbol,
                "side": side,
                "market_type": market_type,
            }
        )
        return {"status": "filled", "symbol": symbol, "pos_side": side, "action": "close"}

    monkeypatch.setattr(live.strategy_engine, "close_paper_position", fake_close_paper_position)
    client = build_client()

    response = client.post(
        "/api/v2/live/positions/close",
        json={
            "instance_id": paper_id,
            "symbol": "BTC/USDT:USDT",
            "side": "long",
            "market_type": "swap",
        },
    )

    assert response.status_code == 200
    assert response.json()["data"]["closed"] is True
    assert calls == [
        {
            "strategy_id": paper_id,
            "symbol": "BTC/USDT:USDT",
            "side": "long",
            "market_type": "swap",
        }
    ]


def test_paper_position_close_endpoint_rejects_live_strategy(tmp_path, monkeypatch):
    database = _temp_db(tmp_path, monkeypatch)
    live_id = database.save_strategy(
        "[合约] Live Close Demo",
        "class Demo: pass",
        config={"strategy_key": "demo", "is_paper_trading": False, "market_type": "swap"},
        exchange="okx",
        symbols=["BTC/USDT:USDT"],
    )
    calls = []

    async def fake_close_paper_position(*args, **kwargs):
        calls.append((args, kwargs))
        return {"status": "filled"}

    monkeypatch.setattr(live.strategy_engine, "close_paper_position", fake_close_paper_position)
    client = build_client()

    response = client.post(
        "/api/v2/live/positions/close",
        json={"instance_id": live_id, "symbol": "BTC/USDT:USDT", "side": "long", "market_type": "swap"},
    )

    assert response.status_code == 400
    assert "仅支持模拟盘" in str(response.json())
    assert calls == []


def test_live_account_permission_check_uses_read_and_trade_probe(monkeypatch):
    calls = []

    class FakeNativeOKX:
        def fetch_balance(self, params):
            calls.append(("read", params))
            return {"USDT": {"free": 1}}

        def privatePostTradeCancelOrder(self, payload):
            calls.append(("trade", payload))
            return {"code": "1", "data": [{"sCode": "51603", "sMsg": "Order does not exist"}]}

    class FakeOKXExchange:
        def __init__(self, config):
            calls.append(("config", config))
            self.exchange = FakeNativeOKX()

        def initialize(self):
            calls.append(("initialize", None))

    monkeypatch.setattr(live_account_service, "OKXExchange", FakeOKXExchange)

    result = live_account_service.validate_okx_account_permissions(
        api_key="api-key",
        api_secret="api-secret",
        passphrase="pass",
        testnet=True,
    )

    assert result["can_read"] is True
    assert result["can_trade"] is True
    assert calls[0] == (
        "config",
        {
            "api_key": "api-key",
            "api_secret": "api-secret",
            "passphrase": "pass",
            "testnet": True,
        },
    )
    assert ("read", {"type": "trading"}) in calls
    trade_payload = next(payload for action, payload in calls if action == "trade")
    assert trade_payload["instId"] == "BTC-USDT"
    assert trade_payload["clOrdId"].startswith("bpperm")
    assert "_" not in trade_payload["clOrdId"]


def test_live_account_create_rejects_when_trade_permission_check_fails(tmp_path, monkeypatch):
    _temp_db(tmp_path, monkeypatch)

    def fail_permission(**kwargs):
        raise BadRequestError("账户 API 交易权限测试失败：当前 API Key 缺少 Trade 权限")

    monkeypatch.setattr(live_account_service, "validate_okx_account_permissions", fail_permission)
    client = build_client()

    created = client.post(
        "/api/v2/live/accounts",
        json={
            "name": "Read Only Account",
            "api_key": "abcd1234efgh5678",
            "api_secret": "secret-value",
            "passphrase": "pass-value",
        },
    )
    accounts = client.get("/api/v2/live/accounts")

    assert created.status_code == 400
    assert "交易权限测试失败" in str(created.json())
    account_ids = [item["account_id"] for item in accounts.json()["data"]["accounts"]]
    account_names = [item["name"] for item in accounts.json()["data"]["accounts"]]
    assert "default" in account_ids
    assert "Read Only Account" not in account_names


def test_live_execution_preflight_failure_does_not_create_live_strategy(tmp_path, monkeypatch):
    database = _temp_db(tmp_path, monkeypatch)
    paper_id = database.save_strategy(
        "[合约] Demo Paper",
        "class Demo: pass",
        config={"strategy_key": "demo", "is_paper_trading": True, "market_type": "swap"},
        exchange="okx",
        symbols=["BTC/USDT:USDT"],
    )

    async def fake_preflight(body, prepared=None):
        return {"all_passed": False, "checks": [{"item": "余额", "passed": False, "detail": "不足"}]}

    monkeypatch.setattr(live, "_run_promote_preflight", fake_preflight)
    client = build_client()
    client.patch(f"/api/v2/live/strategies/{paper_id}", json={"added": True})

    result = client.post(f"/api/v2/live/strategies/{paper_id}/preflight", json={"loop_interval": 60})

    assert result.status_code == 200
    assert result.json()["data"]["preflight"]["all_passed"] is False
    assert len(database.get_strategies()) == 1


def test_live_execution_deploy_creates_account_subscription_without_cloning_strategy(tmp_path, monkeypatch):
    database = _temp_db(tmp_path, monkeypatch)
    paper_id = database.save_strategy(
        "[合约] Demo Paper",
        "class Demo: pass",
        config={"strategy_key": "demo", "is_paper_trading": True, "market_type": "swap"},
        exchange="okx",
        symbols=["BTC/USDT:USDT"],
    )

    async def fake_preflight(body, prepared=None):
        return {
            "all_passed": True,
            "checks": [{"item": "策略存在性", "passed": True}],
            "plan": {
                "symbol_scope": "dynamic_runtime_symbols",
                "symbols": ["BTC/USDT:USDT"],
                "excluded_symbols": ["PEPE/USDT:USDT"],
            },
        }

    monkeypatch.setattr(live, "_run_promote_preflight", fake_preflight)

    client = build_client()
    client.patch(f"/api/v2/live/strategies/{paper_id}", json={"added": True})
    before_count = len(database.get_strategies())
    deployed = client.post(
        f"/api/v2/live/strategies/{paper_id}/deploy",
        json={
            "initial_equity": 100,
            "loop_interval": 60,
            "confirm_paper_reviewed": True,
            "confirm_live_risk": True,
        },
    )

    assert deployed.status_code == 200
    data = deployed.json()["data"]
    assert data["deployed"] is True
    assert data["live_strategy_id"] is None
    assert data["live_subscription_id"] > 0
    source = database.get_strategy_by_id(paper_id)
    assert source["config"]["is_paper_trading"] is True
    assert len(database.get_strategies()) == before_count

    listed = client.get("/api/v2/live/strategies").json()["data"]["strategies"][0]
    assert listed["added"] is True
    assert listed["deployed"] is True
    assert listed["deployment_strategy_id"] is None
    assert listed["account_bindings"][0]["deployment_strategy_id"] is None
    assert listed["account_bindings"][0]["live_subscription_id"] == data["live_subscription_id"]
    assert listed["account_bindings"][0]["deployment_status"] == "running"
    subscription = live.live_signal_execution_service.get_subscription(paper_id, "default")
    assert subscription["risk_config"]["allowed_live_symbols"] == ["BTC/USDT:USDT"]
    assert subscription["risk_config"]["excluded_live_symbols"] == ["PEPE/USDT:USDT"]


def test_live_execution_subscription_stop_clears_deployed_state_but_pause_keeps_it(tmp_path, monkeypatch):
    database = _temp_db(tmp_path, monkeypatch)
    paper_id = database.save_strategy(
        "[合约] Stop Binding Paper",
        "class Demo: pass",
        config={"strategy_key": "demo", "is_paper_trading": True, "market_type": "swap"},
        exchange="okx",
        symbols=["BTC/USDT:USDT"],
    )

    async def fake_preflight(body, prepared=None):
        return {"all_passed": True, "checks": [{"item": "策略存在性", "passed": True}]}

    monkeypatch.setattr(live, "_run_promote_preflight", fake_preflight)

    client = build_client()
    client.patch(f"/api/v2/live/strategies/{paper_id}", json={"added": True})
    deployed = client.post(
        f"/api/v2/live/strategies/{paper_id}/deploy",
        json={
            "initial_equity": 100,
            "loop_interval": 60,
            "confirm_paper_reviewed": True,
            "confirm_live_risk": True,
        },
    )
    subscription_id = deployed.json()["data"]["live_subscription_id"]

    paused = client.post(f"/api/v2/live/strategies/{paper_id}/pause", json={"account_id": "default"})
    after_pause = client.get("/api/v2/live/strategies").json()["data"]["strategies"][0]
    pause_binding = after_pause["account_bindings"][0]

    assert paused.status_code == 200
    assert after_pause["deployed"] is True
    assert pause_binding["deployment_strategy_id"] is None
    assert pause_binding["live_subscription_id"] == subscription_id
    assert pause_binding["deployment_status"] == "paused"

    stopped = client.post(f"/api/v2/live/strategies/{paper_id}/stop", json={"account_id": "default"})
    after_stop = client.get("/api/v2/live/strategies").json()["data"]["strategies"][0]
    stop_binding = after_stop["account_bindings"][0]

    assert stopped.status_code == 200
    assert after_stop["added"] is True
    assert after_stop["deployed"] is False
    assert after_stop["deployment_strategy_id"] is None
    assert after_stop["account_ids"] == ["default"]
    assert stop_binding["deployment_strategy_id"] is None
    assert stop_binding["deployed"] is False
    assert stop_binding["status"] == "added"


def test_live_execution_subscription_controls_do_not_touch_source_paper_runtime(tmp_path, monkeypatch):
    database = _temp_db(tmp_path, monkeypatch)
    paper_id = database.save_strategy(
        "[合约] Runtime Isolation Paper",
        "class Demo: pass",
        config={"strategy_key": "demo", "is_paper_trading": True, "market_type": "swap"},
        exchange="okx",
        symbols=["BTC/USDT:USDT"],
    )
    database.update_strategy_status(paper_id, "running", clear_run_started_at=False)
    engine_calls = []

    async def fake_preflight(body, prepared=None):
        return {"all_passed": True, "checks": [{"item": "策略存在性", "passed": True}]}

    async def unexpected_pause_strategy(strategy_id):
        engine_calls.append(("pause", strategy_id))
        raise AssertionError("live subscription pause must not pause the source paper strategy")

    async def unexpected_stop_strategy(strategy_id, *args, **kwargs):
        engine_calls.append(("stop", strategy_id))
        raise AssertionError("live subscription stop must not stop the source paper strategy")

    monkeypatch.setattr(live, "_run_promote_preflight", fake_preflight)
    monkeypatch.setattr(live.strategy_engine, "pause_strategy", unexpected_pause_strategy)
    monkeypatch.setattr(live.strategy_engine, "stop_strategy", unexpected_stop_strategy)

    client = build_client()
    client.patch(f"/api/v2/live/strategies/{paper_id}", json={"added": True})
    deployed = client.post(
        f"/api/v2/live/strategies/{paper_id}/deploy",
        json={
            "initial_equity": 100,
            "loop_interval": 60,
            "confirm_paper_reviewed": True,
            "confirm_live_risk": True,
        },
    )
    assert deployed.status_code == 200

    paused = client.post(f"/api/v2/live/strategies/{paper_id}/pause", json={"account_id": "default"})
    after_pause = database.get_strategy_by_id(paper_id)
    pause_subscription = live.live_signal_execution_service.get_subscription(paper_id, "default")

    assert paused.status_code == 200
    assert after_pause["status"] == "running"
    assert pause_subscription["status"] == "paused"

    stopped = client.post(f"/api/v2/live/strategies/{paper_id}/stop", json={"account_id": "default"})
    after_stop = database.get_strategy_by_id(paper_id)
    stop_subscription = live.live_signal_execution_service.get_subscription(paper_id, "default")

    assert stopped.status_code == 200
    assert after_stop["status"] == "running"
    assert stop_subscription["status"] == "stopped"
    assert engine_calls == []


def test_live_execution_deployed_strategy_cannot_be_removed_from_workspace(tmp_path, monkeypatch):
    database = _temp_db(tmp_path, monkeypatch)
    paper_id = database.save_strategy(
        "[合约] Removed Binding Paper",
        "class Demo: pass",
        config={"strategy_key": "demo", "is_paper_trading": True, "market_type": "swap"},
        exchange="okx",
        symbols=["TRX/USDT:USDT"],
    )

    async def fake_preflight(body, prepared=None):
        return {"all_passed": True, "checks": [{"item": "策略存在性", "passed": True}]}

    monkeypatch.setattr(live, "_run_promote_preflight", fake_preflight)

    client = build_client()
    client.patch(f"/api/v2/live/strategies/{paper_id}", json={"added": True})
    deployed = client.post(
        f"/api/v2/live/strategies/{paper_id}/deploy",
        json={
            "initial_equity": 100,
            "loop_interval": 60,
            "confirm_paper_reviewed": True,
            "confirm_live_risk": True,
        },
    )
    assert deployed.status_code == 200

    removed = client.patch(f"/api/v2/live/strategies/{paper_id}", json={"added": False})
    assert removed.status_code == 400
    assert "不能从实盘策略列表删除" in str(removed.json())
    visible = client.get("/api/v2/live/strategies").json()["data"]["strategies"][0]
    assert visible["deployed"] is True
    assert visible["account_bindings"][0]["status"] != "removed"

    paused = client.post(f"/api/v2/live/strategies/{paper_id}/pause", json={"account_id": "default"})
    after_pause = client.get("/api/v2/live/strategies").json()["data"]["strategies"][0]
    assert paused.status_code == 200
    assert after_pause["deployed"] is True
    assert after_pause["account_bindings"][0]["added"] is True
    assert after_pause["account_bindings"][0]["deployment_status"] == "paused"

    stopped = client.post(f"/api/v2/live/strategies/{paper_id}/stop", json={"account_id": "default"})
    after_stop = client.get("/api/v2/live/strategies").json()["data"]["strategies"][0]
    assert stopped.status_code == 200
    assert after_stop["added"] is True
    assert after_stop["deployed"] is False
    assert after_stop["account_bindings"][0]["added"] is True
    assert after_stop["account_bindings"][0]["status"] == "added"


def test_live_execution_account_endpoints_use_selected_okx_account(tmp_path, monkeypatch):
    _temp_db(tmp_path, monkeypatch)
    seen = []

    class FakeTradingService:
        async def get_balance(self, exchange):
            seen.append(("balance", exchange))
            return [
                {"currency": "USDT", "free": 12, "used": 1, "total": 13},
                {"currency": "BTC", "free": 0.01, "used": 0, "total": 0.01},
            ]

        async def get_balance_detail(self, exchange):
            seen.append(("balance_detail", exchange))
            return {
                "trading": [{"currency": "BTC", "free": 0.01, "used": 0, "total": 0.01}],
                "funding": [{"currency": "USDT", "free": 12, "used": 1, "total": 13}],
            }

        async def get_account_return_rates(self, exchange):
            seen.append(("return_rates", exchange))
            return {
                "one_day": 1.2,
                "seven_day": -2.3,
                "thirty_day": 4.56,
                "source": "okx",
            }

        async def get_positions(self, exchange, symbol=None):
            seen.append(("positions", exchange))
            return [{"symbol": symbol or "BTC/USDT:USDT", "amount": 1}]

        async def get_open_orders(self, exchange, symbol=None):
            seen.append(("open", exchange))
            return [{"id": "open-1", "symbol": symbol or "BTC/USDT:USDT"}]

        async def get_order_history(self, exchange, symbol=None, limit=50):
            seen.append(("history", exchange))
            return [{"id": "hist-1", "symbol": symbol or "BTC/USDT:USDT", "limit": limit}]

    monkeypatch.setattr(live, "trading_service", FakeTradingService())
    client = build_client()

    accounts = client.get("/api/v2/live/accounts")
    created = client.post(
        "/api/v2/live/accounts",
        json={
            "name": "Main Account",
            "api_key": "abcd1234efgh5678",
            "api_secret": "secret-value",
            "passphrase": "pass-value",
        },
    )
    account = created.json()["data"]["account"]
    account_id = account["account_id"]

    default_balance = client.get("/api/v2/live/accounts/default/balance")
    balance_detail = client.get("/api/v2/live/accounts/default/balance/detail")
    positions = client.get("/api/v2/live/accounts/default/positions?symbol=BTC/USDT:USDT")
    open_orders = client.get(f"/api/v2/live/accounts/{account_id}/orders/open")
    history = client.get(f"/api/v2/live/accounts/{account_id}/orders/history?limit=5")
    unsupported = client.get("/api/v2/live/accounts/sub-account/positions")

    assert accounts.status_code == 200
    assert accounts.json()["data"]["accounts"][0]["account_id"] == "default"
    assert created.status_code == 200
    assert account["masked_api_key"] == "abcd****5678"
    assert account["can_trade"] is True
    assert account["permission_check"]["can_trade"] is True
    assert account["permission_check_detail"] == "读取权限和交易权限测试通过"
    assert "api_secret" not in account
    assert default_balance.status_code == 200
    assert balance_detail.status_code == 200
    assert balance_detail.json()["data"]["trading"][0]["currency"] == "BTC"
    assert balance_detail.json()["data"]["return_rates"] == {
        "one_day": 1.2,
        "seven_day": -2.3,
        "thirty_day": 4.56,
        "source": "okx",
    }
    assert positions.status_code == 200
    assert positions.json()["data"]["positions"][0]["symbol"] == "BTC/USDT:USDT"
    assert positions.json()["data"]["positions"][1]["symbol"] == "BTC/USDT"
    assert positions.json()["data"]["positions"][1]["asset_type"] == "spot"
    assert open_orders.status_code == 200
    assert history.json()["data"]["orders"][0]["limit"] == 5
    assert unsupported.status_code == 400
    assert ("balance", "okx") in seen
    assert ("balance_detail", "okx") in seen
    assert ("return_rates", "okx") in seen
    assert ("positions", "okx") in seen
    assert ("open", f"okx:{account_id}") in seen
    assert ("history", f"okx:{account_id}") in seen


def test_live_account_private_reads_use_short_ttl_cache(tmp_path, monkeypatch):
    _temp_db(tmp_path, monkeypatch)
    if hasattr(live, "_clear_live_private_read_cache"):
        live._clear_live_private_read_cache()
    calls = {"positions": 0, "balance": 0, "history": 0}

    class FakeTradingService:
        async def get_positions(self, exchange, symbol=None):
            calls["positions"] += 1
            return [{"symbol": symbol or "BTC/USDT:USDT", "contracts": 1, "side": "long"}]

        async def get_balance(self, exchange):
            calls["balance"] += 1
            return []

        async def get_order_history(self, exchange, symbol=None, limit=50):
            calls["history"] += 1
            return [{"id": f"hist-{calls['history']}", "symbol": symbol or "BTC/USDT:USDT"}]

    monkeypatch.setattr(live, "trading_service", FakeTradingService())
    client = build_client()

    first_positions = client.get("/api/v2/live/accounts/default/positions?symbol=BTC/USDT:USDT")
    second_positions = client.get("/api/v2/live/accounts/default/positions?symbol=BTC/USDT:USDT")
    first_history = client.get("/api/v2/live/accounts/default/orders/history?limit=5")
    second_history = client.get("/api/v2/live/accounts/default/orders/history?limit=5")

    assert first_positions.status_code == 200
    assert second_positions.status_code == 200
    assert first_history.status_code == 200
    assert second_history.status_code == 200
    assert first_history.json()["data"]["orders"] == second_history.json()["data"]["orders"]
    assert calls == {"positions": 1, "balance": 1, "history": 1}


def test_live_account_asset_reads_use_one_minute_ttl_cache(tmp_path, monkeypatch):
    _temp_db(tmp_path, monkeypatch)
    if hasattr(live, "_clear_live_private_read_cache"):
        live._clear_live_private_read_cache()
    clock = {"now": 1_000.0}
    calls = {"balance": 0, "detail": 0, "rates": 0}

    monkeypatch.setattr(live.time, "monotonic", lambda: clock["now"])

    class FakeTradingService:
        async def get_balance(self, exchange):
            calls["balance"] += 1
            return [{"currency": "USDT", "total": calls["balance"]}]

        async def get_balance_detail(self, exchange):
            calls["detail"] += 1
            return {
                "trading": [{"currency": "USDT", "total": calls["detail"]}],
                "funding": [],
            }

        async def get_account_return_rates(self, exchange):
            calls["rates"] += 1
            return {"one_day": calls["rates"], "source": "okx"}

    monkeypatch.setattr(live, "trading_service", FakeTradingService())
    client = build_client()

    first_balance = client.get("/api/v2/live/accounts/default/balance")
    first_detail = client.get("/api/v2/live/accounts/default/balance/detail")
    second_balance = client.get("/api/v2/live/accounts/default/balance")
    second_detail = client.get("/api/v2/live/accounts/default/balance/detail")
    clock["now"] += 59.0
    within_ttl_balance = client.get("/api/v2/live/accounts/default/balance")
    within_ttl_detail = client.get("/api/v2/live/accounts/default/balance/detail")
    clock["now"] += 2.0
    expired_balance = client.get("/api/v2/live/accounts/default/balance")
    expired_detail = client.get("/api/v2/live/accounts/default/balance/detail")

    assert first_balance.status_code == 200
    assert first_detail.status_code == 200
    assert second_balance.json()["data"]["balance"] == first_balance.json()["data"]["balance"]
    assert second_detail.json()["data"]["trading"] == first_detail.json()["data"]["trading"]
    assert within_ttl_balance.json()["data"]["balance"] == first_balance.json()["data"]["balance"]
    assert within_ttl_detail.json()["data"]["return_rates"] == first_detail.json()["data"]["return_rates"]
    assert expired_balance.json()["data"]["balance"][0]["total"] == 2
    assert expired_detail.json()["data"]["trading"][0]["total"] == 2
    assert expired_detail.json()["data"]["return_rates"]["one_day"] == 2
    assert calls == {"balance": 2, "detail": 2, "rates": 2}


def test_live_account_position_close_uses_live_contract_broker(tmp_path, monkeypatch):
    _temp_db(tmp_path, monkeypatch)
    calls = []

    class FakeLiveContractBroker:
        def __init__(self, *, strategy_id, exchange_name, symbols, config):
            calls.append(
                {
                    "strategy_id": strategy_id,
                    "exchange_name": exchange_name,
                    "symbols": symbols,
                    "config": config,
                }
            )

        async def close_contract(self, symbol, side, ratio=1.0, contracts=None, price=None):
            calls.append(
                {
                    "symbol": symbol,
                    "side": side,
                    "ratio": ratio,
                    "contracts": contracts,
                    "price": price,
                }
            )
            return {
                "status": "filled",
                "symbol": symbol,
                "pos_side": side,
                "order_side": "buy" if side == "short" else "sell",
            }

    monkeypatch.setattr(live, "LiveContractBroker", FakeLiveContractBroker)
    client = build_client()

    missing_confirm = client.post(
        "/api/v2/live/accounts/default/positions/close",
        json={"symbol": "DOGE/USDT:USDT", "side": "short"},
    )
    response = client.post(
        "/api/v2/live/accounts/default/positions/close",
        json={"symbol": "DOGE/USDT:USDT", "side": "short", "confirm_live_risk": True},
    )

    assert missing_confirm.status_code == 400
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["closed"] == 1
    assert data["results"][0]["status"] == "filled"
    assert calls[0]["strategy_id"] == 0
    assert calls[0]["exchange_name"] == "okx"
    assert calls[0]["symbols"] == ["DOGE/USDT:USDT"]
    assert calls[0]["config"]["live_order_type"] == "market"
    assert calls[1] == {
        "symbol": "DOGE/USDT:USDT",
        "side": "short",
        "ratio": 1.0,
        "contracts": None,
        "price": None,
    }


def test_live_account_position_market_close_all_closes_all_sides_for_symbol(tmp_path, monkeypatch):
    _temp_db(tmp_path, monkeypatch)
    close_calls = []

    class FakeTradingService:
        async def get_positions(self, exchange, symbol=None):
            assert exchange == "okx"
            assert symbol == "DOGE/USDT:USDT"
            return [
                {"symbol": "DOGE/USDT:USDT", "side": "long", "contracts": 0.2},
                {"symbol": "DOGE/USDT:USDT", "side": "short", "contracts": 0.3},
                {"symbol": "ETH/USDT:USDT", "side": "long", "contracts": 0.1},
            ]

    class FakeLiveContractBroker:
        def __init__(self, *, strategy_id, exchange_name, symbols, config):
            self.symbols = symbols

        async def close_contract(self, symbol, side, ratio=1.0, contracts=None, price=None):
            close_calls.append((symbol, side, ratio))
            return {"status": "filled", "symbol": symbol, "pos_side": side}

    monkeypatch.setattr(live, "trading_service", FakeTradingService())
    monkeypatch.setattr(live, "LiveContractBroker", FakeLiveContractBroker)
    client = build_client()

    response = client.post(
        "/api/v2/live/accounts/default/positions/close",
        json={"symbol": "DOGE/USDT:USDT", "close_all": True, "confirm_live_risk": True},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["closed"] == 2
    assert close_calls == [
        ("DOGE/USDT:USDT", "long", 1.0),
        ("DOGE/USDT:USDT", "short", 1.0),
    ]


def test_live_account_position_market_close_all_uses_side_when_pos_side_is_net(tmp_path, monkeypatch):
    _temp_db(tmp_path, monkeypatch)
    close_calls = []

    class FakeTradingService:
        async def get_positions(self, exchange, symbol=None):
            assert exchange == "okx"
            assert symbol == "1INCH/USDT:USDT"
            return [
                {"symbol": "1INCH/USDT:USDT", "side": "short", "pos_side": "net", "contracts": 514.0},
            ]

    class FakeLiveContractBroker:
        def __init__(self, *, strategy_id, exchange_name, symbols, config):
            self.symbols = symbols

        async def close_contract(self, symbol, side, ratio=1.0, contracts=None, price=None):
            close_calls.append((symbol, side, ratio))
            return {"status": "filled", "symbol": symbol, "pos_side": side}

    monkeypatch.setattr(live, "trading_service", FakeTradingService())
    monkeypatch.setattr(live, "LiveContractBroker", FakeLiveContractBroker)
    client = build_client()

    response = client.post(
        "/api/v2/live/accounts/default/positions/close",
        json={"symbol": "1INCH/USDT:USDT", "close_all": True, "confirm_live_risk": True},
    )

    assert response.status_code == 200
    assert close_calls == [("1INCH/USDT:USDT", "short", 1.0)]


def test_live_execution_order_history_includes_strategy_attribution(tmp_path, monkeypatch):
    database = _temp_db(tmp_path, monkeypatch)
    paper_id = database.save_strategy(
        "[合约] Attribution Source",
        "class Demo: pass",
        config={"strategy_key": "demo", "is_paper_trading": True, "market_type": "swap"},
        exchange="okx",
        symbols=["DOGE/USDT:USDT"],
    )
    service = live.live_signal_execution_service
    subscription = service.upsert_subscription(
        source_strategy_id=paper_id,
        account_id="default",
        status="running",
    )
    event = service.insert_signal_event(
        source_strategy_id=paper_id,
        exchange="okx",
        market_type="swap",
        action="open",
        symbol="DOGE/USDT:USDT",
        side="short",
    )
    service._insert_execution(
        event_id=event["id"],
        subscription=subscription,
        exchange="okx",
        status="filled",
        live_order_id=None,
        request_payload={"client_order_id": "bpls1e1abc"},
        response_payload={"client_order_id": "bpls1e1abc"},
    )

    class FakeTradingService:
        async def get_order_history(self, exchange, symbol=None, limit=50):
            return [
                {
                    "id": "exchange-order-1",
                    "client_order_id": "bpls1e1abc",
                    "symbol": "DOGE/USDT:USDT",
                    "status": "closed",
                },
                {
                    "id": "manual-order-1",
                    "client_order_id": "manual-client-1",
                    "symbol": "DOGE/USDT:USDT",
                    "status": "closed",
                },
            ]

    monkeypatch.setattr(live, "trading_service", FakeTradingService())
    client = build_client()
    response = client.get("/api/v2/live/accounts/default/orders/history?limit=5")

    assert response.status_code == 200
    orders = response.json()["data"]["orders"]
    assert orders[0]["quantbase_source"] == "strategy"
    assert orders[0]["quantbase_source_label"] == "[合约] Attribution Source"
    assert orders[0]["source_strategy_id"] == paper_id
    assert orders[0]["source_strategy_name"] == "[合约] Attribution Source"
    assert orders[0]["subscription_id"] == subscription["id"]
    assert orders[0]["signal_event_id"] == event["id"]
    assert orders[1]["quantbase_source"] == "external"
    assert orders[1]["quantbase_source_label"] == "手动/外部订单"
    assert orders[1]["source_strategy_id"] is None


def test_live_execution_order_history_includes_rejected_live_executions(tmp_path, monkeypatch):
    database = _temp_db(tmp_path, monkeypatch)
    paper_id = database.save_strategy(
        "[合约] Rejected Source",
        "class Demo: pass",
        config={"strategy_key": "demo", "is_paper_trading": True, "market_type": "swap"},
        exchange="okx",
        symbols=["ANTHROPIC/USDT:USDT"],
    )
    service = live.live_signal_execution_service
    subscription = service.upsert_subscription(
        source_strategy_id=paper_id,
        account_id="default",
        status="running",
    )
    event = service.insert_signal_event(
        source_strategy_id=paper_id,
        exchange="okx",
        market_type="swap",
        action="open",
        symbol="ANTHROPIC/USDT:USDT",
        side="long",
        price=1554.5,
        quantity=0.01,
    )
    execution = service._insert_execution(
        event_id=event["id"],
        subscription=subscription,
        exchange="okx",
        status="failed",
        live_order_id=None,
        request_payload={"client_order_id": "bpls1e1reject", "action": "open", "side": "long"},
        response_payload={"code": "1", "data": [{"sCode": "51000", "sMsg": "Parameter posSide error"}]},
        error="OKX rejected order: 51000 Parameter posSide error",
    )

    class FakeTradingService:
        async def get_order_history(self, exchange, symbol=None, limit=50):
            return []

    monkeypatch.setattr(live, "trading_service", FakeTradingService())
    client = build_client()
    response = client.get("/api/v2/live/accounts/default/orders/history?limit=5")

    assert response.status_code == 200
    orders = response.json()["data"]["orders"]
    assert len(orders) == 1
    assert orders[0]["id"] == f"live-execution-{execution['id']}"
    assert orders[0]["status"] == "failed"
    assert orders[0]["raw_status"] == "failed"
    assert orders[0]["quantbase_source"] == "strategy"
    assert orders[0]["quantbase_source_label"] == "[合约] Rejected Source"
    assert orders[0]["source_strategy_id"] == paper_id
    assert orders[0]["subscription_id"] == subscription["id"]
    assert orders[0]["signal_event_id"] == event["id"]
    assert orders[0]["failure_log"]["error"] == "OKX rejected order: 51000 Parameter posSide error"
    assert orders[0]["failure_log"]["response_payload"]["data"][0]["sCode"] == "51000"
