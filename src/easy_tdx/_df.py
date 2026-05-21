"""Dataclass → DataFrame 转换工具。"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any

import pandas as pd


def _to_df(data: Any) -> pd.DataFrame:
    """将 list[dataclass] 或单个 dataclass 转为 DataFrame。

    自动丢弃以 ``_`` 开头的内部字段（如 ``_raw``）。
    仅处理 year/month/day（无 hour/minute）→ date 的合并；
    SecurityBar 的完整 datetime 合并由调用方按周期决定。
    """
    if isinstance(data, list):
        if not data:
            return pd.DataFrame()
        rows = []
        for item in data:
            d = _clean_dict(item)
            rows.append(d)
        return pd.DataFrame(rows)
    if is_dataclass(data) and not isinstance(data, type):
        return pd.DataFrame([_clean_dict(data)])
    raise TypeError(f"不支持转换为 DataFrame 的类型: {type(data)}")


def _clean_dict(item: Any) -> dict[str, Any]:
    d = asdict(item)
    d = {k: v for k, v in d.items() if not k.startswith("_")}
    return _merge_datetime_fields(d)


def _merge_datetime_fields(d: dict[str, Any]) -> dict[str, Any]:
    """将仅含 year/month/day（无 hour/minute）的模型合并为 date 列。"""
    if all(k in d for k in ("year", "month", "day")) and not all(
        k in d for k in ("hour", "minute")
    ):
        dt = pd.Timestamp(year=d["year"], month=d["month"], day=d["day"])
        result: dict[str, Any] = {"date": dt}
        result.update({k: v for k, v in d.items() if k not in {"year", "month", "day"}})
        return result
    return d


def _merge_bar_datetime(df: pd.DataFrame, daily_plus: bool) -> pd.DataFrame:
    """根据 K 线周期将 SecurityBar 的分散字段合并为 date 或 datetime。

    Args:
        daily_plus: True 表示日线及以上周期（DAY/WEEK/MONTH/YEAR），只保留 date；
                    False 表示分钟线（MIN_1/5/15/30/60），保留完整 datetime。
    """
    if df.empty or "year" not in df.columns:
        return df
    date_str = (
        df["year"].astype(str)
        + "-"
        + df["month"].astype(str).str.zfill(2)
        + "-"
        + df["day"].astype(str).str.zfill(2)
    )
    if daily_plus:
        df.insert(0, "date", pd.to_datetime(date_str))
    else:
        full_str = (
            date_str
            + " "
            + df["hour"].astype(str).str.zfill(2)
            + ":"
            + df["minute"].astype(str).str.zfill(2)
        )
        df.insert(0, "datetime", pd.to_datetime(full_str))
    df.drop(columns=["year", "month", "day", "hour", "minute"], inplace=True)
    return df


def _merge_txn_datetime(df: pd.DataFrame, date_int: int) -> pd.DataFrame:
    """将逐笔成交的 date + hour:minute 合并为 datetime 列。"""
    if df.empty or "hour" not in df.columns:
        return df
    year = date_int // 10000
    month = (date_int // 100) % 100
    day = date_int % 100
    base = pd.Timestamp(year=year, month=month, day=day)
    offsets = pd.to_timedelta(df["hour"] * 3600 + df["minute"] * 60, unit="s")
    df.insert(0, "datetime", base + offsets)
    df.drop(columns=["hour", "minute"], inplace=True)
    return df


def _add_minute_datetime(df: pd.DataFrame, date_int: int) -> pd.DataFrame:
    """为分时 DataFrame 添加 datetime 列（从 bar 索引计算时间）。

    A 股分时 240 条：0-119 = 9:30~11:29（上午），120-239 = 13:00~14:59（下午）。
    """
    if df.empty:
        return df
    year = date_int // 10000
    month = (date_int // 100) % 100
    day = date_int % 100
    base = pd.Timestamp(year=year, month=month, day=day)
    n = len(df)
    morning = list(range(9 * 60 + 30, 9 * 60 + 30 + 120))
    afternoon = list(range(13 * 60, 13 * 60 + 120))
    all_minutes = (morning + afternoon)[:n]
    offsets = pd.to_timedelta(all_minutes, unit="m")
    df.insert(0, "datetime", base + offsets)
    return df
