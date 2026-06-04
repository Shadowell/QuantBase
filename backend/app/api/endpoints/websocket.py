"""
WebSocket API 端点
"""
import json
import logging
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from app.services.websocket_service import connection_manager, realtime_service

router = APIRouter()
logger = logging.getLogger(__name__)


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    WebSocket 连接端点

    消息格式:

    订阅:
    {
        "action": "subscribe",
        "channel": "ticker|kline|orderbook|trades|funding",
        "exchange": "okx",
        "symbol": "BTC/USDT"  // 可选，某些频道必须
    }

    取消订阅:
    {
        "action": "unsubscribe",
        "channel": "ticker",
        "exchange": "okx",
        "symbol": "BTC/USDT"
    }

    心跳:
    {
        "action": "ping"
    }

    推送消息格式:
    {
        "channel": "ticker",
        "exchange": "okx",
        "symbol": "BTC/USDT",
        "data": {...},
        "timestamp": 1234567890123
    }
    """
    await connection_manager.connect(websocket)

    try:
        # 发送连接成功消息
        await connection_manager.send_personal(websocket, {
            "type": "connected",
            "message": "WebSocket connected successfully"
        })

        while True:
            # 接收消息
            data = await websocket.receive_text()

            try:
                message = json.loads(data)
                action = message.get("action")

                if action == "ping":
                    # 心跳响应
                    await connection_manager.send_personal(websocket, {
                        "type": "pong"
                    })

                elif action == "subscribe":
                    channel = message.get("channel")
                    exchange = message.get("exchange")
                    symbol = message.get("symbol")
                    timeframe = message.get("timeframe")

                    if not channel or not exchange:
                        await connection_manager.send_personal(websocket, {
                            "type": "error",
                            "message": "Missing channel or exchange"
                        })
                        continue

                    sub_key = await connection_manager.subscribe(
                        websocket, channel, exchange, symbol, timeframe
                    )

                    await connection_manager.send_personal(websocket, {
                        "type": "subscribed",
                        "channel": channel,
                        "exchange": exchange,
                        "symbol": symbol,
                        "timeframe": timeframe,
                        "subscription": sub_key
                    })

                    logger.info(f"Client subscribed to {sub_key}")

                elif action == "unsubscribe":
                    channel = message.get("channel")
                    exchange = message.get("exchange")
                    symbol = message.get("symbol")
                    timeframe = message.get("timeframe")

                    await connection_manager.unsubscribe(
                        websocket, channel, exchange, symbol, timeframe
                    )

                    await connection_manager.send_personal(websocket, {
                        "type": "unsubscribed",
                        "channel": channel,
                        "exchange": exchange,
                        "symbol": symbol,
                        "timeframe": timeframe
                    })

                else:
                    await connection_manager.send_personal(websocket, {
                        "type": "error",
                        "message": f"Unknown action: {action}"
                    })

            except json.JSONDecodeError:
                await connection_manager.send_personal(websocket, {
                    "type": "error",
                    "message": "Invalid JSON"
                })

    except WebSocketDisconnect:
        await connection_manager.disconnect(websocket)
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        await connection_manager.disconnect(websocket)


@router.get("/ws/stats")
async def websocket_stats():
    """获取 WebSocket 统计信息"""
    return connection_manager.get_stats()
