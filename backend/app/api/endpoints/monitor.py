"""
监控告警 API
"""
from fastapi import APIRouter, HTTPException, Query
from typing import List, Optional
from pydantic import BaseModel
from app.services.alert_service import alert_service, AlertType
from app.exchange import exchange_manager
from app.exchange.okx_response import first_okx_data_row, open_interest_base_units

router = APIRouter()

STRATEGY_ALERT_TYPES = {
    AlertType.STRATEGY_RETURN_BELOW.value,
    AlertType.STRATEGY_LIQUIDATION_RISK.value,
}


def _okx_swap_inst_id(symbol: str) -> str:
    s = str(symbol or "").strip().upper()
    if s.endswith("-SWAP"):
        return s
    base = s.split("/", 1)[0] if "/" in s else s.split("-", 1)[0]
    quote_part = s.split("/", 1)[1] if "/" in s else "USDT"
    quote = quote_part.split(":", 1)[0].split("-", 1)[0] or "USDT"
    return f"{base}-{quote}-SWAP"


def _okx_ratio_currency(symbol: str) -> str:
    return _okx_swap_inst_id(symbol).split("-", 1)[0]


def _first_okx_row(response):
    if isinstance(response, dict):
        data = response.get("data")
        if isinstance(data, list) and data:
            return data[0]
    if isinstance(response, list) and response:
        return response[0]
    return None


def _parse_okx_long_short_row(row):
    ratio_raw = None
    ts_raw = None
    if isinstance(row, dict):
        ratio_raw = row.get("longShortRatio") or row.get("long_short_ratio") or row.get("ratio") or row.get("lsr")
        ts_raw = row.get("ts") or row.get("timestamp") or row.get("time")
    elif isinstance(row, (list, tuple)) and row:
        ts_raw = row[0]
        if len(row) >= 2:
            ratio_raw = row[1]
    return float(ratio_raw or 0), int(ts_raw or 0)


def _parse_okx_open_interest_row(row, market):
    if not isinstance(row, dict):
        raise HTTPException(status_code=500, detail="Unexpected open interest response")
    oi_raw = float(row.get("oi") or row.get("openInterest") or 0)
    oi_base_raw = row.get("oiCcy") or row.get("openInterestAmount") or row.get("openInterestBtc")
    oi_base = float(oi_base_raw) if oi_base_raw not in (None, "") else open_interest_base_units(oi_raw, market)
    ts_val = None
    ts_raw = row.get("ts") or row.get("time") or row.get("timestamp")
    if ts_raw is not None:
        ts_val = int(float(ts_raw))
    return oi_raw, oi_base, ts_val


class AlertCreateRequest(BaseModel):
    """创建告警请求"""
    name: str
    type: str  # price_above/price_below/price_change/funding_above/funding_below/strategy_return_below/strategy_liquidation_risk
    exchange: str = "okx"
    symbol: Optional[str] = None
    threshold: float
    strategy_id: Optional[int] = None
    cooldown_sec: Optional[int] = None
    telegram_bot_token: Optional[str] = None
    telegram_chat_id: Optional[str] = None
    webhook_url: Optional[str] = None


@router.get("/alerts")
async def get_alerts():
    """
    获取告警列表
    """
    return alert_service.get_alerts()


@router.post("/alert")
async def create_alert(request: AlertCreateRequest):
    """
    创建告警

    告警类型:
    - price_above: 价格高于阈值
    - price_below: 价格低于阈值
    - price_change: 价格变动超过阈值(%)
    - funding_above: 资金费率高于阈值
    - funding_below: 资金费率低于阈值
    """
    if request.type in STRATEGY_ALERT_TYPES:
        if request.strategy_id is None:
            raise HTTPException(status_code=400, detail=f"strategy_id is required for {request.type}")
        if request.type == AlertType.STRATEGY_LIQUIDATION_RISK.value and request.threshold <= 0:
            raise HTTPException(status_code=400, detail="threshold must be positive for strategy_liquidation_risk")
        from app.db.local_db import db_instance as db

        strategy = db.get_strategy_by_id(int(request.strategy_id))
        condition = {
            'scope': 'strategy',
            'strategy_id': int(request.strategy_id),
            'strategy_name': (strategy or {}).get('name') or f"策略 #{request.strategy_id}",
            'threshold': request.threshold,
            'cooldown_sec': int(request.cooldown_sec or 3600),
        }
        if request.type == AlertType.STRATEGY_LIQUIDATION_RISK.value:
            condition['metric'] = 'liquidation_buffer_pct'
    else:
        if not request.symbol:
            raise HTTPException(status_code=400, detail="symbol is required")
        condition = {
            'exchange': request.exchange,
            'symbol': request.symbol,
            'threshold': request.threshold,
        }
        if request.cooldown_sec is not None:
            condition['cooldown_sec'] = int(request.cooldown_sec)

    notification = {}
    if request.telegram_bot_token and request.telegram_chat_id:
        notification['telegram'] = {
            'bot_token': request.telegram_bot_token,
            'chat_id': request.telegram_chat_id,
        }
    if request.webhook_url:
        notification['webhook'] = {
            'url': request.webhook_url,
        }

    alert_id = await alert_service.create_alert(
        name=request.name,
        alert_type=request.type,
        condition=condition,
        notification=notification,
    )

    return {'id': alert_id, 'message': 'Alert created'}


@router.put("/alert/{alert_id}")
async def update_alert(alert_id: int, enabled: bool = Query(...)):
    """
    启用/禁用告警
    """
    await alert_service.toggle_alert(alert_id, enabled)
    return {'message': f'Alert {"enabled" if enabled else "disabled"}'}


@router.delete("/alert/{alert_id}")
async def delete_alert(alert_id: int):
    """
    删除告警
    """
    await alert_service.delete_alert(alert_id)
    return {'message': 'Alert deleted'}


@router.get("/running-strategies")
async def get_running_strategies():
    """获取运行中的策略（含实时 equity / positions / PnL）"""
    from app.services.strategy_engine import strategy_engine
    return strategy_engine.get_all_running(refresh_marks=False)


@router.get("/active_strategies")
async def get_active_strategies():
    """
    监控大盘 — 返回所有运行中策略的实时权益、浮动盈亏、持仓详情。
    前端应每 3-5 秒轮询此接口。
    """
    from app.services.strategy_engine import strategy_engine
    return strategy_engine.get_all_running(refresh_marks=False)


@router.get("/liquidations")
async def get_liquidations(
    exchange: str = Query("okx", description="交易所"),
    symbol: Optional[str] = Query(None, description="交易对"),
    limit: int = Query(50, ge=1, le=200)
):
    """
    获取爆仓数据 (从数据库缓存)
    """
    from app.db.local_db import db_instance as db

    conn = db.get_connection()
    cursor = conn.cursor()

    if symbol:
        cursor.execute('''
            SELECT exchange, symbol, timestamp, side, price, quantity, value
            FROM liquidation_history
            WHERE exchange = ? AND symbol = ?
            ORDER BY timestamp DESC
            LIMIT ?
        ''', (exchange, symbol, limit))
    else:
        cursor.execute('''
            SELECT exchange, symbol, timestamp, side, price, quantity, value
            FROM liquidation_history
            WHERE exchange = ?
            ORDER BY timestamp DESC
            LIMIT ?
        ''', (exchange, limit))

    rows = cursor.fetchall()
    conn.close()

    return [dict(row) for row in rows]


@router.get("/long-short-ratio")
async def get_long_short_ratio(
    exchange_name: str = Query("okx", description="交易所"),
    symbol: str = Query("BTC/USDT:USDT", description="交易对")
):
    """
    获取多空比
    """
    exchange = exchange_manager.get_exchange(exchange_name)
    if not exchange:
        raise HTTPException(status_code=400, detail="Exchange not supported")

    # OKX v5 Rubik 公共数据 API
    if exchange_name == 'okx' and hasattr(exchange.exchange, 'publicGetRubikStatContractsLongShortAccountRatio'):
        try:
            response = exchange.exchange.publicGetRubikStatContractsLongShortAccountRatio({
                'ccy': _okx_ratio_currency(symbol),
                'period': '5m',
            })

            item = _first_okx_row(response)
            if item is None:
                raise HTTPException(status_code=500, detail="No data from exchange")
            ratio, ts = _parse_okx_long_short_row(item)
            if ratio <= 0:
                raise HTTPException(status_code=500, detail="No valid long-short ratio from exchange")
            long_ratio = ratio / (1 + ratio)
            short_ratio = 1 / (1 + ratio)
            return {
                'exchange': exchange_name,
                'symbol': symbol,
                'long_ratio': long_ratio,
                'short_ratio': short_ratio,
                'long_short_ratio': ratio,
                'ratio': ratio,
                'timestamp': ts,
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    raise HTTPException(status_code=501, detail="Not supported for this exchange")


@router.get("/open-interest")
async def get_open_interest(
    exchange_name: str = Query("okx", description="交易所"),
    symbol: str = Query("BTC/USDT:USDT", description="交易对")
):
    """
    获取持仓量
    """
    exchange = exchange_manager.get_exchange(exchange_name)
    if not exchange:
        raise HTTPException(status_code=400, detail="Exchange not supported")

    # OKX v5 public open-interest API
    if exchange_name == 'okx':
        if not (
            hasattr(exchange.exchange, 'publicGetPublicOpenInterest')
            or hasattr(exchange.exchange, 'fapiPublicGetOpenInterest')
        ):
            raise HTTPException(status_code=501, detail="Not supported for this exchange")
        try:
            exchange.load_markets()
            market = exchange.exchange.market(symbol)
            if hasattr(exchange.exchange, 'publicGetPublicOpenInterest'):
                response = exchange.exchange.publicGetPublicOpenInterest({
                    'instType': 'SWAP',
                    'instId': str(market.get('id') or _okx_swap_inst_id(symbol)),
                })
            elif hasattr(exchange.exchange, 'fapiPublicGetOpenInterest'):
                response = exchange.exchange.fapiPublicGetOpenInterest({
                    'symbol': market['id']
                })
            row = first_okx_data_row(response)
            if row is None and isinstance(response, dict):
                row = response
            oi_raw, oi_btc, ts_val = _parse_okx_open_interest_row(row, market)

            return {
                'exchange': exchange_name,
                'symbol': symbol,
                'open_interest': oi_raw,
                'open_interest_btc': oi_btc,
                'timestamp': ts_val,
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    raise HTTPException(status_code=501, detail="Not supported for this exchange")
