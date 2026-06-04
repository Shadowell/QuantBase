"""AI autonomous paper trader for OKX USDT perpetual swaps.

The LLM may suggest actions and pacing, but this strategy always enforces the
operator-configured hard envelope before any simulated order is sent.
"""

from __future__ import annotations

import json
import logging
import asyncio
import re
import time
from collections import deque
from dataclasses import replace
from typing import Any, Deque, Dict, Iterable, List, Optional, Tuple

from app.core.execution.base_strategy import BarData, OrderResult
from app.services.agent.llm_client import (
    describe_qwen_exception,
    get_llm_fallback_model_choices,
    get_qwen_client,
    is_dashscope_free_tier_exhausted,
    validate_llm_model_name,
)
from app.services.agent.ai_strategy_assistant import HermesAgentBridge
from app.services.contract_paper_account import normalize_contract_symbol
from app.strategies.contract_common import ContractStrategyBase

logger = logging.getLogger(__name__)

DEFAULT_SYMBOLS = [
    "BTC/USDT:USDT",
    "ETH/USDT:USDT",
    "SOL/USDT:USDT",
    "DOGE/USDT:USDT",
    "XRP/USDT:USDT",
]

OPEN_ACTIONS = {"open_long", "open_short"}
CLOSE_ACTIONS = {"close_long", "close_short", "close_all"}
VALID_ACTIONS = {"hold", *OPEN_ACTIONS, *CLOSE_ACTIONS}
HERMES_PROVIDER_ALIASES = {"hermes", "grok", "xai", "xai-oauth", "xai_oauth"}
HERMES_DEFAULT_MODEL = "gpt-5.5"
HERMES_DISPLAY_NAME = "Hermes/Codex"
SHORT_ONLY_VALUES = {"short", "short_only", "short-only", "only_short"}


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_fraction(value: Any, default: float) -> float:
    number = _as_float(value, default)
    if number > 1:
        return number / 100.0
    return max(0.0, number)


def _normalize_symbols(value: Any) -> List[str]:
    if isinstance(value, str):
        items = [part.strip() for part in value.split(",") if part.strip()]
    elif isinstance(value, Iterable):
        items = [str(part).strip() for part in value if str(part).strip()]
    else:
        items = []
    out: List[str] = []
    seen = set()
    for item in items or DEFAULT_SYMBOLS:
        symbol = normalize_contract_symbol(item)
        if not symbol or symbol in seen:
            continue
        out.append(symbol)
        seen.add(symbol)
    return out or list(DEFAULT_SYMBOLS)


def _action_side(action: str) -> str:
    return "short" if action.endswith("_short") else "long"


def _normalize_llm_provider(value: Any) -> str:
    normalized = str(value or "").strip().lower().replace("_", "-")
    if normalized in HERMES_PROVIDER_ALIASES:
        return "hermes"
    return "dashscope"


def _normalize_trade_direction(value: Any) -> str:
    normalized = str(value or "long_short").strip().lower().replace("-", "_")
    if normalized in {item.replace("-", "_") for item in SHORT_ONLY_VALUES}:
        return "short_only"
    return "long_short"


def _parse_hermes_json_stdout(stdout: str) -> Dict[str, Any]:
    text = str(stdout or "").strip()
    if not text:
        raise ValueError("Hermes 未返回 stdout")
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.S | re.I)
    if fenced:
        parsed = json.loads(fenced.group(1))
        if isinstance(parsed, dict):
            return parsed

    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        parsed = json.loads(text[start:end + 1])
        if isinstance(parsed, dict):
            return parsed
    raise ValueError("Hermes 输出不是 JSON object")


class AiAutonomousTraderStrategy(ContractStrategyBase):
    """Paper-only AI decision loop with deterministic risk gates."""

    async def on_init(self) -> None:
        await super().on_init()
        if str(self.config.get("market_type", "swap")).lower() != "swap":
            raise ValueError("AI自主交易当前仅支持合约模拟盘")
        if not bool(self.config.get("is_paper_trading", True)):
            raise ValueError("AI自主交易只能运行在模拟盘，禁止实盘启动")

        self.trade_symbols = _normalize_symbols(
            self.config.get("contract_trade_symbols")
            or self.config.get("trade_symbols")
            or self.config.get("symbols")
            or self.symbols()
        )
        self.allowed_symbols = set(self.trade_symbols)
        self._recent_trade_times: Deque[float] = deque(maxlen=1)
        self._apply_runtime_limits()
        self._quota_exhausted_llm_models: set[str] = set()

        self._next_decision_after = 0.0
        self._deciding = False
        self._last_decision: Dict[str, Any] = {}
        self._consecutive_holds = 0

        await self._emit(
            "ai_autonomous_ready",
            "AI自主交易已启动",
            "模拟盘合约策略已启动，AI 决策将被硬性风控上限约束",
            {
                "symbols": self.trade_symbols,
                "llm_model": self.llm_model,
                "limits": self._limits_snapshot(),
            },
        )

    def apply_runtime_config(self, config: Dict[str, Any]) -> None:
        """Apply model and risk-envelope changes without restarting the paper instance."""
        self.config.update(dict(config or {}))
        self.trade_symbols = _normalize_symbols(
            self.config.get("contract_trade_symbols")
            or self.config.get("trade_symbols")
            or self.config.get("symbols")
            or self.trade_symbols
        )
        self.allowed_symbols = set(self.trade_symbols)
        self._apply_runtime_limits()

    def _apply_runtime_limits(self) -> None:
        self.max_leverage_cap = max(1.0, min(20.0, _as_float(self.config.get("max_leverage_cap"), 10.0)))
        self.max_single_position_pct = min(1.0, _as_fraction(self.config.get("max_single_position_pct"), 0.20))
        self.max_total_exposure_pct = min(1.0, _as_fraction(self.config.get("max_total_exposure_pct"), 0.60))
        self.min_decision_interval_sec = max(30.0, _as_float(self.config.get("min_decision_interval_sec"), 30.0))
        configured_max_interval = _as_float(self.config.get("max_decision_interval_sec"), 90.0)
        self.max_decision_interval_sec = max(self.min_decision_interval_sec, configured_max_interval)
        self.max_trades_per_hour = max(0, int(_as_float(self.config.get("max_trades_per_hour"), 20)))
        self.min_order_notional_usdt = max(1.0, _as_float(self.config.get("min_order_notional_usdt"), 10.0))
        self.context_bars = max(5, int(_as_float(self.config.get("context_bars"), 12)))
        self.model_temperature = max(0.0, min(1.0, _as_float(self.config.get("model_temperature"), 0.35)))
        self.activity_bias = str(self.config.get("activity_bias") or "active_paper_research").strip()
        self.llm_provider = _normalize_llm_provider(self.config.get("llm_provider") or self.config.get("ai_provider"))
        self.trade_direction = _normalize_trade_direction(self.config.get("trade_direction"))
        self.allow_long = bool(self.config.get("allow_long", True)) and self.trade_direction != "short_only"
        self.allow_short = bool(self.config.get("allow_short", True))
        self.active_after_holds = max(1, int(_as_float(self.config.get("active_after_holds"), 2)))
        self.probe_size_pct = min(
            self.max_single_position_pct,
            max(0.0, _as_fraction(self.config.get("probe_size_pct"), 0.08)),
        )
        self.default_decision_leverage = max(
            1.0,
            min(self.max_leverage_cap, _as_float(self.config.get("default_decision_leverage"), 2.0)),
        )
        raw_llm_model = str(self.config.get("llm_model") or "").strip()
        if not raw_llm_model and self.llm_provider == "hermes":
            raw_llm_model = HERMES_DEFAULT_MODEL
        self.llm_model = validate_llm_model_name(raw_llm_model) if raw_llm_model else None
        self.operator_prompt = str(self.config.get("operator_prompt") or "").strip()

        next_maxlen = max(1, self.max_trades_per_hour or 1)
        existing = list(getattr(self, "_recent_trade_times", []))[-next_maxlen:]
        self._recent_trade_times = deque(existing, maxlen=next_maxlen)

    def _llm_candidate_models(self) -> List[str]:
        raw_fallbacks = self.config.get("llm_model_fallbacks") or []
        if isinstance(raw_fallbacks, str):
            raw_fallbacks = [part.strip() for part in raw_fallbacks.split(",") if part.strip()]
        configured_fallbacks: List[str] = []
        if isinstance(raw_fallbacks, Iterable):
            for raw in raw_fallbacks:
                try:
                    configured_fallbacks.append(validate_llm_model_name(str(raw)))
                except ValueError:
                    logger.warning("忽略无效 AI 自主交易备用模型名: %s", raw)

        out: List[str] = []
        for model in [
            self.llm_model,
            *configured_fallbacks,
            *get_llm_fallback_model_choices(self.llm_model),
        ]:
            normalized = str(model or "").strip()
            if normalized and normalized not in out:
                out.append(normalized)

        exhausted = getattr(self, "_quota_exhausted_llm_models", set())
        available = [model for model in out if model not in exhausted]
        return available or out

    async def on_bar(self, bar: BarData) -> None:
        symbol = normalize_contract_symbol(bar.symbol)
        if symbol not in self.allowed_symbols:
            return
        if symbol != bar.symbol:
            bar = replace(bar, symbol=symbol)
        bars = self._append_bar(bar)
        if getattr(self.broker, "warmup_mode", False):
            return
        if len(bars) < self.context_bars:
            return

        now = time.monotonic()
        if self._deciding or now < self._next_decision_after:
            return

        self._deciding = True
        try:
            decision = await self._ask_ai_for_decision(bar)
            await self._handle_decision(bar, decision)
        finally:
            self._deciding = False

    async def _ask_ai_for_decision(self, bar: BarData) -> Dict[str, Any]:
        context = self._market_context(bar)
        direction_guard = (
            "当前实例为 short_only 做空模式，只允许 hold/open_short/close_short/close_all；"
            "禁止 open_long，禁止以做多验证假设。"
            if self.trade_direction == "short_only"
            else "当前实例允许 long/short 双向模拟盘交易。"
        )
        messages = [
            {
                "role": "system",
                "content": (
                    "你是 QuantBase 模拟盘 AI 合约交易员。你只能输出 JSON，不能输出解释文本。"
                    "你可以自行决定观察窗口、是否交易、方向、仓位比例、杠杆和下一次检查间隔，"
                    "但所有建议都会被系统硬性风控拦截。禁止建议实盘交易。"
                    f"{direction_guard}"
                    "当前目标是模拟盘研究，不是现金观望最大化；当候选标的出现相对强弱、短线突破、"
                    "回撤反转或波动扩张时，应优先使用小仓位试单来验证假设。"
                    "不要因为缺少完美确认而长期连续 hold。"
                    "字段必须为: risk_policy, decision, next_check_seconds。"
                    "decision.action 只能是 hold/open_long/open_short/close_long/close_short/close_all。"
                    "decision.observation_window 可用中文说明你本轮自行选择的观察窗口或节奏依据。"
                    "size_pct 使用 0-1 小数，建议小仓试单 0.05-0.10；leverage 使用数字，"
                    "建议 1-3 倍，reason 使用中文。"
                ),
            },
            {
                "role": "user",
                "content": json.dumps(context, ensure_ascii=False),
            },
        ]
        if self.llm_provider == "hermes":
            return await self._ask_hermes_for_decision(messages, context)
        attempts: List[Dict[str, str]] = []
        candidates = self._llm_candidate_models()
        for index, model in enumerate(candidates):
            try:
                client = get_qwen_client(model)
                result = await client.chat_json(messages, temperature=self.model_temperature, max_tokens=1200)
                if not isinstance(result, dict):
                    raise ValueError("AI 返回不是 JSON object")
                return result
            except Exception as exc:
                detail = describe_qwen_exception(exc) if isinstance(exc, Exception) else str(exc)
                attempts.append({"llm_model": model, "error": detail})
                if is_dashscope_free_tier_exhausted(exc) or is_dashscope_free_tier_exhausted(detail):
                    if not hasattr(self, "_quota_exhausted_llm_models"):
                        self._quota_exhausted_llm_models = set()
                    self._quota_exhausted_llm_models.add(model)
                    next_model = candidates[index + 1] if index + 1 < len(candidates) else ""
                    await self._emit(
                        "ai_model_quota_exhausted",
                        "大模型免费额度耗尽",
                        f"{model} 免费额度耗尽，{f'切换到 {next_model}' if next_model else '暂无更多候选模型'}",
                        {"llm_model": model, "next_model": next_model or None, "error": detail},
                        level="warning",
                    )
                    continue

            await self._emit(
                "ai_decision_error",
                "AI决策失败",
                f"大模型调用失败，本轮不交易: {detail}",
                {"error": detail, "llm_model": model, "attempts": attempts},
                level="error",
            )
            return {"decision": {"action": "hold", "reason": f"AI决策失败: {detail}"}, "next_check_seconds": self.min_decision_interval_sec}

        detail = "；".join(f"{item['llm_model']}: {item['error']}" for item in attempts)[:800] or "没有可用候选模型"
        await self._emit(
            "ai_decision_error",
            "AI决策失败",
            f"候选模型免费额度均不可用，本轮不交易: {detail}",
            {"error": detail, "attempts": attempts},
            level="error",
        )
        return {
            "decision": {
                "action": "hold",
                "reason": f"候选模型免费额度均不可用: {detail}",
            },
            "next_check_seconds": self.min_decision_interval_sec,
        }

    async def _ask_hermes_for_decision(self, messages: List[Dict[str, str]], context: Dict[str, Any]) -> Dict[str, Any]:
        prompt = "\n\n".join(
            [
                f"你是 QuantBase 服务器 {HERMES_DISPLAY_NAME} 模拟盘交易 Agent。",
                "必须只返回一个 JSON object，不要输出 Markdown、解释或额外文本。",
                "JSON 字段必须为 risk_policy, decision, next_check_seconds。",
                "decision.action 必须遵守上下文 required_json_schema.allowed_actions。",
                "所有交易都是 paper/simulation，禁止实盘或真实账户操作。",
                "Messages:",
                json.dumps(messages, ensure_ascii=False, indent=2),
                "Context:",
                json.dumps(context, ensure_ascii=False, indent=2),
            ]
        )
        bridge = HermesAgentBridge(timeout=int(_as_float(self.config.get("hermes_timeout_sec"), 90)))
        try:
            result = await asyncio.to_thread(bridge.run, prompt)
        except Exception as exc:  # pragma: no cover - defensive bridge boundary
            detail = str(exc)
            await self._emit(
                "ai_decision_error",
                "Hermes决策失败",
                f"{HERMES_DISPLAY_NAME} 调用异常，本轮不交易: {detail}",
                {"error": detail, "llm_provider": "hermes", "llm_model": self.llm_model},
                level="error",
            )
            return {"decision": {"action": "hold", "reason": f"{HERMES_DISPLAY_NAME} 调用异常: {detail}"}, "next_check_seconds": self.min_decision_interval_sec}

        status = str(result.get("status") or "unknown")
        if status != "ok":
            detail = str(result.get("message") or result.get("stderr") or status)
            await self._emit(
                "ai_decision_error",
                "Hermes决策失败",
                f"{HERMES_DISPLAY_NAME} 未返回可用结果，本轮不交易: {detail}",
                {"error": detail, "llm_provider": "hermes", "llm_model": self.llm_model, "hermes": result},
                level="error",
            )
            return {"decision": {"action": "hold", "reason": f"{HERMES_DISPLAY_NAME} 未返回可用结果: {detail}"}, "next_check_seconds": self.min_decision_interval_sec}

        try:
            parsed = _parse_hermes_json_stdout(str(result.get("stdout") or ""))
        except Exception as exc:
            detail = str(exc)
            await self._emit(
                "ai_decision_error",
                "Hermes决策解析失败",
                f"{HERMES_DISPLAY_NAME} 输出无法解析，本轮不交易: {detail}",
                {"error": detail, "llm_provider": "hermes", "llm_model": self.llm_model, "hermes": result},
                level="error",
            )
            return {"decision": {"action": "hold", "reason": f"{HERMES_DISPLAY_NAME} 输出无法解析: {detail}"}, "next_check_seconds": self.min_decision_interval_sec}
        parsed.setdefault("model_provider", "hermes")
        parsed.setdefault("llm_model", self.llm_model or HERMES_DEFAULT_MODEL)
        parsed.setdefault("hermes_status", status)
        return parsed

    def _market_context(self, bar: BarData) -> Dict[str, Any]:
        recent: Dict[str, Any] = {}
        for symbol in self.trade_symbols:
            bars = list(self._bars.get(symbol, []))[-20:]
            if not bars:
                continue
            recent[symbol] = {
                "last_close": bars[-1].close,
                "returns_pct": [
                    round((bars[i].close - bars[i - 1].close) / bars[i - 1].close * 100, 4)
                    for i in range(1, len(bars))
                    if bars[i - 1].close
                ][-10:],
                "last_volume": bars[-1].volume,
            }

        has_position = bool(self._position_snapshots())
        force_trade_review = (
            self.activity_bias == "active_paper_research"
            and not has_position
            and self._consecutive_holds >= self.active_after_holds
        )

        return {
            "mode": "paper_swap_only",
            "market_observation_mode": "ai_decides",
            "llm_provider": self.llm_provider,
            "llm_model": self.llm_model,
            "trade_direction": self.trade_direction,
            "operator_prompt": self.operator_prompt,
            "latest_market_snapshot": {
                "symbol": bar.symbol,
                "timestamp": bar.timestamp,
                "open": bar.open,
                "high": bar.high,
                "low": bar.low,
                "close": bar.close,
                "volume": bar.volume,
            },
            "symbols": self.trade_symbols,
            "equity": round(self._account_equity(), 4),
            "positions": self._position_snapshots(),
            "recent_market": recent,
            "candidate_signals": self._candidate_signals(),
            "activity_prompt": {
                "mode": self.activity_bias,
                "consecutive_holds": self._consecutive_holds,
                "force_trade_review": force_trade_review,
                "preferred_probe_size_pct": round(self.probe_size_pct, 4),
                "preferred_leverage": round(self.default_decision_leverage, 4),
                "guidance": (
                    "已连续观望，请从 candidate_signals 中选择一个小仓位 open_long/open_short 验证，"
                    "除非全部标的数据不可用、已有仓位需要先处理，或会触发 hard_limits。"
                    if force_trade_review
                    else "若候选标的有相对强弱、突破、反转或波动扩张，优先小仓位试单，不等待完美信号。"
                ),
            },
            "hard_limits": self._limits_snapshot(),
            "last_decision": self._last_decision,
            "required_json_schema": {
                "risk_policy": {"comment": "中文说明"},
                "decision": {
                    "allowed_actions": (
                        ["hold", "open_short", "close_short", "close_all"]
                        if self.trade_direction == "short_only"
                        else ["hold", "open_long", "open_short", "close_long", "close_short", "close_all"]
                    ),
                    "action": "hold",
                    "symbol": bar.symbol,
                    "observation_window": "由你自行决定并用中文描述",
                    "leverage": "number",
                    "reason": "中文理由",
                },
                "next_check_seconds": "number",
            },
        }

    async def _handle_decision(self, bar: BarData, raw: Dict[str, Any]) -> None:
        decision = raw.get("decision") if isinstance(raw.get("decision"), dict) else {}
        action = str(decision.get("action") or "hold").strip().lower()
        symbol = normalize_contract_symbol(str(decision.get("symbol") or bar.symbol).strip())
        reason = str(decision.get("reason") or "AI 未说明理由").strip()
        observation_window = str(
            decision.get("observation_window")
            or decision.get("analysis_window")
            or raw.get("observation_window")
            or raw.get("analysis_window")
            or ""
        ).strip()
        next_check = max(self.min_decision_interval_sec, _as_float(raw.get("next_check_seconds"), self.min_decision_interval_sec))
        next_check = min(next_check, self.max_decision_interval_sec)
        self._next_decision_after = time.monotonic() + next_check

        default_size = self.probe_size_pct if action in OPEN_ACTIONS else 0.0
        default_leverage = self.default_decision_leverage if action in OPEN_ACTIONS else 1.0
        normalized = {
            "action": action,
            "symbol": symbol,
            "size_pct": _as_fraction(decision.get("size_pct"), default_size),
            "leverage": _as_float(decision.get("leverage"), default_leverage),
            "reason": reason,
            "next_check_seconds": next_check,
        }
        if observation_window:
            normalized["observation_window"] = observation_window[:120]
        self._attach_open_notional_details(normalized)
        self._last_decision = normalized

        validation = await self._validate_decision(normalized, bar)
        if validation:
            await self._emit(
                "ai_trade_rejected",
                "AI交易被风控拦截",
                "AI 建议未通过硬性风控，未执行模拟盘下单",
                {"decision": normalized, "reasons": validation, "raw": raw},
                level="warning",
            )
            return

        if action == "hold":
            self._consecutive_holds += 1
            await self._emit(
                "ai_hold",
                "AI决定观望",
                reason,
                {"decision": normalized, "raw": raw},
            )
            return

        result = await self._execute_decision(normalized, bar)
        await self._emit(
            "ai_trade_executed" if result.get("status") == "filled" else "ai_trade_result",
            "AI模拟交易执行",
            self._result_summary(normalized, result),
            {"decision": normalized, "order_result": dict(result), "raw": raw},
            level="success" if result.get("status") == "filled" else "warning",
        )
        if result.get("status") == "filled":
            self._consecutive_holds = 0
            self._recent_trade_times.append(time.monotonic())

    async def _validate_decision(self, decision: Dict[str, Any], bar: BarData) -> List[str]:
        action = str(decision.get("action") or "")
        symbol = str(decision.get("symbol") or "")
        reasons: List[str] = []

        if action not in VALID_ACTIONS:
            reasons.append(f"action 不允许: {action}")
        if symbol not in self.allowed_symbols:
            reasons.append(f"交易标的不在允许币池: {symbol}")
        if action == "open_long" and not self.allow_long:
            reasons.append("当前 short_only 配置只允许做空，禁止开多")
        if action == "open_short" and not self.allow_short:
            reasons.append("当前配置不允许做空")
        if action in OPEN_ACTIONS | {"close_long", "close_short"} and symbol in self.allowed_symbols:
            if self._execution_price(symbol, bar) is None:
                reasons.append(f"{symbol} 当前没有可用成交价格，等待该标的新行情")
        if action in OPEN_ACTIONS:
            size_pct = _as_fraction(decision.get("size_pct"), 0.0)
            leverage = _as_float(decision.get("leverage"), 1.0)
            if leverage < 1:
                reasons.append("杠杆必须 >= 1")
            if leverage > self.max_leverage_cap:
                reasons.append(f"杠杆 {leverage:g}x 超过上限 {self.max_leverage_cap:g}x")
            if size_pct <= 0:
                reasons.append("开仓 size_pct 必须大于 0")
            if size_pct > self.max_single_position_pct:
                reasons.append(
                    f"单笔仓位 {size_pct * 100:.1f}% 超过上限 {self.max_single_position_pct * 100:.1f}%"
                )
            equity = self._account_equity()
            current_exposure = self._total_exposure()
            requested_notional = equity * size_pct
            new_notional = self._effective_open_notional(size_pct, equity)
            if equity <= 0:
                reasons.append("账户权益不可用")
            else:
                effective_size_pct = new_notional / equity if new_notional > 0 else 0.0
                if size_pct <= self.max_single_position_pct:
                    if (
                        requested_notional > 0
                        and requested_notional < self.min_order_notional_usdt
                        and effective_size_pct > self.max_single_position_pct + 1e-9
                    ):
                        reasons.append(
                            f"最小下单名义 {self.min_order_notional_usdt:g} USDT 对应仓位 "
                            f"{effective_size_pct * 100:.1f}%，超过单笔上限 {self.max_single_position_pct * 100:.1f}%"
                        )
                    elif effective_size_pct > self.max_single_position_pct + 1e-9:
                        reasons.append(
                            f"有效开仓仓位 {effective_size_pct * 100:.1f}% 超过单笔上限 "
                            f"{self.max_single_position_pct * 100:.1f}%"
                        )
            if equity > 0 and current_exposure + new_notional > equity * self.max_total_exposure_pct + 1e-9:
                reasons.append(
                    f"总风险敞口将达到 {(current_exposure + new_notional) / equity * 100:.1f}%，"
                    f"超过上限 {self.max_total_exposure_pct * 100:.1f}%"
                )
            if requested_notional and requested_notional < self.min_order_notional_usdt and new_notional < self.min_order_notional_usdt:
                reasons.append(f"开仓名义金额低于最小值 {self.min_order_notional_usdt:g} USDT")
            self._prune_trade_times()
            if self.max_trades_per_hour and len(self._recent_trade_times) >= self.max_trades_per_hour:
                reasons.append(f"最近 1 小时交易次数已达到上限 {self.max_trades_per_hour}")
        if action in {"close_long", "close_short"} and not await self.get_contract_position(symbol, _action_side(action)):
            reasons.append("没有可平的对应方向仓位")
        return reasons

    async def _execute_decision(self, decision: Dict[str, Any], bar: BarData) -> OrderResult:
        action = str(decision.get("action") or "hold")
        symbol = str(decision.get("symbol") or bar.symbol)
        if action in OPEN_ACTIONS:
            price = self._execution_price(symbol, bar)
            if price is None:
                return OrderResult({"status": "skipped", "reason": f"{symbol} 当前没有可用成交价格", "symbol": symbol})
            side = _action_side(action)
            notional = self._effective_open_notional(_as_fraction(decision.get("size_pct"), 0.0))
            return await self.open_contract(
                symbol,
                side,
                notional_usdt=notional,
                leverage=min(_as_float(decision.get("leverage"), 1.0), self.max_leverage_cap),
                price=price,
            )
        if action in {"close_long", "close_short"}:
            price = self._execution_price(symbol, bar)
            if price is None:
                return OrderResult({"status": "skipped", "reason": f"{symbol} 当前没有可用成交价格", "symbol": symbol})
            return await self.close_contract(symbol, _action_side(action), ratio=1.0, price=price)
        if action == "close_all":
            results = []
            for target_symbol in self.trade_symbols:
                for side in ("long", "short"):
                    if await self.get_contract_position(target_symbol, side):
                        target_price = self._execution_price(target_symbol, bar)
                        if target_price is None:
                            results.append(
                                OrderResult(
                                    {
                                        "status": "skipped",
                                        "reason": f"{target_symbol} 当前没有可用成交价格",
                                        "symbol": target_symbol,
                                        "pos_side": side,
                                    }
                                )
                            )
                            continue
                        results.append(await self.close_contract(target_symbol, side, ratio=1.0, price=target_price))
            filled = [item for item in results if item.get("status") == "filled"]
            return OrderResult({"status": "filled" if filled else "skipped", "closed": len(filled), "details": [dict(item) for item in results]})
        return OrderResult({"status": "skipped", "reason": "hold"})

    def _effective_open_notional(self, size_pct: float, equity: Optional[float] = None) -> float:
        account_equity = self._account_equity() if equity is None else float(equity)
        if account_equity <= 0 or size_pct <= 0:
            return 0.0
        requested = account_equity * size_pct
        if requested <= 0:
            return 0.0
        return max(requested, self.min_order_notional_usdt)

    def _attach_open_notional_details(self, decision: Dict[str, Any]) -> None:
        if str(decision.get("action") or "") not in OPEN_ACTIONS:
            return
        equity = self._account_equity()
        size_pct = _as_fraction(decision.get("size_pct"), 0.0)
        requested = equity * size_pct if equity > 0 and size_pct > 0 else 0.0
        effective = self._effective_open_notional(size_pct, equity)
        decision["requested_notional_usdt"] = round(requested, 6)
        decision["effective_notional_usdt"] = round(effective, 6)
        decision["effective_size_pct"] = round(effective / equity, 6) if equity > 0 and effective > 0 else 0.0

    def _execution_price(self, symbol: str, bar: BarData) -> Optional[float]:
        """Return a fill price for the decision symbol, never a different bar's close."""
        normalized = normalize_contract_symbol(symbol)
        bar_symbol = normalize_contract_symbol(getattr(bar, "symbol", ""))
        if normalized == bar_symbol:
            price = _as_float(getattr(bar, "close", None), 0.0)
            if price > 0:
                return price

        bars = self._bars.get(normalized)
        if bars:
            price = _as_float(bars[-1].close, 0.0)
            if price > 0:
                return price

        account = getattr(self.broker, "account", None)
        mark_prices = getattr(account, "mark_prices", None)
        if isinstance(mark_prices, dict):
            price = _as_float(mark_prices.get(normalized), 0.0)
            if price > 0:
                return price

        for position in self._position_snapshots():
            pos_symbol = normalize_contract_symbol(str(position.get("symbol") or ""))
            if pos_symbol != normalized:
                continue
            for key in ("mark_price", "markPrice", "entry_price", "entryPrice", "price"):
                price = _as_float(position.get(key), 0.0)
                if price > 0:
                    return price
        return None

    def _limits_snapshot(self) -> Dict[str, Any]:
        return {
            "paper_only": True,
            "llm_provider": self.llm_provider,
            "llm_model": self.llm_model,
            "trade_direction": self.trade_direction,
            "allow_long": self.allow_long,
            "allow_short": self.allow_short,
            "max_leverage_cap": self.max_leverage_cap,
            "max_single_position_pct": round(self.max_single_position_pct * 100, 4),
            "max_total_exposure_pct": round(self.max_total_exposure_pct * 100, 4),
            "min_decision_interval_sec": self.min_decision_interval_sec,
            "max_decision_interval_sec": self.max_decision_interval_sec,
            "max_trades_per_hour": self.max_trades_per_hour,
            "min_order_notional_usdt": self.min_order_notional_usdt,
            "probe_size_pct": round(self.probe_size_pct * 100, 4),
        }

    def _candidate_signals(self) -> List[Dict[str, Any]]:
        candidates: List[Dict[str, Any]] = []
        for symbol in self.trade_symbols:
            bars = list(self._bars.get(symbol, []))[-20:]
            if len(bars) < 2:
                continue
            closes = [max(0.0, _as_float(item.close, 0.0)) for item in bars]
            latest = closes[-1]
            if latest <= 0:
                continue

            def pct_from(offset: int) -> float:
                if len(closes) <= offset or closes[-1 - offset] <= 0:
                    return 0.0
                return (latest - closes[-1 - offset]) / closes[-1 - offset] * 100.0

            returns = [
                (closes[i] - closes[i - 1]) / closes[i - 1] * 100.0
                for i in range(1, len(closes))
                if closes[i - 1] > 0
            ][-10:]
            avg_abs_return = sum(abs(item) for item in returns) / len(returns) if returns else 0.0
            high = max(_as_float(item.high, latest) for item in bars)
            low = min(_as_float(item.low, latest) for item in bars)
            range_pct = (high - low) / latest * 100.0 if latest else 0.0
            volumes = [_as_float(item.volume, 0.0) for item in bars]
            recent_volume = sum(volumes[-3:]) / min(3, len(volumes))
            base_slice = volumes[-13:-3] or volumes[:-3] or volumes
            base_volume = sum(base_slice) / len(base_slice) if base_slice else recent_volume
            volume_ratio = recent_volume / base_volume if base_volume > 0 else 1.0
            ret_3 = pct_from(3)
            ret_10 = pct_from(10)
            score = abs(ret_3) * 1.2 + abs(ret_10) * 0.8 + avg_abs_return * 0.8 + max(0.0, volume_ratio - 1.0) * 0.5
            if ret_3 > 0 and ret_10 >= -0.1:
                bias = "long"
            elif ret_3 < 0 and ret_10 <= 0.1:
                bias = "short"
            else:
                bias = "reversal_watch"
            candidates.append(
                {
                    "symbol": symbol,
                    "last_close": latest,
                    "return_1": round(pct_from(1), 4),
                    "return_3": round(ret_3, 4),
                    "return_10": round(ret_10, 4),
                    "avg_abs_return": round(avg_abs_return, 4),
                    "range": round(range_pct, 4),
                    "volume_ratio": round(volume_ratio, 4),
                    "suggested_bias": bias,
                    "activity_score": round(score, 4),
                }
            )
        return sorted(candidates, key=lambda item: item["activity_score"], reverse=True)[:5]

    def _position_snapshots(self) -> List[Dict[str, Any]]:
        account = getattr(self.broker, "account", None)
        list_positions = getattr(account, "list_positions", None)
        if callable(list_positions):
            try:
                return list_positions()
            except Exception:
                logger.debug("AI自主交易读取合约仓位失败", exc_info=True)
        positions = getattr(self.broker, "positions", {})
        if not isinstance(positions, dict):
            return []
        snapshots = []
        for key, position in positions.items():
            if isinstance(position, dict):
                item = dict(position)
            else:
                item = {
                    "symbol": getattr(position, "symbol", ""),
                    "pos_side": getattr(position, "pos_side", ""),
                    "contracts": getattr(position, "contracts", 0.0),
                    "entry_price": getattr(position, "entry_price", 0.0),
                    "mark_price": getattr(position, "mark_price", 0.0),
                    "leverage": getattr(position, "leverage", 0.0),
                    "margin": getattr(position, "margin", 0.0),
                }
            item.setdefault("key", str(key))
            snapshots.append(item)
        return snapshots

    def _total_exposure(self) -> float:
        return sum(self._position_notional(pos) for pos in self._position_snapshots())

    def _prune_trade_times(self) -> None:
        cutoff = time.monotonic() - 3600
        while self._recent_trade_times and self._recent_trade_times[0] < cutoff:
            self._recent_trade_times.popleft()

    async def _emit(
        self,
        decision: str,
        label: str,
        summary: str,
        detail: Optional[Dict[str, Any]] = None,
        *,
        level: str = "info",
    ) -> None:
        payload = {
            "decision": decision,
            "decision_label": label,
            "summary": summary,
            "level": level,
            "detail": detail or {},
        }
        await self.broadcast_strategy_channel(payload)

    @staticmethod
    def _result_summary(decision: Dict[str, Any], result: OrderResult) -> str:
        action = decision.get("action")
        symbol = decision.get("symbol")
        status = result.get("status") or result.get("error") or "unknown"
        if status == "filled":
            return f"{symbol} {action} 已在模拟盘成交"
        return f"{symbol} {action} 未成交: {result.get('reason') or result.get('error') or status}"
