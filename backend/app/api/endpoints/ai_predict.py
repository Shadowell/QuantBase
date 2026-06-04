"""
AI 预测 API — Kairos 时序大模型推理 + 可视化
=====================================================================

端点：
- POST /predict          执行一次预测（返回轨迹 + 分数）
- GET  /chart            获取"真实 vs 预测"ECharts 配置
- GET  /history          查询历史预测记录
- GET  /status           模型状态
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.services.kairos_predictor import kairos_predictor, timeframe_to_minutes
from app.services.visualization_service import build_prediction_chart, build_multi_prediction_chart
from app.services.ai_prediction_service import ai_prediction_service

logger = logging.getLogger(__name__)
router = APIRouter()


# ============================================
# 请求 / 响应模型
# ============================================

class PredictRequest(BaseModel):
    """预测请求"""
    exchange: str = "okx"
    symbol: str = "BTC/USDT"
    timeframe: str = "1m"
    lookback: int = 300


class PredictResponse(BaseModel):
    """预测响应"""
    score: float
    direction: str
    confidence: float
    current_close: float
    predicted_prices: List[float]
    predicted_timestamps: List[int]
    is_mock: bool
    timestamp_ms: int


class ChartRequest(BaseModel):
    """图表请求"""
    exchange: str = "okx"
    symbol: str = "BTC/USDT"
    timeframe: str = "1m"
    lookback: int = 300
    show_history: bool = False


# ============================================
# API 端点
# ============================================

@router.post("/predict", response_model=PredictResponse)
async def run_prediction(req: PredictRequest):
    """
    执行一次 AI 预测。

    自动从本地 K 线缓存获取最近 lookback 根数据，
    调用 Kairos 模型推理，返回未来 30 根 K 线的价格轨迹和多空分数。
    """
    bars = await _fetch_recent_klines(req.exchange, req.symbol, req.timeframe, req.lookback)
    if len(bars) < 50:
        raise HTTPException(status_code=400, detail=f"K 线数据不足（需要至少 50 根，当前 {len(bars)}）")

    result = await kairos_predictor.predict_trajectory(
        ohlcv_bars=bars,
        timeframe_minutes=timeframe_to_minutes(req.timeframe),
    )

    persist_bars: List[Dict[str, Any]] = []
    for i, ts in enumerate(result.predicted_timestamps):
        ohlc = result.predicted_ohlcv[i] if i < len(result.predicted_ohlcv) else {}
        pc = result.predicted_prices[i] if i < len(result.predicted_prices) else float(ohlc.get("close", 0))
        persist_bars.append(
            {
                "timestamp": int(ts),
                "open": float(ohlc.get("open", pc)),
                "high": float(ohlc.get("high", pc)),
                "low": float(ohlc.get("low", pc)),
                "close": float(ohlc.get("close", pc)),
            }
        )
    ai_prediction_service.schedule_persist_predicted_bars(
        req.exchange, req.symbol, req.timeframe, persist_bars, result.timestamp_ms
    )

    return PredictResponse(
        score=result.score,
        direction=result.direction,
        confidence=result.confidence,
        current_close=result.current_close,
        predicted_prices=result.predicted_prices,
        predicted_timestamps=result.predicted_timestamps,
        is_mock=result.is_mock,
        timestamp_ms=result.timestamp_ms,
    )


@router.post("/chart")
async def get_prediction_chart(req: ChartRequest):
    """
    获取"真实 K 线 vs AI 预测"的完整 ECharts 配置。

    返回值可直接传给前端 `chart.setOption(data)`。
    """
    bars = await _fetch_recent_klines(req.exchange, req.symbol, req.timeframe, req.lookback)
    if len(bars) < 50:
        raise HTTPException(status_code=400, detail=f"K 线数据不足")

    result = await kairos_predictor.predict_trajectory(
        ohlcv_bars=bars,
        timeframe_minutes=timeframe_to_minutes(req.timeframe),
    )

    if req.show_history:
        predictions = kairos_predictor.store.get_all()
        option = build_multi_prediction_chart(
            real_klines=bars[-200:],
            predictions=predictions,
            symbol=req.symbol,
            timeframe=req.timeframe,
        )
    else:
        option = build_prediction_chart(
            real_klines=bars[-200:],
            prediction=result.to_dict(),
            symbol=req.symbol,
            timeframe=req.timeframe,
        )

    return option


@router.get("/history")
async def get_prediction_history(limit: int = Query(20, ge=1, le=200)):
    """获取最近 N 条预测记录。"""
    records = kairos_predictor.store.get_latest(limit)
    return {"total": len(records), "records": records}


@router.get("/status")
async def get_model_status():
    """获取模型加载状态。"""
    return {
        "loaded": kairos_predictor.is_loaded,
        "mock_mode": kairos_predictor.is_mock,
        "load_error": kairos_predictor._load_error,
        "device": kairos_predictor._device,
        "model_id": "Shadowell/Kairos-base-crypto",
        "tokenizer_id": "NeoQuasar/Kronos-Tokenizer-base",
        "lookback": 256,
        "pred_len": 30,
        "history_count": len(kairos_predictor.store.get_all()),
    }


@router.post("/analyze")
async def analyze_prediction(req: PredictRequest):
    """
    AI 分析 — 预测 + 人话解读。

    结合 K 线指标和 AI 预测分数，调用 LLM 生成简短分析摘要，
    包含形势判断、预测解读和操作建议。
    """
    from app.services.ai_prediction_service import ai_prediction_service

    bars = await _fetch_recent_klines(req.exchange, req.symbol, req.timeframe, req.lookback)
    if len(bars) < 20:
        raise HTTPException(status_code=400, detail="K 线数据不足")

    predicted = await ai_prediction_service.predict(
        bars,
        req.timeframe,
        steps=30,
        exchange=req.exchange,
        symbol=req.symbol,
    )
    analysis = await ai_prediction_service.analyze(
        symbol=req.symbol,
        timeframe=req.timeframe,
        history=bars,
        predicted=predicted,
    )

    # 推送到飞书
    try:
        from app.services.feishu_notifier import feishu_notifier
        await feishu_notifier.notify_ai_signal(
            symbol=req.symbol,
            direction="看涨" if any(p.get("close", 0) > bars[-1].get("close", 0) for p in predicted[:1]) else "看跌",
            confidence=sum(p.get("confidence", 0) for p in predicted) / max(len(predicted), 1),
            analysis=analysis,
        )
    except Exception:
        pass

    return {
        "symbol": req.symbol,
        "timeframe": req.timeframe,
        "analysis": analysis,
        "predicted_bars": predicted,
    }


# ============================================
# 内部工具
# ============================================

async def _fetch_recent_klines(
    exchange: str, symbol: str, timeframe: str, limit: int,
) -> List[Dict[str, Any]]:
    """
    获取最近 N 根 K 线数据。

    优先从本地文件缓存读取；不足时通过 CCXT 实时拉取。
    """
    try:
        from app.services.kline_file_store import kline_store
        import time

        now_ms = int(time.time() * 1000)
        tf_ms = timeframe_to_minutes(timeframe) * 60_000
        start_ms = now_ms - limit * tf_ms

        df = kline_store.read_dataframe(exchange, symbol, timeframe, start_ms=start_ms, end_ms=now_ms)
        if not df.empty and len(df) >= limit * 0.5:
            bars = df.to_dict("records")
            return bars[-limit:]
    except Exception as e:
        logger.debug("本地缓存读取失败: %s", e)

    # 降级：通过交易所实时拉取
    try:
        from app.exchange import exchange_manager

        ex = exchange_manager.get_exchange(exchange)
        if ex:
            raw = await asyncio.to_thread(
                ex.fetch_ohlcv, symbol, timeframe, limit=min(limit, 300),
            )
            return raw
    except Exception as e:
        logger.warning("交易所 K 线拉取失败: %s", e)

    return []
