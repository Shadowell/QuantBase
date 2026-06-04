"""
File-system based K-line store.

Motivation:
- SQLite will lock heavily with millions of OHLCV rows.
- For solo quant "sleep-after" stability, keep the DB for light state only and
  move large time-series data to append-friendly files.

Storage layout (preferred Parquet, fallback CSV):
data/klines/{exchange}/{symbol}/{timeframe}/YYYYMM.(parquet|csv)

Where symbol is sanitized for filesystem safety.
"""

from __future__ import annotations

import os
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import logging

import pandas as pd

logger = logging.getLogger(__name__)


def _sanitize_symbol(symbol: str) -> str:
    # BTC/USDT -> BTC-USDT, BTC/USDT:USDT -> BTC-USDT_USDT
    return symbol.replace("/", "-").replace(":", "_")


@dataclass(frozen=True)
class KlineStoreConfig:
    root_dir: Path
    fmt: str = "parquet"  # "parquet" | "csv"


class KlineFileStore:
    def __init__(self, config: Optional[KlineStoreConfig] = None):
        project_root = Path(__file__).resolve().parents[3]
        default_root = project_root / "data" / "klines"
        self.config = config or KlineStoreConfig(root_dir=default_root, fmt=os.getenv("KLINE_STORE_FMT", "parquet"))

    def _dir(self, exchange: str, symbol: str, timeframe: str) -> Path:
        return self.config.root_dir / exchange / _sanitize_symbol(symbol) / timeframe

    def _partition_key(self, ts_ms: int) -> str:
        dt = datetime.fromtimestamp(ts_ms / 1000)
        return f"{dt.year}{dt.month:02d}"

    def _filter_partition_files_by_range(
        self,
        files: List[Path],
        *,
        start_ms: Optional[int],
        end_ms: Optional[int],
    ) -> List[Path]:
        if start_ms is None and end_ms is None:
            return files

        start_part = self._partition_key(int(start_ms)) if start_ms is not None else None
        end_part = self._partition_key(int(end_ms)) if end_ms is not None else None
        return [
            fp
            for fp in files
            if (start_part is None or fp.stem >= start_part)
            and (end_part is None or fp.stem <= end_part)
        ]

    def _file_path(self, exchange: str, symbol: str, timeframe: str, part: str) -> Path:
        ext = "parquet" if self.config.fmt == "parquet" else "csv"
        return self._dir(exchange, symbol, timeframe) / f"{part}.{ext}"

    def _ensure_dir(self, path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)

    def append_klines(self, exchange: str, symbol: str, timeframe: str, klines: List[Dict]) -> int:
        if not klines:
            return 0

        df = pd.DataFrame(klines)
        required_cols = ["timestamp", "open", "high", "low", "close", "volume"]
        missing = [c for c in required_cols if c not in df.columns]
        if missing:
            raise ValueError(f"Missing kline columns: {missing}")

        # Ensure types
        df["timestamp"] = df["timestamp"].astype("int64")
        df = df.sort_values("timestamp").drop_duplicates(subset=["timestamp"], keep="last")

        self._ensure_dir(self._dir(exchange, symbol, timeframe))

        inserted_total = 0
        for part, part_df in df.groupby(df["timestamp"].map(self._partition_key)):
            file_path = self._file_path(exchange, symbol, timeframe, str(part))
            inserted_total += self._append_partition(file_path, part_df)

        return inserted_total

    def _append_partition(self, file_path: Path, df: pd.DataFrame) -> int:
        if df.empty:
            return 0

        if self.config.fmt == "parquet":
            return self._append_parquet(file_path, df)
        return self._append_csv(file_path, df)

    def _append_parquet(self, file_path: Path, df: pd.DataFrame) -> int:
        try:
            import pyarrow  # noqa: F401
        except Exception as exc:
            logger.warning(f"pyarrow unavailable, falling back to csv: {exc}")
            self.config = KlineStoreConfig(root_dir=self.config.root_dir, fmt="csv")
            csv_path = file_path.with_suffix(".csv")
            return self._append_csv(csv_path, df)

        if file_path.exists():
            try:
                old = pd.read_parquet(file_path)
            except Exception as exc:
                corrupt_path = self._quarantine_corrupt_file(file_path)
                logger.error(
                    "Corrupt parquet partition quarantined: %s -> %s: %s",
                    file_path,
                    corrupt_path,
                    exc,
                )
                old = pd.DataFrame()
            merged = pd.concat([old, df], ignore_index=True)
            merged = merged.sort_values("timestamp").drop_duplicates(subset=["timestamp"], keep="last")
            before = len(old)
            self._write_parquet_atomic(file_path, merged)
            return max(0, len(merged) - before)

        self._write_parquet_atomic(file_path, df)
        return len(df)

    def _write_parquet_atomic(self, file_path: Path, df: pd.DataFrame) -> None:
        self._ensure_dir(file_path.parent)
        tmp_path = file_path.with_name(f".{file_path.name}.{uuid.uuid4().hex}.tmp")
        try:
            df.to_parquet(tmp_path, index=False)
            tmp_path.replace(file_path)
        finally:
            tmp_path.unlink(missing_ok=True)

    def _quarantine_corrupt_file(self, file_path: Path) -> Path:
        suffix = f"corrupt-{int(time.time())}-{uuid.uuid4().hex[:8]}"
        corrupt_path = file_path.with_name(f"{file_path.name}.{suffix}")
        file_path.replace(corrupt_path)
        return corrupt_path

    def _append_csv(self, file_path: Path, df: pd.DataFrame) -> int:
        if file_path.exists():
            old = pd.read_csv(file_path)
            merged = pd.concat([old, df], ignore_index=True)
            merged = merged.sort_values("timestamp").drop_duplicates(subset=["timestamp"], keep="last")
            before = len(old)
            merged.to_csv(file_path, index=False)
            return max(0, len(merged) - before)

        df.to_csv(file_path, index=False)
        return len(df)

    def read_klines(
        self,
        exchange: str,
        symbol: str,
        timeframe: str,
        *,
        start_ms: Optional[int] = None,
        end_ms: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> List[Dict]:
        if start_ms is None and end_ms is None and limit is not None:
            df = self._read_recent_dataframe(exchange, symbol, timeframe, int(limit))
            if df.empty:
                return []
            return df.to_dict(orient="records")

        df = self.read_dataframe(exchange, symbol, timeframe, start_ms=start_ms, end_ms=end_ms)
        if df.empty:
            return []

        if limit is not None:
            df = df.tail(int(limit))

        return df.to_dict(orient="records")

    def _read_file(self, fp: Path) -> pd.DataFrame:
        if fp.suffix == ".parquet":
            return pd.read_parquet(fp)
        return pd.read_csv(fp)

    def _normalize_dataframe(self, frames: List[pd.DataFrame]) -> pd.DataFrame:
        if not frames:
            return pd.DataFrame()

        df = pd.concat(frames, ignore_index=True)
        if "timestamp" not in df.columns:
            return pd.DataFrame()

        df["timestamp"] = df["timestamp"].astype("int64")
        return df.sort_values("timestamp").drop_duplicates(subset=["timestamp"], keep="last")

    def _read_recent_dataframe(self, exchange: str, symbol: str, timeframe: str, limit: int) -> pd.DataFrame:
        base = self._dir(exchange, symbol, timeframe)
        if not base.exists():
            return pd.DataFrame()

        files = sorted(
            [*base.glob("*.parquet"), *base.glob("*.csv")],
            key=lambda fp: fp.stem,
            reverse=True,
        )
        if not files:
            return pd.DataFrame()

        frames: List[pd.DataFrame] = []
        for fp in files:
            try:
                frames.append(self._read_file(fp))
            except Exception as exc:
                logger.warning(f"Failed to read {fp}: {exc}")
                continue

            df = self._normalize_dataframe(frames)
            if len(df) >= limit:
                return df.tail(limit)

        df = self._normalize_dataframe(frames)
        if df.empty:
            return df
        return df.tail(limit)

    def read_dataframe(
        self,
        exchange: str,
        symbol: str,
        timeframe: str,
        *,
        start_ms: Optional[int] = None,
        end_ms: Optional[int] = None,
    ) -> pd.DataFrame:
        base = self._dir(exchange, symbol, timeframe)
        if not base.exists():
            return pd.DataFrame()

        files = sorted(base.glob("*.parquet")) + sorted(base.glob("*.csv"))
        files = self._filter_partition_files_by_range(files, start_ms=start_ms, end_ms=end_ms)
        if not files:
            return pd.DataFrame()

        frames: List[pd.DataFrame] = []
        for fp in files:
            try:
                frames.append(self._read_file(fp))
            except Exception as exc:
                logger.warning(f"Failed to read {fp}: {exc}")

        df = self._normalize_dataframe(frames)
        if df.empty:
            return df

        if start_ms is not None:
            df = df[df["timestamp"] >= int(start_ms)]
        if end_ms is not None:
            df = df[df["timestamp"] <= int(end_ms)]

        return df

    def get_stats(self, exchange: str, symbol: str, timeframe: str) -> Dict:
        df = self.read_dataframe(exchange, symbol, timeframe)
        if df.empty:
            return {"record_count": 0, "first_timestamp": None, "last_timestamp": None}
        return {
            "record_count": int(len(df)),
            "first_timestamp": int(df["timestamp"].min()),
            "last_timestamp": int(df["timestamp"].max()),
        }

    def delete(self, exchange: str, symbol: str, timeframe: Optional[str] = None) -> int:
        """
        Delete stored files. Returns number of deleted files.
        """
        deleted = 0
        base = self.config.root_dir / exchange / _sanitize_symbol(symbol)
        if timeframe:
            base = base / timeframe
        if not base.exists():
            return 0

        for fp in base.rglob("*.parquet"):
            fp.unlink(missing_ok=True)
            deleted += 1
        for fp in base.rglob("*.csv"):
            fp.unlink(missing_ok=True)
            deleted += 1

        return deleted


kline_store = KlineFileStore()
