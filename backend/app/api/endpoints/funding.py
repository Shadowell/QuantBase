"""
资金费率 API
"""
from fastapi import APIRouter, HTTPException, Query
from typing import List, Optional
from app.models.schemas import FundingRate, FundingRateHistory, FundingOpportunity
from app.services.funding_service import funding_service

router = APIRouter()


@router.get("/rates", response_model=List[FundingRate])
async def get_funding_rates(
    exchange: str = Query("okx", description="交易所"),
    symbols: Optional[str] = Query(None, description="交易对列表，逗号分隔")
):
    """
    获取资金费率列表
    """
    try:
        symbol_list = symbols.split(",") if symbols else None
        rates = await funding_service.get_funding_rates(exchange, symbol_list)
        return rates
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/rate/{symbol}", response_model=FundingRate)
async def get_funding_rate(
    symbol: str,
    exchange: str = Query("okx", description="交易所")
):
    """
    获取单个交易对资金费率详情
    """
    try:
        rate = await funding_service.get_funding_rate(exchange, symbol)
        if not rate:
            raise HTTPException(status_code=404, detail="Symbol not found")
        return rate
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/history", response_model=List[FundingRateHistory])
async def get_funding_history(
    exchange: str = Query(..., description="交易所"),
    symbol: str = Query(..., description="交易对"),
    limit: int = Query(100, ge=1, le=500, description="数量限制")
):
    """
    获取资金费率历史
    """
    try:
        history = await funding_service.get_funding_history(exchange, symbol, limit)
        return history
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/opportunities", response_model=List[FundingOpportunity])
async def get_funding_opportunities(
    exchange: str = Query("okx", description="交易所"),
    min_rate: float = Query(0.0001, description="最低费率阈值"),
    limit: int = Query(20, ge=1, le=50, description="返回数量")
):
    """
    获取资金费率套利机会（按费率排序）
    """
    try:
        opportunities = await funding_service.get_opportunities(exchange, min_rate, limit)
        return opportunities
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/summary")
async def get_funding_summary():
    """
    获取资金费率汇总（多交易所）
    """
    try:
        summary = await funding_service.get_summary()
        return summary
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
