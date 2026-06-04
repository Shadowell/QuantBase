"""
健康检查 API
"""
from fastapi import APIRouter
from datetime import datetime

router = APIRouter()


@router.get("")
async def health_check():
    """健康检查"""
    return {
        "status": "healthy",
        "service": "QuantBase API",
        "timestamp": datetime.now().isoformat()
    }


@router.get("/exchanges")
async def check_exchanges():
    """检查交易所连接状态"""
    from app.exchange import exchange_manager

    status = {}
    for name in ["okx"]:
        try:
            exchange = exchange_manager.get_exchange(name)
            if exchange:
                # 尝试获取服务器时间
                exchange.load_markets()
                status[name] = "connected"
            else:
                status[name] = "not_configured"
        except Exception as e:
            status[name] = f"error: {str(e)}"

    return {"exchanges": status}
