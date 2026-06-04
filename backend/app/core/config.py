"""
QuantBase 配置管理
"""
import os
from typing import List, Optional
from pydantic_settings import BaseSettings
from pydantic import field_validator
import json


class Settings(BaseSettings):
    """应用配置"""

    # API 配置
    API_V1_STR: str = "/api/v1"
    PROJECT_NAME: str = "QuantBase"

    # CORS 配置
    BACKEND_CORS_ORIGINS: List[str] = ["http://localhost:8888", "http://127.0.0.1:8888"]

    @field_validator("BACKEND_CORS_ORIGINS", mode="before")
    @classmethod
    def assemble_cors_origins(cls, v):
        if isinstance(v, str):
            if v.startswith("["):
                return json.loads(v)
            return [i.strip() for i in v.split(",")]
        return v

    # 数据库配置
    DB_PATH: Optional[str] = None

    # 日志配置
    LOG_LEVEL: str = "INFO"

    # 交易所配置 - OKX
    OKX_API_KEY: Optional[str] = None
    OKX_API_SECRET: Optional[str] = None
    OKX_PASSPHRASE: Optional[str] = None
    OKX_TESTNET: bool = True

    # 交易所配置 - Binance USD-M（首版用于跨所套利研究与账户展示）
    BINANCE_API_KEY: Optional[str] = None
    BINANCE_API_SECRET: Optional[str] = None
    BINANCE_TESTNET: bool = False

    # AI Agent 配置
    DASHSCOPE_API_KEY: Optional[str] = None
    QWEN_API_KEY: Optional[str] = None
    AI_AGENT_MODEL: str = "qwen3.6-plus"
    QWEN_MODEL: str = "qwen3.6-plus"
    QWEN_BASE_URL: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    AI_AGENT_ENABLE_THINKING: bool = False
    AI_AGENT_THINKING_BUDGET: int = 512
    AI_AGENT_REQUEST_TIMEOUT: int = 180
    AGENT_MAX_ITERATIONS: int = 10
    AGENT_CODE_TIMEOUT: int = 120
    HERMES_AGENT_ENABLED: bool = False
    HERMES_AGENT_COMMAND: str = "hermes"
    HERMES_AGENT_TIMEOUT: int = 240

    # 飞书 Webhook 推送
    FEISHU_WEBHOOK_URL: Optional[str] = None
    ENABLE_FEISHU_NOTIFY: bool = False
    FEISHU_APP_ID: Optional[str] = None
    FEISHU_APP_SECRET: Optional[str] = None
    FEISHU_PROFIT_CARD_IMAGE_ENABLED: bool = True

    # 登录与临时邀请码访问控制
    QUANTBASE_AUTH_ENABLED: bool = False
    QUANTBASE_ADMIN_USERNAME: Optional[str] = None
    QUANTBASE_ADMIN_PASSWORD_HASH: Optional[str] = None
    QUANTBASE_AUTH_COOKIE_NAME: str = "quantbase_session"
    QUANTBASE_AUTH_COOKIE_SECURE: bool = False
    QUANTBASE_ADMIN_SESSION_HOURS: int = 24 * 365 * 10

    # 真实账户与实盘执行总开关。社区版默认关闭。
    QUANTBASE_LIVE_TRADING_ENABLED: bool = False

    # Redis 配置 (可选)
    REDIS_URL: Optional[str] = None

    # 数据同步间隔 (秒)
    SYNC_INTERVAL_TICKER: int = 10
    SYNC_INTERVAL_FUNDING: int = 60
    SYNC_INTERVAL_KLINE: int = 300

    class Config:
        env_file = ".env"
        case_sensitive = True
        extra = "ignore"


# 全局配置实例
settings = Settings()
