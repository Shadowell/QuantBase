"""
策略管理 API
"""
from fastapi import APIRouter, HTTPException, Query
from typing import List
from app.models.schemas import Strategy, StrategyCreate, StrategyUpdate, StrategyTrade
from app.services.strategy_service import strategy_service

router = APIRouter()


@router.get("/list", response_model=List[Strategy])
async def get_strategies():
    """
    获取策略列表
    """
    try:
        strategies = await strategy_service.get_strategies()
        return strategies
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{strategy_id}", response_model=Strategy)
async def get_strategy(strategy_id: int):
    """
    获取策略详情
    """
    try:
        strategy = await strategy_service.get_strategy(strategy_id)
        if not strategy:
            raise HTTPException(status_code=404, detail="Strategy not found")
        return strategy
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/create", response_model=Strategy)
async def create_strategy(strategy: StrategyCreate):
    """
    创建策略
    """
    try:
        created = await strategy_service.create_strategy(strategy)
        return created
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/{strategy_id}", response_model=Strategy)
async def update_strategy(strategy_id: int, strategy: StrategyUpdate):
    """
    更新策略
    """
    try:
        updated = await strategy_service.update_strategy(strategy_id, strategy)
        if not updated:
            raise HTTPException(status_code=404, detail="Strategy not found")
        return updated
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{strategy_id}")
async def delete_strategy(strategy_id: int):
    """
    删除策略
    """
    try:
        success = await strategy_service.delete_strategy(strategy_id)
        if not success:
            raise HTTPException(status_code=404, detail="Strategy not found")
        return {"message": "Strategy deleted"}
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{strategy_id}/start")
async def start_strategy(strategy_id: int):
    """
    启动策略
    """
    try:
        success = await strategy_service.start_strategy(strategy_id)
        if not success:
            raise HTTPException(status_code=400, detail="Failed to start strategy")
        return {"message": "Strategy started"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{strategy_id}/stop")
async def stop_strategy(strategy_id: int):
    """
    停止策略
    """
    try:
        success = await strategy_service.stop_strategy(strategy_id)
        if not success:
            raise HTTPException(status_code=400, detail="Failed to stop strategy")
        return {"message": "Strategy stopped"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{strategy_id}/trades", response_model=List[StrategyTrade])
async def get_strategy_trades(
    strategy_id: int,
    limit: int = Query(50, ge=1, le=500)
):
    """
    获取策略交易记录
    """
    try:
        trades = await strategy_service.get_strategy_trades(strategy_id, limit)
        return trades
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{strategy_id}/status")
async def get_strategy_status(strategy_id: int):
    """
    获取策略运行状态
    """
    try:
        status = await strategy_service.get_strategy_status(strategy_id)
        if not status:
            raise HTTPException(status_code=404, detail="Strategy not found")
        return status
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
