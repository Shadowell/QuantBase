"""Binance USD-M public data adapter for cross-exchange research."""
from __future__ import annotations

import asyncio
import math
from typing import Any, Dict, List, Optional

import httpx


class BinanceUsdmPublicClient:
    """Small public-only Binance USD-M client.

    The arbitrage center only needs unauthenticated market data in this slice,
    so this adapter deliberately does not accept or sign private credentials.
    """

    def __init__(self, base_url: str = "https://fapi.binance.com", timeout_sec: float = 8.0):
        self.base_url = base_url.rstrip("/")
        self.timeout_sec = timeout_sec

    async def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        async with httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout_sec) as client:
            response = await client.get(path, params=params)
            response.raise_for_status()
            return response.json()

    async def fetch_snapshots(self) -> Dict[str, Dict[str, Any]]:
        ticker_rows, premium_rows = await asyncio.gather(
            self._get("/fapi/v1/ticker/24hr"),
            self._get("/fapi/v1/premiumIndex"),
        )
        tickers = {
            symbol: row
            for row in (self._normalize_ticker(row) for row in ticker_rows if isinstance(row, dict))
            if row and (symbol := row.get("symbol"))
        }
        for row in premium_rows if isinstance(premium_rows, list) else [premium_rows]:
            premium = self._normalize_premium(row)
            symbol = premium.get("symbol") if premium else None
            if not symbol:
                continue
            tickers.setdefault(symbol, {"symbol": symbol}).update(premium)
        return tickers

    async def fetch_orderbook(self, symbol: str, limit: int = 20) -> Dict[str, Any]:
        native_symbol = self.to_native_symbol(symbol)
        payload = await self._get("/fapi/v1/depth", {"symbol": native_symbol, "limit": max(5, min(int(limit), 100))})
        return {
            "exchange": "binanceusdm",
            "symbol": symbol,
            "bids": self._price_levels(payload.get("bids")),
            "asks": self._price_levels(payload.get("asks")),
            "timestamp": payload.get("E") or payload.get("T"),
        }

    @classmethod
    def to_unified_symbol(cls, symbol: str) -> Optional[str]:
        raw = str(symbol or "").strip().upper()
        if not raw.endswith("USDT") or "_" in raw:
            return None
        base = raw[:-4]
        if not base:
            return None
        return f"{base}/USDT:USDT"

    @classmethod
    def to_native_symbol(cls, symbol: str) -> str:
        raw = str(symbol or "").strip().upper()
        if "/" not in raw:
            return raw
        base = raw.split("/", 1)[0]
        return f"{base}USDT"

    @classmethod
    def _normalize_ticker(cls, row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        symbol = cls.to_unified_symbol(row.get("symbol"))
        if not symbol:
            return None
        last = cls._finite_float(row.get("lastPrice"))
        quote_volume = cls._finite_float(row.get("quoteVolume"))
        return {
            "exchange": "binanceusdm",
            "symbol": symbol,
            "last": last,
            "bid": cls._finite_float(row.get("bidPrice")),
            "ask": cls._finite_float(row.get("askPrice")),
            "high": cls._finite_float(row.get("highPrice")),
            "low": cls._finite_float(row.get("lowPrice")),
            "volume": cls._finite_float(row.get("volume")),
            "quote_volume": quote_volume,
            "change_percent": cls._finite_float(row.get("priceChangePercent")),
            "timestamp": row.get("closeTime"),
        }

    @classmethod
    def _normalize_premium(cls, row: Any) -> Optional[Dict[str, Any]]:
        if not isinstance(row, dict):
            return None
        symbol = cls.to_unified_symbol(row.get("symbol"))
        if not symbol:
            return None
        return {
            "exchange": "binanceusdm",
            "symbol": symbol,
            "mark_price": cls._finite_float(row.get("markPrice")),
            "index_price": cls._finite_float(row.get("indexPrice")),
            "funding_rate": cls._finite_float(row.get("lastFundingRate")),
            "next_funding_time": row.get("nextFundingTime"),
            "timestamp": row.get("time"),
        }

    @staticmethod
    def _price_levels(rows: Any) -> List[List[float]]:
        levels: List[List[float]] = []
        if not isinstance(rows, list):
            return levels
        for row in rows:
            if not isinstance(row, (list, tuple)) or len(row) < 2:
                continue
            price = BinanceUsdmPublicClient._finite_float(row[0])
            amount = BinanceUsdmPublicClient._finite_float(row[1])
            if price is not None and amount is not None and price > 0 and amount > 0:
                levels.append([price, amount])
        return levels

    @staticmethod
    def _finite_float(value: Any) -> Optional[float]:
        if value in (None, ""):
            return None
        try:
            out = float(value)
        except (TypeError, ValueError):
            return None
        return out if math.isfinite(out) else None
