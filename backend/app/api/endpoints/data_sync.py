"""
数据同步 API
手动触发同步、查看同步状态、查询可用数据、分表统计
"""
from fastapi import APIRouter, HTTPException, BackgroundTasks, Query
from typing import List, Optional
from pydantic import BaseModel

from app.services.data_sync_service import data_sync_service, DEFAULT_SYMBOLS, DEFAULT_TIMEFRAMES
from app.db.local_db import db_instance as db
from app.services.kline_file_store import kline_store

router = APIRouter()


# ============================================
# 请求模型
# ============================================

class SyncRequest(BaseModel):
    """同步请求"""
    exchange: str = "okx"
    symbols: Optional[List[str]] = None
    timeframes: Optional[List[str]] = None
    history_days: int = 365  # 默认1年
    start_date: Optional[str] = None  # YYYY-MM-DD
    end_date: Optional[str] = None


class SyncSingleRequest(BaseModel):
    """单个交易对同步请求"""
    exchange: str = "okx"
    symbol: str = "BTC/USDT"
    timeframe: str = "1h"
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    history_days: int = 365


# ============================================
# API 端点
# ============================================

@router.post("/start")
async def start_sync(request: SyncRequest, background_tasks: BackgroundTasks):
    """
    启动批量数据同步（后台运行）

    同步指定交易所的多个交易对和时间周期的历史K线数据到本地数据库。
    """
    status = data_sync_service.get_sync_status()
    if status['is_running']:
        raise HTTPException(status_code=409, detail="已有同步任务在运行中")

    async def run_sync():
        await data_sync_service.sync_all(
            exchange_name=request.exchange,
            symbols=request.symbols,
            timeframes=request.timeframes,
            history_days=request.history_days,
            start_date=request.start_date,
            end_date=request.end_date,
        )

    background_tasks.add_task(run_sync)

    return {
        "message": "同步任务已启动",
        "exchange": request.exchange,
        "symbols": request.symbols or DEFAULT_SYMBOLS,
        "timeframes": request.timeframes or DEFAULT_TIMEFRAMES,
        "history_days": request.history_days,
    }


@router.post("/sync_one")
async def sync_single(request: SyncSingleRequest):
    """
    同步单个交易对（同步等待完成）

    适合手动补充某个交易对的数据。
    """
    status = data_sync_service.get_sync_status()
    if status['is_running']:
        raise HTTPException(status_code=409, detail="已有同步任务在运行中")

    result = await data_sync_service.sync_klines(
        exchange_name=request.exchange,
        symbol=request.symbol,
        timeframe=request.timeframe,
        start_date=request.start_date,
        end_date=request.end_date,
        history_days=request.history_days,
    )

    return {
        "exchange": result.exchange,
        "symbol": result.symbol,
        "timeframe": result.timeframe,
        "status": result.status.value,
        "total_fetched": result.total_fetched,
        "total_inserted": result.total_inserted,
        "error": result.error,
        "elapsed_seconds": (
            (result.end_time - result.start_time).total_seconds()
            if result.end_time and result.start_time else None
        ),
    }


@router.get("/status")
async def get_sync_status():
    """
    获取同步服务状态

    返回当前是否在运行、已同步的数据概况等。
    """
    return data_sync_service.get_sync_status()


@router.get("/data")
async def get_available_data(
    exchange: str = Query(None, description="交易所名称")
):
    """
    获取已同步的数据清单

    列出所有已同步到本地的交易对、时间周期、数据量和时间范围。
    """
    return data_sync_service.get_available_data(exchange)


@router.post("/daily_update")
async def trigger_daily_update(
    exchange: str = "okx",
    background_tasks: BackgroundTasks = None
):
    """
    手动触发每日增量更新

    适合在定时任务未触发时手动补数据。
    """
    status = data_sync_service.get_sync_status()
    if status['is_running']:
        raise HTTPException(status_code=409, detail="已有同步任务在运行中")

    async def run_update():
        await data_sync_service.daily_update(exchange)

    background_tasks.add_task(run_update)

    return {
        "message": "每日增量更新已启动",
        "exchange": exchange,
    }


@router.get("/config")
async def get_sync_config():
    """
    获取默认同步配置
    """
    return {
        "default_symbols": DEFAULT_SYMBOLS,
        "default_timeframes": DEFAULT_TIMEFRAMES,
        "default_history_days": 365,
    }


@router.get("/table_stats")
async def get_table_stats():
    """
    获取K线数据的统计信息
    阶段4后：数据存储从 SQLite 迁移到文件系统，这里返回文件存储统计（按 sync_metadata 记录的 key）。
    """
    # 从元数据表枚举已同步的 kline key，再从文件系统聚合统计
    metas = [m for m in db.get_all_sync_metadata() if m.get("data_type") == "kline"]
    stats = []
    for meta in metas:
        exchange = meta.get('exchange')
        symbol = meta.get('symbol')
        timeframe = meta.get('timeframe')
        if not exchange or not symbol or not timeframe:
            continue
        s = kline_store.get_stats(exchange, symbol, timeframe)
        stats.append({
            "exchange": exchange,
            "symbol": symbol,
            "timeframe": timeframe,
            "record_count": s.get("record_count", 0),
            "first_timestamp": s.get("first_timestamp"),
            "last_timestamp": s.get("last_timestamp"),
        })
    return {
        "tables": stats,
        "total_records": sum(s['record_count'] for s in stats),
        "total_pairs": len(set((s['exchange'], s['symbol'], s['timeframe']) for s in stats)),
    }


# ============================================
# 数据资产 & 一键同步 (步骤1)
# ============================================

@router.get("/assets")
async def get_data_assets():
    """
    数据资产全景 — 扫描本地已有的所有交易对/时间周期/时间跨度。
    前端 DataManager 可直接用此接口渲染资产表格。
    """
    metas = [m for m in db.get_all_sync_metadata() if m.get("data_type") == "kline"]
    assets = []
    for meta in metas:
        exchange = meta.get("exchange")
        symbol = meta.get("symbol")
        timeframe = meta.get("timeframe")
        if not all([exchange, symbol, timeframe]):
            continue
        s = kline_store.get_stats(exchange, symbol, timeframe)
        first_ts = s.get("first_timestamp")
        last_ts = s.get("last_timestamp")
        assets.append({
            "exchange": exchange,
            "symbol": symbol,
            "timeframe": timeframe,
            "record_count": s.get("record_count", 0),
            "first_date": _ts_to_date(first_ts),
            "last_date": _ts_to_date(last_ts),
            "first_timestamp": first_ts,
            "last_timestamp": last_ts,
        })
    assets.sort(key=lambda a: (a["symbol"], a["timeframe"]))
    return {
        "assets": assets,
        "total_records": sum(a["record_count"] for a in assets),
        "total_pairs": len(set(a["symbol"] for a in assets)),
        "total_items": len(assets),
    }


def _ts_to_date(ts):
    if not ts:
        return None
    try:
        from datetime import datetime, timezone
        return datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
    except Exception:
        return None


class QuickSyncRequest(BaseModel):
    exchange: str = "okx"
    symbol: str = "BTC/USDT"
    timeframe: str = "1h"
    history_days: int = 180


_sync_tasks: dict = {}


def _has_running_quick_sync(exclude_key: str | None = None) -> bool:
    return any(
        key != exclude_key and bool(info.get("running"))
        for key, info in _sync_tasks.items()
    )


@router.post("/sync")
async def quick_sync(request: QuickSyncRequest, background_tasks: BackgroundTasks):
    """
    一键后台同步 — 返回任务 ID，前端可轮询 /data_sync/status 查看进度。
    """
    import uuid
    task_id = str(uuid.uuid4())[:8]
    key = f"{request.exchange}:{request.symbol}:{request.timeframe}"

    if key in _sync_tasks and _sync_tasks[key].get("running"):
        return {"task_id": _sync_tasks[key]["task_id"], "message": "该数据已在同步中", "duplicate": True}
    if data_sync_service.get_sync_status().get("is_running") or _has_running_quick_sync(exclude_key=key):
        raise HTTPException(status_code=409, detail="已有同步任务在运行中，请稍后再试")

    _sync_tasks[key] = {"task_id": task_id, "running": True, "result": None}

    async def _run():
        try:
            result = await data_sync_service.sync_klines(
                exchange_name=request.exchange,
                symbol=request.symbol,
                timeframe=request.timeframe,
                history_days=request.history_days,
            )
            _sync_tasks[key] = {
                "task_id": task_id, "running": False,
                "result": {
                    "status": result.status.value,
                    "total_fetched": result.total_fetched,
                    "total_inserted": result.total_inserted,
                    "error": result.error,
                },
            }
        except Exception as e:
            _sync_tasks[key] = {"task_id": task_id, "running": False, "result": {"status": "error", "error": str(e)}}

    background_tasks.add_task(_run)
    return {"task_id": task_id, "message": "同步任务已启动", "key": key}


@router.get("/sync_task/{task_id}")
async def get_sync_task(task_id: str):
    """查询一键同步任务状态"""
    for key, info in _sync_tasks.items():
        if info.get("task_id") == task_id:
            return {"task_id": task_id, "key": key, **info}
    return {"task_id": task_id, "running": False, "result": None, "message": "任务不存在"}


class DeleteDataRequest(BaseModel):
    """删除数据请求"""
    exchange: str = "okx"
    symbol: Optional[str] = None  # 不传则删除该交易所全部
    timeframe: Optional[str] = None  # 不传则删除该交易对全部周期


@router.post("/delete")
async def delete_kline_data(request: DeleteDataRequest):
    """
    删除指定交易对/周期的K线数据
    """
    if not request.symbol:
        raise HTTPException(status_code=400, detail="阶段4后仅支持按 symbol 删除文件数据（避免误删整库）")

    deleted_files = kline_store.delete(request.exchange, request.symbol, request.timeframe)

    return {
        "message": f"已删除 {deleted_files} 个K线数据文件",
        "deleted_files": deleted_files,
    }
