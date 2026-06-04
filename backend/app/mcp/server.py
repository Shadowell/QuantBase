"""Local stdio MCP server for QuantBase."""
from __future__ import annotations

import json
from typing import Any, Sequence

from mcp.server.fastmcp import FastMCP

from app.mcp.client import QuantBaseMcpClient
from app.mcp import tools


def create_server(client: QuantBaseMcpClient | None = None) -> FastMCP:
    api = client or QuantBaseMcpClient()
    server = FastMCP(
        "quantbase",
        instructions=(
            "QuantBase strategy research MCP. Use real market data only. "
            "Live mutation tools require QUANTBASE_MCP_ENABLE_LIVE_TRADING=1 plus explicit confirmation."
        ),
    )

    @server.resource("quantbase://capabilities")
    def capabilities_resource() -> str:
        return json.dumps(tools.quantbase_capabilities(), ensure_ascii=False, indent=2)

    @server.tool()
    def quantbase_capabilities() -> dict[str, Any]:
        return tools.quantbase_capabilities()

    @server.tool()
    def quantbase_health() -> Any:
        return tools.quantbase_health(api)

    @server.tool()
    def market_symbols(exchange: str = "okx", quote: str = "USDT", market_type: str = "spot") -> Any:
        return tools.market_symbols(api, exchange=exchange, quote=quote, market_type=market_type)

    @server.tool()
    def market_klines(
        symbol: str,
        exchange: str = "okx",
        timeframe: str = "1h",
        limit: int = 500,
        start: int | None = None,
        end: int | None = None,
    ) -> Any:
        return tools.market_klines(
            api,
            exchange=exchange,
            symbol=symbol,
            timeframe=timeframe,
            limit=limit,
            start=start,
            end=end,
        )

    @server.tool()
    def market_indicators(
        symbol: str,
        exchange: str = "okx",
        timeframe: str = "1h",
        limit: int = 500,
        start: int | None = None,
        end: int | None = None,
        ema_periods: Sequence[int] | None = None,
    ) -> Any:
        return tools.market_indicators(
            api,
            exchange=exchange,
            symbol=symbol,
            timeframe=timeframe,
            limit=limit,
            start=start,
            end=end,
            ema_periods=ema_periods,
        )

    @server.tool()
    def sync_config() -> Any:
        return tools.sync_config(api)

    @server.tool()
    def sync_status() -> Any:
        return tools.sync_status(api)

    @server.tool()
    def sync_jobs(limit: int = 20, include_items: bool = True) -> Any:
        return tools.sync_jobs(api, limit=limit, include_items=include_items)

    @server.tool()
    def sync_table_stats() -> Any:
        return tools.sync_table_stats(api)

    @server.tool()
    def sync_start_history(
        symbols: Sequence[str],
        timeframes: Sequence[str],
        history_days: int = 365,
        exchange: str = "okx",
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> Any:
        return tools.sync_start_history(
            api,
            symbols=symbols,
            timeframes=timeframes,
            history_days=history_days,
            exchange=exchange,
            start_date=start_date,
            end_date=end_date,
        )

    @server.tool()
    def sync_one(
        symbol: str,
        timeframe: str,
        history_days: int = 365,
        exchange: str = "okx",
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> Any:
        return tools.sync_one(
            api,
            symbol=symbol,
            timeframe=timeframe,
            history_days=history_days,
            exchange=exchange,
            start_date=start_date,
            end_date=end_date,
        )

    @server.tool()
    def strategy_search(
        search: str = "",
        page: int = 1,
        per_page: int = 18,
        status: str = "all",
        asset_class: str = "all",
        strategy_type: str = "all",
        timeframe: str = "all",
        capital: str = "all",
    ) -> Any:
        return tools.strategy_search(
            api,
            search=search,
            page=page,
            per_page=per_page,
            status=status,
            asset_class=asset_class,
            strategy_type=strategy_type,
            timeframe=timeframe,
            capital=capital,
        )

    @server.tool()
    def strategy_get(strategy_id: int) -> Any:
        return tools.strategy_get(api, strategy_id=strategy_id)

    @server.tool()
    def strategy_create(
        name: str,
        script_content: str,
        description: str | None = None,
        config: dict[str, Any] | None = None,
        exchange: str = "okx",
        symbols: Sequence[str] | None = None,
    ) -> Any:
        return tools.strategy_create(
            api,
            name=name,
            script_content=script_content,
            description=description,
            config=config,
            exchange=exchange,
            symbols=symbols,
        )

    @server.tool()
    def strategy_generate(prompt: str, symbol: str = "BTC/USDT", timeframe: str = "1h") -> Any:
        return tools.strategy_generate(api, prompt=prompt, symbol=symbol, timeframe=timeframe)

    @server.tool()
    def strategy_validate_code(
        code: str,
        symbols: Sequence[str] | None = None,
        market_type: str = "spot",
        timeframe: str = "1m",
        smoke: bool = False,
    ) -> Any:
        return tools.strategy_validate_code(
            code=code,
            symbols=symbols,
            market_type=market_type,
            timeframe=timeframe,
            smoke=smoke,
        )

    @server.tool()
    def agent_create_task(payload: dict[str, Any]) -> Any:
        return tools.agent_create_task(api, payload=payload)

    @server.tool()
    def agent_get_task(task_id: str) -> Any:
        return tools.agent_get_task(api, task_id=task_id)

    @server.tool()
    def agent_get_iterations(task_id: str) -> Any:
        return tools.agent_get_iterations(api, task_id=task_id)

    @server.tool()
    def agent_accept_iteration(task_id: str, iteration: int, allow_low_quality: bool = False) -> Any:
        return tools.agent_accept_iteration(
            api,
            task_id=task_id,
            iteration=iteration,
            allow_low_quality=allow_low_quality,
        )

    @server.tool()
    def optimizer_run_now(llm_model: str | None = None) -> Any:
        return tools.optimizer_run_now(api, llm_model=llm_model)

    @server.tool()
    def optimizer_get_run(run_id: str) -> Any:
        return tools.optimizer_get_run(api, run_id=run_id)

    @server.tool()
    def backtest_start_job(
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
        return tools.backtest_start_job(
            api,
            strategy_id=strategy_id,
            start_date=start_date,
            end_date=end_date,
            initial_capital=initial_capital,
            exchange=exchange,
            symbol=symbol,
            timeframe=timeframe,
            timeframe_mode=timeframe_mode,
            timeframes=timeframes,
            maker_fee_bps=maker_fee_bps,
            taker_fee_bps=taker_fee_bps,
            slippage_bps=slippage_bps,
        )

    @server.tool()
    def backtest_get_job(job_id: str) -> Any:
        return tools.backtest_get_job(api, job_id=job_id)

    @server.tool()
    def backtest_cancel_job(job_id: str) -> Any:
        return tools.backtest_cancel_job(api, job_id=job_id)

    @server.tool()
    def backtest_resume_job(job_id: str) -> Any:
        return tools.backtest_resume_job(api, job_id=job_id)

    @server.tool()
    def backtest_list_results(
        query: str = "",
        strategy_id: int | None = None,
        limit: int = 20,
        offset: int = 0,
        sort_by: str = "created",
        sort_dir: str = "desc",
    ) -> Any:
        return tools.backtest_list_results(
            api,
            query=query,
            strategy_id=strategy_id,
            limit=limit,
            offset=offset,
            sort_by=sort_by,
            sort_dir=sort_dir,
        )

    @server.tool()
    def backtest_get_result(backtest_id: int) -> Any:
        return tools.backtest_get_result(api, backtest_id=backtest_id)

    @server.tool()
    def paper_configure(
        strategy_id: int,
        initial_equity: float = 10000.0,
        exchange: str = "okx",
        loop_interval_sec: int = 60,
    ) -> Any:
        return tools.paper_configure(
            api,
            strategy_id=strategy_id,
            initial_equity=initial_equity,
            exchange=exchange,
            loop_interval_sec=loop_interval_sec,
        )

    @server.tool()
    def paper_start(strategy_id: int) -> Any:
        return tools.paper_start(api, strategy_id=strategy_id)

    @server.tool()
    def paper_pause(strategy_id: int) -> Any:
        return tools.paper_pause(api, strategy_id=strategy_id)

    @server.tool()
    def paper_resume(strategy_id: int) -> Any:
        return tools.paper_resume(api, strategy_id=strategy_id)

    @server.tool()
    def paper_stop(strategy_id: int, clear_metrics: bool = False) -> Any:
        return tools.paper_stop(api, strategy_id=strategy_id, clear_metrics=clear_metrics)

    @server.tool()
    def paper_dashboard(strategy_id: int | None = None) -> Any:
        return tools.paper_dashboard(api, strategy_id=strategy_id)

    @server.tool()
    def paper_events(strategy_id: int | None = None, limit: int = 50) -> Any:
        return tools.paper_events(api, strategy_id=strategy_id, limit=limit)

    @server.tool()
    def paper_equity_curve(strategy_id: int | None = None) -> Any:
        return tools.paper_equity_curve(api, strategy_id=strategy_id)

    @server.tool()
    def live_preflight(payload: dict[str, Any]) -> Any:
        return tools.live_preflight(api, payload=payload)

    @server.tool()
    def live_promote(
        payload: dict[str, Any],
        confirm_live_risk: bool,
        confirmation: str,
        reason: str,
        idempotency_key: str,
    ) -> Any:
        return tools.live_promote(
            api,
            payload=payload,
            confirm_live_risk=confirm_live_risk,
            confirmation=confirmation,
            reason=reason,
            idempotency_key=idempotency_key,
        )

    @server.tool()
    def trading_balance(exchange: str = "okx") -> Any:
        return tools.trading_balance(api, exchange=exchange)

    @server.tool()
    def trading_positions(exchange: str = "okx", symbol: str | None = None) -> Any:
        return tools.trading_positions(api, exchange=exchange, symbol=symbol)

    @server.tool()
    def trading_open_orders(exchange: str = "okx", symbol: str | None = None) -> Any:
        return tools.trading_open_orders(api, exchange=exchange, symbol=symbol)

    @server.tool()
    def trading_spot_order(
        symbol: str,
        side: str,
        amount: float,
        order_type: str = "market",
        price: float | None = None,
        exchange: str = "okx",
        confirm_live_risk: bool = False,
        confirmation: str = "",
        reason: str = "",
        idempotency_key: str = "",
    ) -> Any:
        return tools.trading_spot_order(
            api,
            symbol=symbol,
            side=side,
            amount=amount,
            order_type=order_type,
            price=price,
            exchange=exchange,
            confirm_live_risk=confirm_live_risk,
            confirmation=confirmation,
            reason=reason,
            idempotency_key=idempotency_key,
        )

    @server.tool()
    def trading_futures_order(
        symbol: str,
        side: str,
        action: str,
        amount: float,
        leverage: int = 1,
        price: float | None = None,
        exchange: str = "okx",
        confirm_live_risk: bool = False,
        confirmation: str = "",
        reason: str = "",
        idempotency_key: str = "",
    ) -> Any:
        return tools.trading_futures_order(
            api,
            symbol=symbol,
            side=side,
            action=action,
            amount=amount,
            leverage=leverage,
            price=price,
            exchange=exchange,
            confirm_live_risk=confirm_live_risk,
            confirmation=confirmation,
            reason=reason,
            idempotency_key=idempotency_key,
        )

    @server.tool()
    def trading_cancel_order(
        order_id: str,
        symbol: str,
        exchange: str = "okx",
        confirm_live_risk: bool = False,
        confirmation: str = "",
        reason: str = "",
        idempotency_key: str = "",
    ) -> Any:
        return tools.trading_cancel_order(
            api,
            order_id=order_id,
            symbol=symbol,
            exchange=exchange,
            confirm_live_risk=confirm_live_risk,
            confirmation=confirmation,
            reason=reason,
            idempotency_key=idempotency_key,
        )

    @server.tool()
    def trading_transfer(
        currency: str,
        amount: float,
        from_account: str,
        to_account: str,
        exchange: str = "okx",
        confirm_live_risk: bool = False,
        confirmation: str = "",
        reason: str = "",
        idempotency_key: str = "",
    ) -> Any:
        return tools.trading_transfer(
            api,
            currency=currency,
            amount=amount,
            from_account=from_account,
            to_account=to_account,
            exchange=exchange,
            confirm_live_risk=confirm_live_risk,
            confirmation=confirmation,
            reason=reason,
            idempotency_key=idempotency_key,
        )

    return server


def run_stdio() -> None:
    create_server().run("stdio")
