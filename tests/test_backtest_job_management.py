from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi import HTTPException
from fastapi.testclient import TestClient


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = PROJECT_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.api.endpoints import backtest  # noqa: E402
from app.db.local_db import LocalDatabase  # noqa: E402


def _client_with_temp_db(monkeypatch, tmp_path) -> tuple[TestClient, LocalDatabase]:
    temp_db = LocalDatabase(str(tmp_path / "crypto_data.db"))
    temp_db.init_db()
    monkeypatch.setattr(backtest, "db", temp_db)
    with backtest._BACKTEST_CANCEL_LOCK:
        backtest._BACKTEST_CANCEL_REQUESTS.clear()

    app = FastAPI()
    app.include_router(backtest.router, prefix="/backtest")
    return TestClient(app, raise_server_exceptions=False), temp_db


def _insert_backtest_result(
    db: LocalDatabase,
    *,
    strategy_id: int = 1,
    strategy_name: str = "[现货] 测试策略",
    symbols: str = "BTC/USDT",
    timeframe: str | None = None,
) -> int:
    conn = db.get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT OR IGNORE INTO strategies (id, name, description, script_content, config, status, exchange, symbols)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (strategy_id, strategy_name, "test", "class Strategy: pass", "{}", "stopped", "okx", symbols),
    )
    cursor.execute(
        """
        INSERT INTO backtest_results (
            strategy_id, start_date, end_date, initial_capital, final_capital,
            total_return, annual_return, max_drawdown, sharpe_ratio, win_rate,
            profit_factor, total_trades, trades_detail, timeframe, status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            strategy_id,
            "2026-01-01",
            "2026-02-01",
            10000,
            10100,
            1.0,
            12.0,
            2.0,
            1.2,
            55.0,
            1.4,
            8,
            "[]",
            timeframe,
            "completed",
        ),
    )
    rid = int(cursor.lastrowid)
    conn.commit()
    conn.close()
    return rid


def _insert_backtest_job(db: LocalDatabase, job_id: str, status: str = "running") -> None:
    conn = db.get_connection()
    conn.execute(
        """
        INSERT OR IGNORE INTO strategies (id, name, description, script_content, config, status, exchange, symbols)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (1, "[现货] 测试策略", "test", "class Strategy: pass", "{}", "stopped", "okx", "BTC/USDT"),
    )
    conn.execute(
        """
        INSERT INTO backtest_jobs (
            job_id, strategy_id, request_json, status, current_bar, total_bars
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            job_id,
            1,
            '{"strategy_id":1,"exchange":"okx","start_date":"2026-01-01","end_date":"2026-02-01","initial_capital":10000}',
            status,
            12,
            100,
        ),
    )
    conn.commit()
    conn.close()


def test_list_backtest_results_supports_offset(monkeypatch, tmp_path) -> None:
    client, db = _client_with_temp_db(monkeypatch, tmp_path)
    first_id = _insert_backtest_result(db)
    second_id = _insert_backtest_result(db)
    conn = db.get_connection()
    conn.execute(
        "UPDATE backtest_results SET created_at = ? WHERE id = ?",
        ("2026-01-01 00:00:00", first_id),
    )
    conn.execute(
        "UPDATE backtest_results SET created_at = ? WHERE id = ?",
        ("2026-01-02 00:00:00", second_id),
    )
    conn.commit()
    conn.close()

    response = client.get("/backtest/results?limit=1&offset=1")

    assert response.status_code == 200
    body = response.json()
    assert [item["id"] for item in body] == [first_id]


def test_list_backtest_results_sorts_return_before_pagination(monkeypatch, tmp_path) -> None:
    client, db = _client_with_temp_db(monkeypatch, tmp_path)
    low_id = _insert_backtest_result(db)
    high_id = _insert_backtest_result(db)
    middle_id = _insert_backtest_result(db)
    conn = db.get_connection()
    conn.execute("UPDATE backtest_results SET total_return = ? WHERE id = ?", (-4.0, low_id))
    conn.execute("UPDATE backtest_results SET total_return = ? WHERE id = ?", (12.0, high_id))
    conn.execute("UPDATE backtest_results SET total_return = ? WHERE id = ?", (3.0, middle_id))
    conn.commit()
    conn.close()

    response = client.get("/backtest/results?sort_by=return&sort_dir=desc&limit=1")

    assert response.status_code == 200
    assert [item["id"] for item in response.json()] == [high_id]


def test_list_backtest_results_sorts_created_before_pagination(monkeypatch, tmp_path) -> None:
    client, db = _client_with_temp_db(monkeypatch, tmp_path)
    older_id = _insert_backtest_result(db)
    newer_id = _insert_backtest_result(db)
    conn = db.get_connection()
    conn.execute(
        "UPDATE backtest_results SET created_at = ? WHERE id = ?",
        ("2026-01-01 00:00:00", older_id),
    )
    conn.execute(
        "UPDATE backtest_results SET created_at = ? WHERE id = ?",
        ("2026-01-02 00:00:00", newer_id),
    )
    conn.commit()
    conn.close()

    response = client.get("/backtest/results?sort_by=created&sort_dir=asc&limit=1")

    assert response.status_code == 200
    assert [item["id"] for item in response.json()] == [older_id]


def test_list_backtest_results_sorts_drawdown_before_pagination(monkeypatch, tmp_path) -> None:
    client, db = _client_with_temp_db(monkeypatch, tmp_path)
    low_drawdown_id = _insert_backtest_result(db)
    high_drawdown_id = _insert_backtest_result(db)
    middle_drawdown_id = _insert_backtest_result(db)
    conn = db.get_connection()
    conn.execute("UPDATE backtest_results SET max_drawdown = ? WHERE id = ?", (1.5, low_drawdown_id))
    conn.execute("UPDATE backtest_results SET max_drawdown = ? WHERE id = ?", (18.0, high_drawdown_id))
    conn.execute("UPDATE backtest_results SET max_drawdown = ? WHERE id = ?", (6.0, middle_drawdown_id))
    conn.commit()
    conn.close()

    response = client.get("/backtest/results?sort_by=drawdown&sort_dir=asc&limit=1")

    assert response.status_code == 200
    assert [item["id"] for item in response.json()] == [low_drawdown_id]


def test_list_backtest_results_sorts_win_rate_before_pagination(monkeypatch, tmp_path) -> None:
    client, db = _client_with_temp_db(monkeypatch, tmp_path)
    low_win_id = _insert_backtest_result(db)
    high_win_id = _insert_backtest_result(db)
    middle_win_id = _insert_backtest_result(db)
    conn = db.get_connection()
    conn.execute("UPDATE backtest_results SET win_rate = ? WHERE id = ?", (35.0, low_win_id))
    conn.execute("UPDATE backtest_results SET win_rate = ? WHERE id = ?", (80.0, high_win_id))
    conn.execute("UPDATE backtest_results SET win_rate = ? WHERE id = ?", (52.0, middle_win_id))
    conn.commit()
    conn.close()

    response = client.get("/backtest/results?sort_by=win_rate&sort_dir=desc&limit=1")

    assert response.status_code == 200
    assert [item["id"] for item in response.json()] == [high_win_id]


def test_list_backtest_results_supports_fuzzy_search_before_pagination(monkeypatch, tmp_path) -> None:
    client, db = _client_with_temp_db(monkeypatch, tmp_path)
    _insert_backtest_result(
        db,
        strategy_id=10,
        strategy_name="[合约][1H][CTA] ETH · Heikin Ashi趋势跟踪低频版 · 100U",
        symbols='["ETH/USDT:USDT"]',
        timeframe="1h",
    )
    doge_id = _insert_backtest_result(
        db,
        strategy_id=11,
        strategy_name="[合约][1M][马丁] DOGE · ATR马丁网格 · 100U",
        symbols='["DOGE/USDT:USDT"]',
        timeframe="1m",
    )

    response = client.get("/backtest/results?q=doge%201m&limit=1")

    assert response.status_code == 200
    assert [item["id"] for item in response.json()] == [doge_id]


def test_list_backtest_results_can_skip_matrix_summary(monkeypatch, tmp_path) -> None:
    client, db = _client_with_temp_db(monkeypatch, tmp_path)
    result_id = _insert_backtest_result(db)
    conn = db.get_connection()
    conn.execute(
        "UPDATE backtest_results SET matrix_results_json = ? WHERE id = ?",
        (
            '[{"timeframe":"5m","total_return":1.0,"equity_curve":[1,2,3],"trades":[{"x":1}]}]',
            result_id,
        ),
    )
    conn.commit()
    conn.close()

    response = client.get("/backtest/results?include_matrix_summary=false")

    assert response.status_code == 200
    assert response.json()[0]["matrix_results"] == []


def test_delete_backtest_result_removes_only_history(monkeypatch, tmp_path) -> None:
    client, db = _client_with_temp_db(monkeypatch, tmp_path)
    result_id = _insert_backtest_result(db)

    response = client.delete(f"/backtest/result/{result_id}")

    assert response.status_code == 200
    assert response.json() == {"deleted": True, "id": result_id}
    conn = db.get_connection()
    count = conn.execute("SELECT COUNT(*) FROM backtest_results WHERE id = ?", (result_id,)).fetchone()[0]
    conn.close()
    assert count == 0


def test_delete_backtest_result_returns_404_for_missing_row(monkeypatch, tmp_path) -> None:
    client, _db = _client_with_temp_db(monkeypatch, tmp_path)

    response = client.delete("/backtest/result/999")

    assert response.status_code == 404


def test_cancel_running_backtest_job_marks_cancelling(monkeypatch, tmp_path) -> None:
    client, db = _client_with_temp_db(monkeypatch, tmp_path)
    _insert_backtest_job(db, "job-running", "running")

    response = client.post("/backtest/job/job-running/cancel")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "cancelling"
    assert body["current_bar"] == 12
    assert body["total_bars"] == 100
    assert backtest._is_backtest_cancel_requested("job-running")


def test_cancel_completed_backtest_job_is_noop(monkeypatch, tmp_path) -> None:
    client, db = _client_with_temp_db(monkeypatch, tmp_path)
    _insert_backtest_job(db, "job-completed", "completed")

    response = client.post("/backtest/job/job-completed/cancel")

    assert response.status_code == 200
    assert response.json()["status"] == "completed"
    assert not backtest._is_backtest_cancel_requested("job-completed")


def test_cancelled_worker_does_not_write_backtest_history(monkeypatch, tmp_path) -> None:
    _client, db = _client_with_temp_db(monkeypatch, tmp_path)
    _insert_backtest_job(db, "job-cancelled", "running")

    class DummyStrategy:
        pass

    monkeypatch.setattr(
        backtest,
        "get_strategy_for_id",
        lambda _strategy_id: {
            "name": "[现货] 测试策略",
            "strategy_class": DummyStrategy,
            "db_config": {"timeframe": "1m", "symbols": ["BTC/USDT"]},
        },
    )

    def fake_run_strategy(**_kwargs):
        raise backtest.BacktestCancelled("用户已停止回测")

    monkeypatch.setattr(backtest.backtrader_engine, "run_strategy", fake_run_strategy)

    backtest._run_backtest_job_worker(
        "job-cancelled",
        {
            "strategy_id": 1,
            "exchange": "okx",
            "start_date": "2026-01-01",
            "end_date": "2026-02-01",
            "initial_capital": 10000,
        },
    )

    conn = db.get_connection()
    status = conn.execute("SELECT status FROM backtest_jobs WHERE job_id = ?", ("job-cancelled",)).fetchone()[0]
    history_count = conn.execute("SELECT COUNT(*) FROM backtest_results").fetchone()[0]
    conn.close()
    assert status == "cancelled"
    assert history_count == 0


def test_matrix_backtest_job_runs_each_requested_timeframe(monkeypatch, tmp_path) -> None:
    client, db = _client_with_temp_db(monkeypatch, tmp_path)
    _insert_backtest_job(db, "job-matrix", "running")

    class DummyStrategy:
        pass

    monkeypatch.setattr(
        backtest,
        "get_strategy_for_id",
        lambda _strategy_id: {
            "name": "[现货] 测试策略",
            "strategy_class": DummyStrategy,
            "db_config": {"timeframe": "1m", "symbols": ["BTC/USDT"]},
        },
    )
    seen: list[tuple[str, str]] = []

    def fake_run_strategy(**kwargs):
        seen.append((kwargs["timeframe"], kwargs["strategy_config"]["timeframe"]))
        return backtest.BacktestReport(
            status="completed",
            initial_capital=10000,
            final_capital=10100,
            total_return_pct=1.0 if kwargs["timeframe"] == "5m" else 2.0,
            annual_return_pct=12.0,
            max_drawdown_pct=6.0,
            sortino_ratio=1.7,
            calmar_ratio=2.4,
            total_fees=3.5,
            avg_holding_bars=4.0,
            total_bars=10,
            elapsed_seconds=0.1,
            equity_curve=[
                {"timestamp": 1760000000000, "equity": 10000},
                {"timestamp": 1760000600000, "equity": 10100},
            ],
        )

    monkeypatch.setattr(backtest.backtrader_engine, "run_strategy", fake_run_strategy)

    backtest._run_backtest_job_worker(
        "job-matrix",
        {
            "strategy_id": 1,
            "exchange": "okx",
            "timeframe_mode": "matrix",
            "timeframes": ["5M", "15m", "5m"],
            "start_date": "2026-01-01",
            "end_date": "2026-02-01",
            "initial_capital": 10000,
        },
    )

    conn = db.get_connection()
    row = conn.execute(
        "SELECT status, result_json FROM backtest_jobs WHERE job_id = ?",
        ("job-matrix",),
    ).fetchone()
    conn.close()
    result = backtest.json.loads(row["result_json"])

    assert row["status"] == "completed"
    assert seen == [("5m", "5m"), ("15m", "15m")]
    assert result["timeframe_mode"] == "matrix"
    assert [item["timeframe"] for item in result["matrix_results"]] == ["5m", "15m"]
    assert result["timeframe"] == "15m"
    assert result["total_return"] == 2.0

    history_response = client.get("/backtest/results")
    assert history_response.status_code == 200
    history_item = history_response.json()[0]
    assert history_item["timeframe"] == "15m"
    assert history_item["timeframe_mode"] == "matrix"
    assert [item["timeframe"] for item in history_item["matrix_results"]] == ["5m", "15m"]
    assert all("equity_curve" not in item for item in history_item["matrix_results"])
    assert all("trades" not in item for item in history_item["matrix_results"])

    detail_response = client.get(f"/backtest/result/{history_item['id']}")
    assert detail_response.status_code == 200
    detail = detail_response.json()
    assert detail["timeframe"] == "15m"
    assert detail["timeframe_mode"] == "matrix"
    assert detail["sortino_ratio"] == 1.7
    assert detail["calmar_ratio"] == 2.4
    assert detail["total_fees"] == 3.5
    assert detail["avg_holding_bars"] == 4.0
    assert detail["equity_curve"][0]["equity"] == 10000
    assert [item["timeframe"] for item in detail["matrix_results"]] == ["5m", "15m"]
    assert "equity_curve" in detail["matrix_results"][0]


def test_resume_interrupted_backtest_job_requeues_saved_request(monkeypatch, tmp_path) -> None:
    client, db = _client_with_temp_db(monkeypatch, tmp_path)
    _insert_backtest_job(db, "job-interrupted", "interrupted")
    captured: dict[str, object] = {}

    async def fake_run_task(job_id: str, payload: dict) -> None:
        captured["job_id"] = job_id
        captured["payload"] = payload
        backtest._update_backtest_job(job_id, status="running", message="fake resumed")
        backtest._clear_backtest_active(job_id)

    monkeypatch.setattr(backtest, "_run_backtest_job_task", fake_run_task)

    response = client.post("/backtest/job/job-interrupted/resume")

    assert response.status_code == 200
    body = response.json()
    assert body["job_id"] == "job-interrupted"
    assert body["status"] == "pending"
    assert body["current_bar"] == 0
    assert body["total_bars"] == 0
    assert captured["job_id"] == "job-interrupted"
    assert captured["payload"] == {
        "strategy_id": 1,
        "exchange": "okx",
        "symbol": None,
        "timeframe": None,
        "timeframe_mode": "strategy",
        "timeframes": None,
        "start_date": "2026-01-01",
        "end_date": "2026-02-01",
        "initial_capital": 10000.0,
        "commission": None,
        "slippage": None,
        "maker_fee_bps": None,
        "taker_fee_bps": None,
        "slippage_bps": None,
        "stop_loss": None,
        "take_profit": None,
        "trailing_stop": None,
    }


def test_resume_completed_backtest_job_is_rejected(monkeypatch, tmp_path) -> None:
    client, db = _client_with_temp_db(monkeypatch, tmp_path)
    _insert_backtest_job(db, "job-completed", "completed")

    response = client.post("/backtest/job/job-completed/resume")

    assert response.status_code == 409
    assert "已完成" in response.json()["detail"]


def test_list_backtest_jobs_exposes_request_for_resume_matching(monkeypatch, tmp_path) -> None:
    client, db = _client_with_temp_db(monkeypatch, tmp_path)
    _insert_backtest_job(db, "job-interrupted", "interrupted")
    conn = db.get_connection()
    conn.execute(
        "UPDATE backtest_jobs SET result_json = ? WHERE job_id = ?",
        ('{"large":"payload"}', "job-interrupted"),
    )
    conn.commit()
    conn.close()

    response = client.get("/backtest/jobs?status=interrupted&limit=5")

    assert response.status_code == 200
    body = response.json()
    assert body[0]["job_id"] == "job-interrupted"
    assert body[0]["status"] == "interrupted"
    assert body[0]["resumable"] is True
    assert body[0]["request"]["strategy_id"] == 1
    assert body[0]["request"]["start_date"] == "2026-01-01"
    assert body[0]["request"]["end_date"] == "2026-02-01"
    assert "result" not in body[0]


def test_list_backtest_jobs_can_include_result_when_requested(monkeypatch, tmp_path) -> None:
    client, db = _client_with_temp_db(monkeypatch, tmp_path)
    _insert_backtest_job(db, "job-completed", "completed")
    conn = db.get_connection()
    conn.execute(
        "UPDATE backtest_jobs SET result_json = ? WHERE job_id = ?",
        ('{"total_return":1.23}', "job-completed"),
    )
    conn.commit()
    conn.close()

    response = client.get("/backtest/jobs?status=completed&limit=5&include_result=true")

    assert response.status_code == 200
    body = response.json()
    assert body[0]["job_id"] == "job-completed"
    assert body[0]["result"]["total_return"] == 1.23


def test_backtest_date_validation_rejects_future_end_date() -> None:
    with pytest.raises(HTTPException) as exc:
        backtest._validate_backtest_date_range(
            "2026-05-01",
            "2026-05-09",
            today=date(2026, 5, 8),
        )

    assert exc.value.status_code == 400
    assert exc.value.detail == "回测结束日期不能晚于当前日期 2026-05-08"


def test_backtest_date_validation_rejects_start_after_end() -> None:
    with pytest.raises(HTTPException) as exc:
        backtest._validate_backtest_date_range(
            "2026-05-09",
            "2026-05-08",
            today=date(2026, 5, 8),
        )

    assert exc.value.status_code == 400
    assert exc.value.detail == "回测开始日期不能晚于结束日期"
