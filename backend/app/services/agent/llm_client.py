"""
通义千问 LLM 客户端封装
兼容 OpenAI Chat Completions API 格式

修复: 复用 httpx.AsyncClient 避免文件描述符泄漏 (Too many open files)
"""
import json
import logging
import asyncio
import re
from pathlib import Path
from typing import Dict, Any, Optional, List

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

_MAX_RETRIES = 3
_RETRY_DELAY = 2.0
_MODEL_NAME_RE = re.compile(r"^[A-Za-z0-9._:/+-]{2,128}$")
_PROJECT_ROOT = Path(__file__).resolve().parents[4]
_MODEL_CONFIG_PATH = _PROJECT_ROOT / "data" / "ai_lab_model_config.json"
_DEFAULT_MODEL_CHOICES = (
    "qwen3.6-plus",
    "qwen3.6-max",
    "qwen-plus",
    "qwen-max",
)
_DASHSCOPE_FREE_TIER_MODEL_CHOICES = (
    "qwen3.6-flash-2026-04-16",
    "qwen3.6-35b-a3b",
    "qwen3.5-35b-a3b",
    "qwen3.5-122b-a10b",
    "qwen3.5-plus",
    "qwen3.5-plus-2026-02-15",
)


def get_agent_api_key() -> Optional[str]:
    """Return the configured DashScope-compatible key without exposing it."""
    return settings.DASHSCOPE_API_KEY or settings.QWEN_API_KEY


def get_agent_api_key_source() -> Optional[str]:
    if settings.DASHSCOPE_API_KEY:
        return "DASHSCOPE_API_KEY"
    if settings.QWEN_API_KEY:
        return "QWEN_API_KEY"
    return None


def has_agent_api_key() -> bool:
    return bool(get_agent_api_key())


def _dedupe_models(values: List[str]) -> List[str]:
    out: List[str] = []
    for raw in values:
        model = str(raw or "").strip()
        if model and _MODEL_NAME_RE.fullmatch(model) and model not in out:
            out.append(model)
    return out


def _read_model_config_file() -> Dict[str, Any]:
    if not _MODEL_CONFIG_PATH.exists():
        return {}
    try:
        data = json.loads(_MODEL_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        logger.exception("大模型配置读取失败: %s", _MODEL_CONFIG_PATH)
        return {}
    return data if isinstance(data, dict) else {}


def _read_model_override() -> Optional[str]:
    data = _read_model_config_file()
    model = str(data.get("model") or "").strip()
    if model and _MODEL_NAME_RE.fullmatch(model):
        return model
    return None


def get_agent_default_model_name() -> str:
    return (settings.AI_AGENT_MODEL or settings.QWEN_MODEL or "qwen3.6-plus").strip()


def get_agent_model_name() -> str:
    return _read_model_override() or get_agent_default_model_name()


def get_llm_model_choices() -> List[str]:
    data = _read_model_config_file()
    configured = data.get("models") if isinstance(data.get("models"), list) else []
    return _dedupe_models([
        get_agent_default_model_name(),
        *_DEFAULT_MODEL_CHOICES,
        *configured,
        *_DASHSCOPE_FREE_TIER_MODEL_CHOICES,
        get_agent_model_name(),
    ])


def get_dashscope_free_tier_model_choices() -> List[str]:
    return _dedupe_models(list(_DASHSCOPE_FREE_TIER_MODEL_CHOICES))


def get_llm_fallback_model_choices(primary: Optional[str] = None) -> List[str]:
    data = _read_model_config_file()
    configured = data.get("models") if isinstance(data.get("models"), list) else []
    return _dedupe_models([
        primary or "",
        *_DASHSCOPE_FREE_TIER_MODEL_CHOICES,
        *configured,
        get_agent_model_name(),
        get_agent_default_model_name(),
        *_DEFAULT_MODEL_CHOICES,
    ])


def get_llm_model_config() -> Dict[str, Any]:
    return {
        "model": get_agent_model_name(),
        "default_model": get_agent_default_model_name(),
        "models": get_llm_model_choices(),
        "free_tier_models": get_dashscope_free_tier_model_choices(),
        "model_fallback_enabled": True,
        "base_url": settings.QWEN_BASE_URL,
        "enable_thinking": settings.AI_AGENT_ENABLE_THINKING,
        "request_timeout": settings.AI_AGENT_REQUEST_TIMEOUT,
        "api_key_configured": has_agent_api_key(),
        "api_key_source": get_agent_api_key_source(),
    }


def get_agent_model_config() -> Dict[str, Any]:
    """Backward-compatible alias for the global LLM model configuration."""
    return get_llm_model_config()


def _validate_model_name(model: str) -> str:
    normalized = (model or "").strip()
    if not normalized:
        raise ValueError("模型名称不能为空")
    if not _MODEL_NAME_RE.fullmatch(normalized):
        raise ValueError("模型名称只能包含字母、数字、点、下划线、中划线、加号、冒号或斜杠，长度 2~128")
    return normalized


def validate_llm_model_name(model: str) -> str:
    """Validate a DashScope-compatible model name without changing global config."""
    return _validate_model_name(model)


async def set_llm_model_name(model: str) -> Dict[str, Any]:
    normalized = _validate_model_name(model)
    models = _dedupe_models([*get_llm_model_choices(), normalized])
    _MODEL_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    _MODEL_CONFIG_PATH.write_text(
        json.dumps({"model": normalized, "models": models}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    await reset_qwen_client()
    return get_llm_model_config()


async def add_llm_model_name(model: str) -> Dict[str, Any]:
    """Add a model choice and select it for subsequent global LLM calls."""
    return await set_llm_model_name(model)


async def set_agent_model_name(model: str) -> Dict[str, Any]:
    """Backward-compatible alias for callers that still use Agent wording."""
    return await set_llm_model_name(model)


class QwenClient:
    """通义千问 API 客户端 (兼容 OpenAI 格式)"""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        base_url: Optional[str] = None,
    ):
        self.api_key = api_key or get_agent_api_key()
        self.model = model or get_agent_model_name()
        self.base_url = (base_url or settings.QWEN_BASE_URL).rstrip("/")
        if not self.api_key:
            raise ValueError("DASHSCOPE_API_KEY 未配置，请在 .env 中设置")
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=max(10, int(settings.AI_AGENT_REQUEST_TIMEOUT)),
                trust_env=False,
            )
        return self._client

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        response_format: Optional[Dict[str, str]] = None,
        max_retries: int = _MAX_RETRIES,
    ) -> str:
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        body: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        # We call the OpenAI-compatible HTTP endpoint directly, so DashScope
        # extension fields must be top-level JSON fields. Disabling thinking by
        # default keeps AI Lab strategy generation from timing out on long
        # hidden reasoning traces.
        body["enable_thinking"] = bool(settings.AI_AGENT_ENABLE_THINKING)
        if settings.AI_AGENT_ENABLE_THINKING:
            body["thinking_budget"] = max(1, int(settings.AI_AGENT_THINKING_BUDGET))
        if response_format:
            body["response_format"] = response_format

        retry_count = max(1, int(max_retries))
        last_error_message = ""
        for attempt in range(1, retry_count + 1):
            try:
                client = await self._get_client()
                resp = await client.post(url, headers=headers, json=body)
                resp.raise_for_status()
                data = resp.json()
                content = data["choices"][0]["message"]["content"]
                return content
            except httpx.HTTPStatusError as e:
                last_error_message = describe_qwen_exception(e)
                logger.warning(
                    "Qwen API HTTP %s (attempt %d/%d): %s",
                    e.response.status_code, attempt, retry_count, e.response.text[:300],
                )
                if e.response.status_code == 429 or e.response.status_code >= 500:
                    await asyncio.sleep(_RETRY_DELAY * attempt)
                    continue
                raise RuntimeError(last_error_message) from e
            except (httpx.RequestError, KeyError, IndexError, TypeError, json.JSONDecodeError) as e:
                last_error_message = describe_qwen_exception(e)
                logger.warning("Qwen API error (attempt %d/%d): %s", attempt, retry_count, last_error_message)
                # 连接错误时重置 client
                await self.close()
                await asyncio.sleep(_RETRY_DELAY * attempt)

        raise RuntimeError(f"Qwen API failed after {retry_count} retries: {last_error_message or '未知错误'}")

    async def chat_json(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.3,
        max_tokens: int = 4096,
    ) -> Dict[str, Any]:
        raw = await self.chat(
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
        )
        text = raw.strip()
        if text.startswith("```"):
            first_nl = text.index("\n")
            text = text[first_nl + 1:]
            if text.endswith("```"):
                text = text[:-3].strip()
        return json.loads(text)


def describe_qwen_exception(error: Exception) -> str:
    if isinstance(error, httpx.HTTPStatusError):
        body = error.response.text.strip()
        if len(body) > 500:
            body = body[:500] + "..."
        return f"HTTP {error.response.status_code}: {body or error.response.reason_phrase}"
    if isinstance(error, httpx.TimeoutException):
        return (
            f"{error.__class__.__name__}: 请求 DashScope 超时，"
            f"当前超时 {settings.AI_AGENT_REQUEST_TIMEOUT}s；请检查网络、模型名或稍后重试"
        )
    if isinstance(error, httpx.ConnectError):
        return f"ConnectError: 无法连接 DashScope ({settings.QWEN_BASE_URL})，请检查服务器网络/DNS/防火墙"
    if isinstance(error, httpx.RequestError):
        detail = str(error).strip()
        return f"{error.__class__.__name__}: {detail or '请求 DashScope 失败'}"
    if isinstance(error, KeyError):
        return f"响应缺少字段: {error}"
    detail = str(error).strip()
    return detail or error.__class__.__name__


def is_dashscope_free_tier_exhausted(error: object) -> bool:
    if isinstance(error, Exception):
        detail = describe_qwen_exception(error)
    else:
        detail = str(error or "")
    normalized = detail.lower()
    if "allocationquota.freetieronly" in normalized or "freetieronly" in normalized:
        return True
    if "free tier" in normalized and ("quota" in normalized or "allocation" in normalized):
        return True
    if "免费额度" in detail and any(token in detail for token in ("用完", "耗尽", "不足", "用尽")):
        return True
    return False


qwen_client: Optional[QwenClient] = None
qwen_client_signature: Optional[tuple[str, str, str]] = None
qwen_clients: Dict[tuple[str, str, str], QwenClient] = {}


def get_qwen_client(model: Optional[str] = None) -> QwenClient:
    global qwen_client, qwen_client_signature
    api_key = get_agent_api_key()
    selected_model = _validate_model_name(model) if model else get_agent_model_name()
    base_url = settings.QWEN_BASE_URL.rstrip("/")
    signature = (api_key or "", selected_model, base_url)
    if signature not in qwen_clients:
        qwen_clients[signature] = QwenClient(api_key=api_key, model=selected_model, base_url=base_url)
    qwen_client = qwen_clients[signature]
    qwen_client_signature = signature
    return qwen_client


async def reset_qwen_client() -> None:
    global qwen_client, qwen_client_signature
    clients = list(qwen_clients.values())
    qwen_clients.clear()
    qwen_client = None
    qwen_client_signature = None
    for client in clients:
        await client.close()
