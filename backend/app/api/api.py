"""
API 路由聚合（v1）
"""
from fastapi import APIRouter
from app.api.endpoints import market, funding, strategy, backtest, monitor, health, websocket, data_sync, agent, ai_predict, settings

api_router = APIRouter()

api_router.include_router(health.router, prefix="/health", tags=["健康检查"])
api_router.include_router(market.router, prefix="/market", tags=["行情数据"])
api_router.include_router(funding.router, prefix="/funding", tags=["资金费率"])
api_router.include_router(strategy.router, prefix="/strategy", tags=["策略"])
api_router.include_router(backtest.router, prefix="/backtest", tags=["回测"])
api_router.include_router(monitor.router, prefix="/monitor", tags=["监控告警"])
api_router.include_router(data_sync.router, prefix="/data_sync", tags=["数据同步"])
api_router.include_router(agent.router, prefix="/agent", tags=["AI Agent"])
api_router.include_router(ai_predict.router, prefix="/ai_predict", tags=["AI 预测"])
api_router.include_router(settings.router, prefix="/settings", tags=["系统设置"])
api_router.include_router(websocket.router, tags=["WebSocket"])
