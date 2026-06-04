"""
系统设置 API — 动态开关管理
"""
import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

from app.core.config import settings
from app.db.local_db import db_instance as db

logger = logging.getLogger(__name__)
router = APIRouter()


class NotifySettingsResponse(BaseModel):
    enabled: bool
    webhook_configured: bool


class NotifyToggleRequest(BaseModel):
    enabled: bool


class StrategyProfitPushSettingsRequest(BaseModel):
    enabled: Optional[bool] = None
    interval_minutes: Optional[int] = None


class FeishuWebhookSettingsRequest(BaseModel):
    webhook_url: str


class FeishuWebhookSettingsResponse(BaseModel):
    webhook_configured: bool
    masked_webhook_url: Optional[str] = None


class LLMModelSettingsRequest(BaseModel):
    model: str


def _mask_webhook_url(url: str) -> Optional[str]:
    value = str(url or "").strip()
    if not value:
        return None
    token = value.rstrip("/").rsplit("/", 1)[-1]
    if len(token) <= 8:
        masked_token = "****"
    else:
        masked_token = f"{token[:4]}...{token[-4:]}"
    prefix = value.rsplit("/", 1)[0]
    return f"{prefix}/****{masked_token}"


@router.get("/notify")
async def get_notify_settings() -> NotifySettingsResponse:
    """获取飞书推送开关状态"""
    from app.services.feishu_notifier import feishu_notifier

    return NotifySettingsResponse(
        enabled=feishu_notifier.is_ready(),
        webhook_configured=feishu_notifier.has_webhook(),
    )


@router.post("/notify")
async def set_notify_settings(req: NotifyToggleRequest) -> NotifySettingsResponse:
    """动态切换飞书推送开关（运行时生效，不写 .env）"""
    settings.ENABLE_FEISHU_NOTIFY = req.enabled
    logger.info("飞书推送开关已 %s", "开启" if req.enabled else "关闭")

    from app.services.feishu_notifier import feishu_notifier
    feishu_notifier.enabled = req.enabled

    return NotifySettingsResponse(
        enabled=feishu_notifier.is_ready(),
        webhook_configured=feishu_notifier.has_webhook(),
    )


@router.get("/feishu-webhook")
async def get_feishu_webhook_settings() -> FeishuWebhookSettingsResponse:
    """获取统一飞书 Webhook 配置状态，不返回明文地址。"""
    url = db.get_feishu_webhook_url()
    return FeishuWebhookSettingsResponse(
        webhook_configured=bool(url),
        masked_webhook_url=_mask_webhook_url(url or ""),
    )


@router.post("/feishu-webhook")
async def set_feishu_webhook_settings(req: FeishuWebhookSettingsRequest) -> FeishuWebhookSettingsResponse:
    """保存统一飞书 Webhook，所有飞书通知共用该地址。"""
    url = str(req.webhook_url or "").strip()
    if "open-apis/bot" not in url:
        raise HTTPException(status_code=400, detail="请填写有效的飞书机器人 Webhook URL")

    db.set_feishu_webhook_url(url)
    db.clear_monitor_profit_push_error()
    db.clear_live_profit_push_error()
    settings.ENABLE_FEISHU_NOTIFY = True
    from app.services.feishu_notifier import feishu_notifier

    feishu_notifier.enabled = True
    logger.info("统一飞书 Webhook 已更新")
    return FeishuWebhookSettingsResponse(
        webhook_configured=True,
        masked_webhook_url=_mask_webhook_url(url),
    )


@router.get("/llm-model")
async def get_llm_model_settings() -> dict:
    """获取全局大模型配置状态，不返回 API Key 明文。"""
    from app.services.agent.llm_client import get_llm_model_config

    return get_llm_model_config()


@router.put("/llm-model")
async def set_llm_model_settings(req: LLMModelSettingsRequest) -> dict:
    """更新全局大模型名称，所有 Qwen/DashScope 调用共用该配置。"""
    from app.services.agent.llm_client import set_llm_model_name

    try:
        return await set_llm_model_name(req.model)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/llm-models")
async def add_llm_model_settings(req: LLMModelSettingsRequest) -> dict:
    """新增一个全局大模型候选项，并切换为当前模型。"""
    from app.services.agent.llm_client import add_llm_model_name

    try:
        return await add_llm_model_name(req.model)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/llm-model/test")
async def test_llm_model_settings() -> dict:
    """用当前全局大模型配置发起一次最小连接测试。"""
    from app.services.agent.llm_client import (
        describe_qwen_exception,
        get_llm_model_config,
        get_qwen_client,
        has_agent_api_key,
    )

    if not has_agent_api_key():
        raise HTTPException(status_code=400, detail="DASHSCOPE_API_KEY 未配置，请在 .env 中设置")

    client = get_qwen_client()
    try:
        reply = await client.chat(
            [{"role": "user", "content": "请只回复 OK"}],
            temperature=0.0,
            max_tokens=16,
            max_retries=1,
        )
        cfg = get_llm_model_config()
        return {
            "ok": True,
            "model": cfg["model"],
            "base_url": cfg["base_url"],
            "reply": reply.strip()[:80],
        }
    except Exception as e:
        logger.warning("全局大模型连接测试失败: %s", describe_qwen_exception(e))
        raise HTTPException(status_code=502, detail=f"模型连接测试失败: {describe_qwen_exception(e)}")


def _profit_push_response(config: dict) -> dict:
    from app.services.feishu_notifier import feishu_notifier

    image_status = feishu_notifier.get_profit_report_image_status()
    delivery = feishu_notifier.get_last_profit_report_delivery()
    return {
        "enabled": bool(config.get("enabled")),
        "interval_minutes": int(config.get("interval_minutes") or 60),
        "running": bool(config.get("running")),
        "last_started_at": config.get("last_started_at"),
        "last_sent_at": config.get("last_sent_at"),
        "last_finished_at": config.get("last_finished_at"),
        "last_error": config.get("last_error"),
        "last_skip_reason": config.get("last_skip_reason"),
        "notify_ready": bool(config.get("notify_ready")),
        "notify_enabled": bool(feishu_notifier.is_ready()),
        "webhook_configured": feishu_notifier.has_webhook(),
        "profit_report_image_ready": bool(image_status.get("ready")),
        "profit_report_image_configured": bool(image_status.get("app_configured")),
        "profit_report_image_cjk_font_available": bool(image_status.get("cjk_font_available")),
        "profit_report_image_reason": image_status.get("reason"),
        "last_delivery_type": delivery.get("type"),
        "last_delivery_error": delivery.get("error") or delivery.get("image_reason"),
    }


@router.get("/strategy-profit-push")
async def get_strategy_profit_push_settings() -> dict:
    """获取运行策略收益卡片推送配置"""
    from app.services.strategy_profit_push_service import strategy_profit_push_service

    return _profit_push_response(strategy_profit_push_service.get_config())


@router.post("/strategy-profit-push")
async def set_strategy_profit_push_settings(req: StrategyProfitPushSettingsRequest) -> dict:
    """更新运行策略收益卡片推送配置"""
    from app.services.strategy_profit_push_service import strategy_profit_push_service

    cfg = strategy_profit_push_service.update_config(req.model_dump(exclude_unset=True))
    logger.info(
        "运行策略收益卡片推送配置已更新: enabled=%s interval_minutes=%s",
        cfg.get("enabled"),
        cfg.get("interval_minutes"),
    )
    return _profit_push_response(cfg)


@router.post("/strategy-profit-push/test")
async def test_strategy_profit_push() -> dict:
    """立即推送一次运行策略收益卡片"""
    from app.services.strategy_profit_push_service import strategy_profit_push_service

    result = await strategy_profit_push_service.run_once(force=True)
    cfg = strategy_profit_push_service.get_config()
    return {
        **_profit_push_response(cfg),
        "result": result,
    }


@router.get("/live-profit-push")
async def get_live_profit_push_settings() -> dict:
    """获取实盘收益卡片推送配置"""
    from app.services.live_profit_push_service import live_profit_push_service

    return _profit_push_response(live_profit_push_service.get_config())


@router.post("/live-profit-push")
async def set_live_profit_push_settings(req: StrategyProfitPushSettingsRequest) -> dict:
    """更新实盘收益卡片推送配置"""
    from app.services.live_profit_push_service import live_profit_push_service

    cfg = live_profit_push_service.update_config(req.model_dump(exclude_unset=True))
    logger.info(
        "实盘收益卡片推送配置已更新: enabled=%s interval_minutes=%s",
        cfg.get("enabled"),
        cfg.get("interval_minutes"),
    )
    return _profit_push_response(cfg)


@router.post("/live-profit-push/test")
async def test_live_profit_push() -> dict:
    """立即推送一次实盘收益卡片"""
    from app.services.live_profit_push_service import live_profit_push_service

    result = await live_profit_push_service.run_once(force=True)
    cfg = live_profit_push_service.get_config()
    return {
        **_profit_push_response(cfg),
        "result": result,
    }
