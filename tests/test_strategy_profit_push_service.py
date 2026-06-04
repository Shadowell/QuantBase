from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List

from fastapi import FastAPI
from fastapi.testclient import TestClient


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = PROJECT_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.db.local_db import LocalDatabase  # noqa: E402
from app.api.endpoints import settings as settings_endpoint  # noqa: E402
from app.services.live_profit_push_service import LiveProfitPushService  # noqa: E402
from app.services.strategy_profit_push_service import StrategyProfitPushService, iso  # noqa: E402


class FakeDb:
    def __init__(self):
        self.config: Dict[str, Any] = {
            "enabled": False,
            "interval_minutes": 60,
            "running": False,
            "last_started_at": None,
            "last_sent_at": None,
            "last_finished_at": None,
            "last_error": None,
            "last_skip_reason": None,
        }

    def get_monitor_profit_push_config(self):
        return dict(self.config)

    def update_monitor_profit_push_config(self, updates):
        self.config.update(updates)
        return dict(self.config)

    def set_monitor_profit_push_runtime(self, **kwargs):
        self.config.update(kwargs)


class FakeEngine:
    def __init__(self, strategies: List[Dict[str, Any]] | None = None):
        self.strategies = strategies or []

    def get_all_running(self):
        return list(self.strategies)


class FakeNotifier:
    def __init__(self, *, send_ok: bool = True, ready: bool = True):
        self.send_ok = send_ok
        self.ready = ready
        self.reports: List[Dict[str, Any]] = []

    def is_ready(self, **_kwargs):
        return self.ready

    async def notify_strategy_profit_report(self, report):
        self.reports.append(report)
        return self.send_ok


class FakeLiveDb(FakeDb):
    def __init__(self):
        super().__init__()
        self.strategy_rows = {
            7: {
                "id": 7,
                "name": "[合约] CTA 趋势跟踪",
                "config": {
                    "trade_symbols": ["OPENAI/USDT:USDT"],
                    "symbols": ["OPENAI/USDT:USDT", "ANTHROPIC/USDT:USDT"],
                },
            },
            8: {
                "id": 8,
                "name": "[合约][15M][CTA] Top15 · 动态趋势跟踪 · 100U",
                "config": {
                    "trade_symbols": ["DOT/USDT:USDT"],
                    "symbols": ["DOT/USDT:USDT"],
                },
            }
        }

    def get_live_profit_push_config(self):
        return dict(self.config)

    def update_live_profit_push_config(self, updates):
        self.config.update(updates)
        return dict(self.config)

    def set_live_profit_push_runtime(self, **kwargs):
        self.config.update(kwargs)

    def get_strategy_by_id(self, strategy_id):
        return self.strategy_rows.get(int(strategy_id))


class FakeLiveAccounts:
    def __init__(self, accounts=None):
        self.accounts = accounts or [
            {
                "account_id": "default",
                "name": "默认 OKX 实盘账户",
                "enabled": True,
                "configured": True,
                "is_default": True,
            }
        ]

    def list_accounts(self):
        return list(self.accounts)

    def exchange_alias_for_account(self, account_id):
        return "okx" if str(account_id) == "default" else f"okx:{account_id}"


class FakeLiveExecution:
    DEPLOYED_STATUSES = {"running", "active", "deployed", "paused"}

    def __init__(self, subscriptions=None):
        self.subscriptions = subscriptions or [
            {"id": 3, "source_strategy_id": 7, "account_id": "default", "status": "running"},
            {"id": 4, "source_strategy_id": 8, "account_id": "default", "status": "paused"},
        ]

    def list_subscriptions(self, **kwargs):
        statuses = kwargs.get("statuses")
        if not statuses:
            return list(self.subscriptions)
        allowed = {str(status).lower() for status in statuses}
        return [
            item
            for item in self.subscriptions
            if str(item.get("status") or "").lower() in allowed
        ]

    def enrich_orders_with_attribution(self, *, account_id, orders):
        return list(orders)


class FakeLiveTrading:
    async def get_positions(self, exchange, symbol=None):
        return [
            {
                "symbol": "OPENAI/USDT:USDT",
                "side": "short",
                "contracts": 0.06,
                "entryPrice": 1422.75,
                "markPrice": 1412.5,
                "unrealizedPnl": 0.61,
            },
            {
                "symbol": "DOT/USDT:USDT",
                "side": "short",
                "contracts": 40,
                "entryPrice": 1.243,
                "markPrice": 1.241,
                "unrealizedPnl": 0.08,
            }
        ]

    async def get_balance_detail(self, exchange):
        return {
            "trading": [{"currency": "USDT", "free": 990.0, "used": 10.0, "total": 1000.0}],
            "funding": [],
        }

    async def get_order_history(self, exchange, symbol=None, limit=50):
        return [
            {
                "symbol": "OPENAI/USDT:USDT",
                "source_strategy_id": 7,
                "source_strategy_name": "[合约] CTA 趋势跟踪",
                "status": "closed",
                "pnl": 1.2,
                "filled": 0.03,
            },
            {
                "symbol": "DOT/USDT:USDT",
                "source_strategy_id": 8,
                "source_strategy_name": "[合约][15M][CTA] Top15 · 动态趋势跟踪 · 100U",
                "status": "closed",
                "pnl": -0.51,
                "filled": 40,
            }
        ]


async def _noop_enrich_market_metrics(snapshot: Dict[str, Any]) -> None:
    snapshot["long_short_ratio"] = 0.58


def _running_strategy(
    strategy_id: int,
    *,
    pnl: float,
    ret: float,
    equity: float,
    closing_trades: int = 2,
    winning_trades: int = 1,
    gross_profit: float = 30,
    gross_loss: float = 20,
):
    return {
        "strategy_id": strategy_id,
        "name": f"strategy-{strategy_id}",
        "status": "running",
        "exchange": "okx",
        "symbols": ["BTC/USDT", "ETH/USDT"],
        "pnl": pnl,
        "return_pct": ret,
        "equity": equity,
        "initial_capital": 10000,
        "balance": equity - 100,
        "unrealized_pnl": pnl / 2,
        "total_trades": 3,
        "closing_trades": closing_trades,
        "winning_trades": winning_trades,
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "win_rate": winning_trades / closing_trades * 100 if closing_trades else 0,
        "profit_factor": gross_profit / gross_loss if gross_loss else 0,
        "positions": {"BTC/USDT": {"size": 0.1, "entry_price": 49000, "mark_price": 50000}},
    }


def test_profit_push_config_defaults_off(tmp_path):
    db = LocalDatabase(str(tmp_path / "monitor_profit_push.db"))
    db.init_db()

    cfg = db.get_monitor_profit_push_config()

    assert cfg["enabled"] is False
    assert cfg["interval_minutes"] == 60
    assert cfg["running"] is False


def test_live_profit_push_config_defaults_off(tmp_path):
    db = LocalDatabase(str(tmp_path / "live_profit_push.db"))
    db.init_db()

    cfg = db.get_live_profit_push_config()

    assert cfg["enabled"] is False
    assert cfg["interval_minutes"] == 60
    assert cfg["running"] is False


def test_latest_feishu_webhook_uses_saved_alert_notification(tmp_path):
    db = LocalDatabase(str(tmp_path / "monitor_profit_push.db"))
    db.init_db()
    conn = db.get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO alerts (name, type, symbol, condition, notification, enabled)
        VALUES (?, ?, ?, ?, ?, 1)
        """,
        (
            "plain webhook",
            "price_above",
            "BTC/USDT",
            json.dumps({"symbol": "BTC/USDT", "threshold": 100000}),
            json.dumps({"webhook": {"url": "https://example.com/webhook"}}),
        ),
    )
    cursor.execute(
        """
        INSERT INTO alerts (name, type, symbol, condition, notification, enabled)
        VALUES (?, ?, ?, ?, ?, 1)
        """,
        (
            "feishu webhook",
            "price_above",
            "BTC/USDT",
            json.dumps({"symbol": "BTC/USDT", "threshold": 100000}),
            json.dumps({"webhook": {"url": "https://open.feishu.cn/open-apis/bot/v2/hook/test-token"}}),
        ),
    )
    conn.commit()

    assert db.get_latest_feishu_webhook_url() == "https://open.feishu.cn/open-apis/bot/v2/hook/test-token"


def test_central_feishu_webhook_setting_is_saved(tmp_path):
    db = LocalDatabase(str(tmp_path / "monitor_profit_push.db"))
    db.init_db()
    db.set_feishu_webhook_url("https://open.feishu.cn/open-apis/bot/v2/hook/settings-token")

    assert db.get_feishu_webhook_url() == "https://open.feishu.cn/open-apis/bot/v2/hook/settings-token"


def test_feishu_webhook_settings_api_saves_and_masks(monkeypatch, tmp_path):
    db = LocalDatabase(str(tmp_path / "settings.db"))
    db.init_db()
    db.set_monitor_profit_push_runtime(
        running=False,
        last_error="飞书推送未启用、Webhook 未配置或发送失败",
        last_skip_reason="missing_webhook",
    )
    monkeypatch.setattr(settings_endpoint, "db", db)
    app = FastAPI()
    app.include_router(settings_endpoint.router, prefix="/settings")
    client = TestClient(app, raise_server_exceptions=False)

    webhook = "https://open.feishu.cn/open-apis/bot/v2/hook/settings-token"
    response = client.post("/settings/feishu-webhook", json={"webhook_url": webhook})

    assert response.status_code == 200
    assert db.get_feishu_webhook_url() == webhook
    payload_text = json.dumps(response.json())
    assert webhook not in payload_text
    assert response.json()["webhook_configured"] is True
    cfg = db.get_monitor_profit_push_config()
    assert cfg["last_error"] is None
    assert cfg["last_skip_reason"] is None


def test_build_snapshot_sums_and_sorts_running_strategies():
    now = datetime(2026, 5, 3, 8, 0, tzinfo=timezone.utc)
    engine = FakeEngine(
        [
            _running_strategy(1, pnl=-50, ret=-0.5, equity=9950),
            _running_strategy(2, pnl=200, ret=2.0, equity=10200),
        ]
    )
    svc = StrategyProfitPushService(
        database=FakeDb(),
        engine=engine,
        notifier=FakeNotifier(),
        now_fn=lambda: now,
    )

    snapshot = svc.build_snapshot()

    assert snapshot["running_count"] == 2
    assert snapshot["total_equity"] == 20150
    assert snapshot["total_pnl"] == 150
    assert snapshot["total_return_pct"] == 0.75
    assert snapshot["total_position_notional_usdt"] == 10000
    assert snapshot["position_strategy_count"] == 2
    assert snapshot["total_trades"] == 6
    assert snapshot["closing_trades"] == 4
    assert snapshot["winning_trades"] == 2
    assert snapshot["win_rate"] == 50
    assert snapshot["profit_factor"] == 1.5
    assert [item["strategy_id"] for item in snapshot["strategies"]] == [2, 1]
    assert snapshot["strategies"][0]["positions_count"] == 1
    assert snapshot["strategies"][0]["positions"][0]["symbol"] == "BTC/USDT"
    assert snapshot["strategies"][0]["positions"][0]["size"] == 0.1
    assert snapshot["strategies"][0]["positions"][0]["notional_usdt"] == 5000


def test_live_profit_push_snapshot_uses_real_live_positions_and_orders():
    now = datetime(2026, 5, 12, 8, 0, tzinfo=timezone.utc)
    svc = LiveProfitPushService(
        database=FakeLiveDb(),
        account_service=FakeLiveAccounts(),
        live_execution_service=FakeLiveExecution(),
        trading_service=FakeLiveTrading(),
        notifier=FakeNotifier(),
        now_fn=lambda: now,
    )

    snapshot = asyncio.run(svc.build_snapshot())

    assert snapshot["report_scope"] == "live"
    assert snapshot["title"] == "实盘收益卡片"
    assert snapshot["running_count"] == 1
    assert snapshot["total_equity"] == 1000
    assert snapshot["total_unrealized_pnl"] == 0.61
    assert snapshot["total_pnl"] == 1.81
    assert snapshot["total_trades"] == 1
    assert snapshot["strategies"][0]["strategy_id"] == 7
    assert snapshot["strategies"][0]["name"] == "[合约] CTA 趋势跟踪"
    assert snapshot["strategies"][0]["account_name"] == "默认 OKX 实盘账户"
    assert snapshot["strategies"][0]["positions"][0]["symbol"] == "OPENAI/USDT:USDT"
    assert snapshot["strategies"][0]["positions"][0]["side"] == "short"
    assert all("Top15" not in item["name"] for item in snapshot["strategies"])


def test_run_once_sends_profit_report_and_updates_runtime():
    now = datetime(2026, 5, 3, 8, 0, tzinfo=timezone.utc)
    database = FakeDb()
    database.config["enabled"] = True
    notifier = FakeNotifier(send_ok=True)
    svc = StrategyProfitPushService(
        database=database,
        engine=FakeEngine([_running_strategy(1, pnl=100, ret=1.0, equity=10100)]),
        notifier=notifier,
        now_fn=lambda: now,
    )
    svc._enrich_market_metrics = _noop_enrich_market_metrics

    result = asyncio.run(svc.run_once())

    assert result["sent"] is True
    assert result["running_count"] == 1
    assert len(notifier.reports) == 1
    assert notifier.reports[0]["long_short_ratio"] == 0.58
    assert database.config["running"] is False
    assert database.config["last_sent_at"] == iso(now)
    assert database.config["last_error"] is None


def test_run_once_skips_empty_running_set_without_sending():
    now = datetime(2026, 5, 3, 8, 0, tzinfo=timezone.utc)
    database = FakeDb()
    database.config["enabled"] = True
    notifier = FakeNotifier()
    svc = StrategyProfitPushService(
        database=database,
        engine=FakeEngine([]),
        notifier=notifier,
        now_fn=lambda: now,
    )
    svc._enrich_market_metrics = _noop_enrich_market_metrics

    result = asyncio.run(svc.run_once())

    assert result["sent"] is False
    assert result["skipped"] == "no_running_strategies"
    assert notifier.reports == []
    assert database.config["last_skip_reason"] == "no_running_strategies"


def test_run_due_respects_configured_interval():
    now = datetime(2026, 5, 3, 8, 0, tzinfo=timezone.utc)
    database = FakeDb()
    database.config.update(
        {
            "enabled": True,
            "interval_minutes": 60,
            "last_finished_at": iso(now - timedelta(minutes=10)),
        }
    )
    notifier = FakeNotifier()
    svc = StrategyProfitPushService(
        database=database,
        engine=FakeEngine([_running_strategy(1, pnl=100, ret=1.0, equity=10100)]),
        notifier=notifier,
        now_fn=lambda: now,
    )

    result = asyncio.run(svc.run_due())

    assert result["skipped"] == "not_due"
    assert notifier.reports == []
