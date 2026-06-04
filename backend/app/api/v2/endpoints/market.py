"""Market endpoints for API v2."""
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query

from app.core.contracts import ok, page_meta
from app.domain.market import market_domain_service
from app.services.ai_prediction_service import ai_prediction_service

router = APIRouter()


def _parse_periods(raw: str, param_name: str = "ema_periods") -> List[int]:
    periods: List[int] = []
    for part in raw.split(","):
        value = part.strip()
        if not value:
            continue
        try:
            period = int(value)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"{param_name} must be comma-separated integers") from exc
        if period <= 0 or period > 500:
            raise HTTPException(status_code=400, detail="indicator period must be between 1 and 500")
        periods.append(period)
    return periods or [5, 10, 20, 30]


@router.get("/ticker")
async def get_ticker(
    exchange: str = Query(..., description="交易所"),
    symbol: str = Query(..., description="交易对"),
):
    return ok(await market_domain_service.get_ticker(exchange, symbol))


@router.get("/tickers")
async def get_tickers(
    exchange: str = Query(..., description="交易所"),
    symbols: Optional[str] = Query(None, description="逗号分隔交易对"),
    offset: int = Query(0, ge=0),
    limit: int = Query(200, ge=1, le=500),
):
    symbol_list = [s.strip() for s in symbols.split(",") if s.strip()] if symbols else None
    items = await market_domain_service.get_tickers(exchange, symbol_list)
    total = len(items)
    paged = items[offset: offset + limit]
    return ok(paged, meta=page_meta(total=total, offset=offset, limit=limit))


@router.get("/klines")
async def get_klines(
    exchange: str = Query(..., description="交易所"),
    symbol: str = Query(..., description="交易对"),
    timeframe: str = Query("1h", description="周期"),
    limit: int = Query(100, ge=1, le=1000),
    start: Optional[int] = Query(None, description="开始时间戳(毫秒)"),
    end: Optional[int] = Query(None, description="结束时间戳(毫秒)"),
    predict: bool = Query(False, description="是否附加 AI 预测 K 线"),
    predict_steps: int = Query(30, ge=1, le=30, description="预测 K 线根数"),
):
    klines = await market_domain_service.get_klines(exchange, symbol, timeframe, limit, start, end)

    if not predict:
        return ok(klines)

    predicted_bars = await ai_prediction_service.predict(
        klines,
        timeframe,
        steps=predict_steps,
        exchange=exchange,
        symbol=symbol,
    )
    ai_prediction_service.schedule_persist_predicted_bars(
        exchange, symbol, timeframe, predicted_bars
    )
    return ok({
        "klines": klines,
        "predicted_bars": predicted_bars,
    })


@router.get("/indicators")
async def get_technical_indicators(
    exchange: str = Query(..., description="交易所"),
    symbol: str = Query(..., description="交易对"),
    timeframe: str = Query("1h", description="周期"),
    limit: int = Query(100, ge=1, le=1000),
    start: Optional[int] = Query(None, description="开始时间戳(毫秒)"),
    end: Optional[int] = Query(None, description="结束时间戳(毫秒)"),
    ema_periods: Optional[str] = Query(None, description="逗号分隔 EMA 周期"),
    ma_periods: Optional[str] = Query(None, description="兼容旧参数：逗号分隔周期，仍返回 EMA 序列"),
):
    period_param = ema_periods or ma_periods or "5,10,20,30"
    periods = _parse_periods(period_param, "ema_periods" if ema_periods or not ma_periods else "ma_periods")
    payload = await market_domain_service.get_technical_indicators(
        exchange,
        symbol,
        timeframe,
        limit,
        start,
        end,
        ema_periods=periods,
    )
    return ok(payload)


@router.get("/predictions/compare")
async def predictions_compare(
    exchange: str = Query(..., description="交易所"),
    symbol: str = Query(..., description="交易对"),
    timeframe: str = Query(..., description="周期"),
    start_time: int = Query(..., description="窗口起始时间戳(毫秒)"),
    end_time: int = Query(..., description="窗口结束时间戳(毫秒)"),
    predict_steps: int = Query(30, ge=1, le=30, description="未来预测根数"),
):
    """
    真实 K 线 + 历史已保存预测（去重）+ 最新未来预测。
    未来预测每次请求会异步落库，便于后续复盘。
    """
    payload = await ai_prediction_service.fetch_prediction_compare(
        exchange,
        symbol,
        timeframe,
        start_time,
        end_time,
        predict_steps=predict_steps,
    )
    return ok(payload)


@router.get("/orderbook")
async def get_orderbook(
    exchange: str = Query(..., description="交易所"),
    symbol: str = Query(..., description="交易对"),
    limit: int = Query(20, ge=1, le=1000),
):
    return ok(await market_domain_service.get_orderbook(exchange, symbol, limit))


@router.get("/trades")
async def get_trades(
    exchange: str = Query(..., description="交易所"),
    symbol: str = Query(..., description="交易对"),
    limit: int = Query(50, ge=1, le=500),
):
    return ok(await market_domain_service.get_trades(exchange, symbol, limit))


@router.get("/symbols")
async def get_symbols(
    exchange: str = Query(..., description="交易所"),
    quote: str = Query("USDT", description="计价币种"),
    market_type: str = Query("spot", description="市场类型: spot/swap/future/all"),
):
    symbols = await market_domain_service.get_symbols(exchange, quote, market_type)
    return ok({"symbols": symbols})
