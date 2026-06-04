"""Shared MCP schemas and permission constants."""
from __future__ import annotations

import os
from typing import Final


DEFAULT_API_BASE: Final[str] = "http://127.0.0.1:8889/api/v2"
LIVE_CONFIRMATION: Final[str] = "I_UNDERSTAND_REAL_TRADING_RISK"

READ_TOOLS: Final[tuple[str, ...]] = (
    "quantbase_capabilities",
    "quantbase_health",
    "market_symbols",
    "market_klines",
    "market_indicators",
    "sync_config",
    "sync_status",
    "sync_jobs",
    "sync_table_stats",
    "strategy_search",
    "strategy_get",
    "agent_get_task",
    "agent_get_iterations",
    "optimizer_get_run",
    "backtest_get_job",
    "backtest_list_results",
    "backtest_get_result",
    "paper_dashboard",
    "paper_events",
    "paper_equity_curve",
    "trading_balance",
    "trading_positions",
    "trading_open_orders",
)

RESEARCH_MUTATION_TOOLS: Final[tuple[str, ...]] = (
    "sync_start_history",
    "sync_one",
    "strategy_create",
    "strategy_generate",
    "strategy_validate_code",
    "agent_create_task",
    "agent_accept_iteration",
    "optimizer_run_now",
    "backtest_start_job",
    "backtest_cancel_job",
    "backtest_resume_job",
    "paper_configure",
    "paper_start",
    "paper_pause",
    "paper_resume",
    "paper_stop",
)

LIVE_MUTATION_TOOLS: Final[tuple[str, ...]] = (
    "live_promote",
    "trading_spot_order",
    "trading_futures_order",
    "trading_cancel_order",
    "trading_transfer",
)


def live_trading_enabled() -> bool:
    return os.getenv("QUANTBASE_MCP_ENABLE_LIVE_TRADING", "").strip() == "1"
