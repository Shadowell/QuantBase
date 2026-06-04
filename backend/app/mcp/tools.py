"""Pure tool functions for the QuantBase MCP server."""
from __future__ import annotations

from typing import Any, Sequence

from app.mcp.schemas import (
    LIVE_CONFIRMATION,
    LIVE_MUTATION_TOOLS,
    READ_TOOLS,
    RESEARCH_MUTATION_TOOLS,
    live_trading_enabled,
)


class LiveTradingDisabledError(PermissionError):
    """Live mutation tools are disabled by environment policy."""


class LiveConfirmationError(PermissionError):
    """Live mutation tools require explicit real-risk confirmation."""


def _compact(values: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in values.items() if value is not None}


def _require_live_confirmation(
    *,
    confirm_live_risk: bool,
    confirmation: str,
    reason: str,
    idempotency_key: str,
) -> dict[str, str]:
    if not live_trading_enabled():
        raise LiveTradingDisabledError(
            "Live trading MCP tools are disabled. Set QUANTBASE_MCP_ENABLE_LIVE_TRADING=1 to enable."
        )
    if not confirm_live_risk or confirmation != LIVE_CONFIRMATION:
        raise LiveConfirmationError(
            f"Live mutation requires confirm_live_risk=true and confirmation={LIVE_CONFIRMATION!r}."
        )
    reason = str(reason or "").strip()
    idempotency_key = str(idempotency_key or "").strip()
    if not reason or not idempotency_key:
        raise LiveConfirmationError("Live mutation requires non-empty reason and idempotency_key.")
    return {"reason": reason, "idempotency_key": idempotency_key}


def quantbase_capabilities() -> dict[str, Any]:
    return {
        "api_base_env": "QUANTBASE_MCP_API_BASE",
        "audit_path_env": "QUANTBASE_MCP_AUDIT_PATH",
        "live_trading_enabled": live_trading_enabled(),
        "live_confirmation": LIVE_CONFIRMATION,
        "tool_groups": {
            "read": list(READ_TOOLS),
            "research_backtest_paper_mutation": list(RESEARCH_MUTATION_TOOLS),
            "live_mutation": list(LIVE_MUTATION_TOOLS),
        },
        "data_policy": "real_market_data_only_no_mock_or_synthetic_ohlcv",
    }


def quantbase_health(client: Any) -> Any:
    return client.request("GET", "/system/health", tool_name="quantbase_health")


def market_symbols(client: Any, *, exchange: str = "okx", quote: str = "USDT", market_type: str = "spot") -> Any:
    return client.request(
        "GET",
        "/market/symbols",
        params={"exchange": exchange, "quote": quote, "market_type": market_type},
        tool_name="market_symbols",
    )


def market_klines(
    client: Any,
    *,
    exchange: str = "okx",
    symbol: str,
    timeframe: str = "1h",
    limit: int = 500,
    start: int | None = None,
    end: int | None = None,
) -> Any:
    return client.request(
        "GET",
        "/market/klines",
        params=_compact({"exchange": exchange, "symbol": symbol, "timeframe": timeframe, "limit": limit, "start": start, "end": end}),
        tool_name="market_klines",
    )


def market_indicators(
    client: Any,
    *,
    exchange: str = "okx",
    symbol: str,
    timeframe: str = "1h",
    limit: int = 500,
    start: int | None = None,
    end: int | None = None,
    ema_periods: Sequence[int] | None = None,
) -> Any:
    periods = ",".join(str(int(p)) for p in (ema_periods or [5, 10, 20, 30]))
    return client.request(
        "GET",
        "/market/indicators",
        params=_compact({"exchange": exchange, "symbol": symbol, "timeframe": timeframe, "limit": limit, "start": start, "end": end, "ema_periods": periods}),
        tool_name="market_indicators",
    )


def sync_config(client: Any) -> Any:
    return client.request("GET", "/sync/config", tool_name="sync_config")


def sync_status(client: Any) -> Any:
    return client.request("GET", "/sync/status", tool_name="sync_status")


def sync_jobs(client: Any, *, limit: int = 20, include_items: bool = True) -> Any:
    return client.request(
        "GET",
        "/sync/jobs",
        params={"limit": limit, "include_items": include_items},
        tool_name="sync_jobs",
    )


def sync_table_stats(client: Any) -> Any:
    return client.request("GET", "/sync/table-stats", tool_name="sync_table_stats")


def sync_start_history(
    client: Any,
    *,
    symbols: Sequence[str],
    timeframes: Sequence[str],
    history_days: int = 365,
    exchange: str = "okx",
    start_date: str | None = None,
    end_date: str | None = None,
) -> Any:
    return client.request(
        "POST",
        "/sync/start",
        json=_compact({
            "exchange": exchange,
            "symbols": list(symbols),
            "timeframes": list(timeframes),
            "history_days": history_days,
            "start_date": start_date,
            "end_date": end_date,
        }),
        tool_name="sync_start_history",
    )


def sync_one(
    client: Any,
    *,
    symbol: str,
    timeframe: str,
    history_days: int = 365,
    exchange: str = "okx",
    start_date: str | None = None,
    end_date: str | None = None,
) -> Any:
    return client.request(
        "POST",
        "/sync/sync-one",
        json=_compact({
            "exchange": exchange,
            "symbol": symbol,
            "timeframe": timeframe,
            "history_days": history_days,
            "start_date": start_date,
            "end_date": end_date,
        }),
        tool_name="sync_one",
    )


def strategy_search(
    client: Any,
    *,
    search: str = "",
    page: int = 1,
    per_page: int = 18,
    status: str = "all",
    asset_class: str = "all",
    strategy_type: str = "all",
    timeframe: str = "all",
    capital: str = "all",
) -> Any:
    return client.request(
        "GET",
        "/strategies",
        params={
            "page": page,
            "per_page": per_page,
            "search": search,
            "status": status,
            "asset_class": asset_class,
            "strategy_type": strategy_type,
            "timeframe": timeframe,
            "capital": capital,
        },
        tool_name="strategy_search",
    )


def strategy_get(client: Any, *, strategy_id: int) -> Any:
    return client.request("GET", f"/strategies/{int(strategy_id)}", tool_name="strategy_get")


def strategy_create(
    client: Any,
    *,
    name: str,
    script_content: str,
    description: str | None = None,
    config: dict[str, Any] | None = None,
    exchange: str = "okx",
    symbols: Sequence[str] | None = None,
) -> Any:
    return client.request(
        "POST",
        "/strategies",
        json=_compact({
            "name": name,
            "description": description,
            "script_content": script_content,
            "config": config or {},
            "exchange": exchange,
            "symbols": list(symbols or []),
        }),
        tool_name="strategy_create",
    )


def strategy_generate(client: Any, *, prompt: str, symbol: str = "BTC/USDT", timeframe: str = "1h") -> Any:
    return client.request(
        "POST",
        "/agent/generate_strategy",
        json={"prompt": prompt, "symbol": symbol, "timeframe": timeframe},
        tool_name="strategy_generate",
        timeout=180.0,
    )


def strategy_validate_code(
    *,
    code: str,
    symbols: Sequence[str] | None = None,
    market_type: str = "spot",
    timeframe: str = "1m",
    smoke: bool = False,
) -> dict[str, Any]:
    from app.services.agent.code_sandbox import validate_base_strategy_contract, validate_strategy_runtime_smoke
    import asyncio

    validate_base_strategy_contract(code)
    if smoke:
        asyncio.run(
            validate_strategy_runtime_smoke(
                code,
                symbols=symbols,
                market_type=market_type,
                timeframe=timeframe,
            )
        )
    return {"valid": True, "smoke": bool(smoke)}


def agent_create_task(client: Any, *, payload: dict[str, Any]) -> Any:
    return client.request("POST", "/agent/tasks", json=payload, tool_name="agent_create_task", timeout=180.0)


def agent_get_task(client: Any, *, task_id: str) -> Any:
    return client.request("GET", f"/agent/tasks/{task_id}", tool_name="agent_get_task")


def agent_get_iterations(client: Any, *, task_id: str) -> Any:
    return client.request("GET", f"/agent/tasks/{task_id}/iterations", tool_name="agent_get_iterations")


def agent_accept_iteration(client: Any, *, task_id: str, iteration: int, allow_low_quality: bool = False) -> Any:
    return client.request(
        "POST",
        f"/agent/tasks/{task_id}/iterations/{int(iteration)}/accept",
        params={"allow_low_quality": allow_low_quality},
        tool_name="agent_accept_iteration",
    )


def optimizer_run_now(client: Any, *, llm_model: str | None = None) -> Any:
    return client.request(
        "POST",
        "/agent/strategy-optimizer/run-now",
        json=_compact({"llm_model": llm_model}),
        tool_name="optimizer_run_now",
    )


def optimizer_get_run(client: Any, *, run_id: str) -> Any:
    return client.request("GET", f"/agent/strategy-optimizer/runs/{run_id}", tool_name="optimizer_get_run")


def backtest_start_job(
    client: Any,
    *,
    strategy_id: int,
    start_date: str,
    end_date: str,
    initial_capital: float = 10000.0,
    exchange: str = "okx",
    symbol: str | None = None,
    timeframe: str | None = None,
    timeframe_mode: str = "strategy",
    timeframes: Sequence[str] | None = None,
    maker_fee_bps: float | None = None,
    taker_fee_bps: float | None = None,
    slippage_bps: float | None = None,
) -> Any:
    return client.request(
        "POST",
        "/backtest/run_job",
        json=_compact({
            "strategy_id": int(strategy_id),
            "exchange": exchange,
            "symbol": symbol,
            "timeframe": timeframe,
            "timeframe_mode": timeframe_mode,
            "timeframes": list(timeframes) if timeframes else None,
            "start_date": start_date,
            "end_date": end_date,
            "initial_capital": float(initial_capital),
            "maker_fee_bps": maker_fee_bps,
            "taker_fee_bps": taker_fee_bps,
            "slippage_bps": slippage_bps,
        }),
        tool_name="backtest_start_job",
    )


def backtest_get_job(client: Any, *, job_id: str) -> Any:
    return client.request("GET", f"/backtest/job/{job_id}", tool_name="backtest_get_job")


def backtest_cancel_job(client: Any, *, job_id: str) -> Any:
    return client.request("POST", f"/backtest/job/{job_id}/cancel", tool_name="backtest_cancel_job")


def backtest_resume_job(client: Any, *, job_id: str) -> Any:
    return client.request("POST", f"/backtest/job/{job_id}/resume", tool_name="backtest_resume_job")


def backtest_list_results(
    client: Any,
    *,
    query: str = "",
    strategy_id: int | None = None,
    limit: int = 20,
    offset: int = 0,
    sort_by: str = "created",
    sort_dir: str = "desc",
) -> Any:
    return client.request(
        "GET",
        "/backtest/results",
        params=_compact({"q": query, "strategy_id": strategy_id, "limit": limit, "offset": offset, "sort_by": sort_by, "sort_dir": sort_dir}),
        tool_name="backtest_list_results",
    )


def backtest_get_result(client: Any, *, backtest_id: int) -> Any:
    return client.request("GET", f"/backtest/result/{int(backtest_id)}", tool_name="backtest_get_result")


def paper_configure(
    client: Any,
    *,
    strategy_id: int,
    initial_equity: float = 10000.0,
    exchange: str = "okx",
    loop_interval_sec: int = 60,
) -> Any:
    return client.request(
        "POST",
        "/live/configure",
        json={
            "strategy_type": str(strategy_id),
            "exchange": exchange,
            "initial_equity": float(initial_equity),
            "dry_run": True,
            "loop_interval": int(loop_interval_sec),
        },
        tool_name="paper_configure",
    )


def paper_start(client: Any, *, strategy_id: int) -> Any:
    return client.request("POST", "/live/start", json={"instance_id": int(strategy_id)}, tool_name="paper_start")


def paper_pause(client: Any, *, strategy_id: int) -> Any:
    return client.request("POST", "/live/pause", json={"instance_id": int(strategy_id)}, tool_name="paper_pause")


def paper_resume(client: Any, *, strategy_id: int) -> Any:
    return client.request("POST", "/live/resume", json={"instance_id": int(strategy_id)}, tool_name="paper_resume")


def paper_stop(client: Any, *, strategy_id: int, clear_metrics: bool = False) -> Any:
    return client.request(
        "POST",
        "/live/stop",
        json={"instance_id": int(strategy_id), "clear_metrics": bool(clear_metrics)},
        tool_name="paper_stop",
    )


def paper_dashboard(client: Any, *, strategy_id: int | None = None) -> Any:
    return client.request(
        "GET",
        "/live/dashboard",
        params=_compact({"instance_id": strategy_id}),
        tool_name="paper_dashboard",
    )


def paper_events(client: Any, *, strategy_id: int | None = None, limit: int = 50) -> Any:
    return client.request(
        "GET",
        "/live/events",
        params=_compact({"instance_id": strategy_id, "limit": limit}),
        tool_name="paper_events",
    )


def paper_equity_curve(client: Any, *, strategy_id: int | None = None) -> Any:
    return client.request(
        "GET",
        "/live/equity_curve",
        params=_compact({"instance_id": strategy_id}),
        tool_name="paper_equity_curve",
    )


def live_preflight(client: Any, *, payload: dict[str, Any]) -> Any:
    return client.request("POST", "/live/promote/preflight", json=payload, tool_name="live_preflight")


def live_promote(
    client: Any,
    *,
    payload: dict[str, Any],
    confirm_live_risk: bool,
    confirmation: str,
    reason: str,
    idempotency_key: str,
) -> Any:
    context = _require_live_confirmation(
        confirm_live_risk=confirm_live_risk,
        confirmation=confirmation,
        reason=reason,
        idempotency_key=idempotency_key,
    )
    next_payload = dict(payload)
    next_payload.setdefault("confirm_live_risk", True)
    return client.request(
        "POST",
        "/live/promote",
        json=next_payload,
        tool_name="live_promote",
        audit_context=context,
    )


def trading_balance(client: Any, *, exchange: str = "okx") -> Any:
    return client.request("GET", "/trading/balance", params={"exchange": exchange}, tool_name="trading_balance")


def trading_positions(client: Any, *, exchange: str = "okx", symbol: str | None = None) -> Any:
    return client.request(
        "GET",
        "/trading/positions",
        params=_compact({"exchange": exchange, "symbol": symbol}),
        tool_name="trading_positions",
    )


def trading_open_orders(client: Any, *, exchange: str = "okx", symbol: str | None = None) -> Any:
    return client.request(
        "GET",
        "/trading/orders/open",
        params=_compact({"exchange": exchange, "symbol": symbol}),
        tool_name="trading_open_orders",
    )


def trading_spot_order(
    client: Any,
    *,
    symbol: str,
    side: str,
    amount: float,
    order_type: str = "market",
    price: float | None = None,
    exchange: str = "okx",
    confirm_live_risk: bool,
    confirmation: str,
    reason: str,
    idempotency_key: str,
) -> Any:
    context = _require_live_confirmation(
        confirm_live_risk=confirm_live_risk,
        confirmation=confirmation,
        reason=reason,
        idempotency_key=idempotency_key,
    )
    return client.request(
        "POST",
        "/trading/spot/order",
        json={"exchange": exchange, "symbol": symbol, "side": side, "type": order_type, "amount": float(amount), "price": price},
        tool_name="trading_spot_order",
        audit_context=context,
    )


def trading_futures_order(
    client: Any,
    *,
    symbol: str,
    side: str,
    action: str,
    amount: float,
    leverage: int = 1,
    price: float | None = None,
    exchange: str = "okx",
    confirm_live_risk: bool,
    confirmation: str,
    reason: str,
    idempotency_key: str,
) -> Any:
    context = _require_live_confirmation(
        confirm_live_risk=confirm_live_risk,
        confirmation=confirmation,
        reason=reason,
        idempotency_key=idempotency_key,
    )
    return client.request(
        "POST",
        "/trading/futures/order",
        json={
            "exchange": exchange,
            "symbol": symbol,
            "side": side,
            "action": action,
            "amount": float(amount),
            "leverage": int(leverage),
            "price": price,
        },
        tool_name="trading_futures_order",
        audit_context=context,
    )


def trading_cancel_order(
    client: Any,
    *,
    order_id: str,
    symbol: str,
    exchange: str = "okx",
    confirm_live_risk: bool,
    confirmation: str,
    reason: str,
    idempotency_key: str,
) -> Any:
    context = _require_live_confirmation(
        confirm_live_risk=confirm_live_risk,
        confirmation=confirmation,
        reason=reason,
        idempotency_key=idempotency_key,
    )
    return client.request(
        "DELETE",
        f"/trading/order/{order_id}",
        params={"exchange": exchange, "symbol": symbol},
        tool_name="trading_cancel_order",
        audit_context=context,
    )


def trading_transfer(
    client: Any,
    *,
    currency: str,
    amount: float,
    from_account: str,
    to_account: str,
    exchange: str = "okx",
    confirm_live_risk: bool,
    confirmation: str,
    reason: str,
    idempotency_key: str,
) -> Any:
    context = _require_live_confirmation(
        confirm_live_risk=confirm_live_risk,
        confirmation=confirmation,
        reason=reason,
        idempotency_key=idempotency_key,
    )
    return client.request(
        "POST",
        "/trading/transfer",
        json={
            "exchange": exchange,
            "currency": currency,
            "amount": float(amount),
            "from_account": from_account,
            "to_account": to_account,
        },
        tool_name="trading_transfer",
        audit_context=context,
    )
