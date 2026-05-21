"""高层行情 API：TdxClient（同步）和 AsyncTdxClient（asyncio）。"""

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from types import TracebackType
from typing import TypeVar
from zoneinfo import ZoneInfo

import pandas as pd

from ._df import _add_minute_datetime, _merge_bar_datetime, _merge_txn_datetime, _to_df
from .codec.block import parse_block_dat
from .codec.financial import parse_financial_dat, parse_financial_file_list
from .codec.industry import parse_tdxhy_cfg
from .codec.price_rules import compute_price_limits, get_no_limit_window_days
from .commands.base import BaseCommand
from .commands.block_info import GetBlockInfoCmd, GetBlockInfoMetaCmd
from .commands.company_info import GetCompanyInfoCategoryCmd, GetCompanyInfoContentCmd
from .commands.finance_info import GetFinanceInfoCmd
from .commands.fund_flow import GetHistoryFundFlowCmd
from .commands.minute_time import GetHistoryMinuteTimeDataCmd
from .commands.report_file import GetReportFileCmd
from .commands.security_bars import GetIndexBarsCmd, GetSecurityBarsCmd
from .commands.security_count import GetSecurityCountCmd
from .commands.security_list import GetSecurityListCmd
from .commands.security_quotes import GetSecurityQuotesCmd
from .commands.transaction import GetHistoryTransactionDataCmd, GetTransactionDataCmd
from .commands.xdxr_info import GetXdxrInfoCmd
from .exceptions import TdxConnectionError
from .models.bar import SecurityBar
from .models.enums import KlineCategory, Market
from .models.finance import (
    FinancialFileInfo,
    FinancialRecord,
)
from .models.security import SecurityInfo
from .models.stats import FundFlow, HistoricalFundFlow, MarketStat
from .models.timeseries import TransactionRecord
from .transport.async_ import AsyncTdxConnection
from .transport.sync import CALC_HOSTS, KNOWN_HOSTS, TdxConnection, ping_all

_DEFAULT_PORT = 7709
_T = TypeVar("_T")
_SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
_DAILY_PLUS = frozenset(
    {
        KlineCategory.DAY,
        KlineCategory.WEEK,
        KlineCategory.MONTH,
        KlineCategory.YEAR,
        KlineCategory.YEAR_ALT,
    }
)


def _today_in_shanghai() -> int:
    return int(datetime.now(_SHANGHAI_TZ).strftime("%Y%m%d"))


def _record_signature(
    record: TransactionRecord,
) -> tuple[int, int, float, int, int, int]:
    return (
        record.hour,
        record.minute,
        record.price,
        record.vol,
        record.buyorsell,
        record.unknown_last,
    )


def _page_signature(
    records: list[TransactionRecord],
) -> tuple[tuple[int, int, float, int, int, int], tuple[int, int, float, int, int, int]]:
    return (_record_signature(records[0]), _record_signature(records[-1]))


def _classify_fund_flow(records: list[TransactionRecord]) -> FundFlow:
    stats = {
        "super_in": 0.0,
        "large_in": 0.0,
        "medium_in": 0.0,
        "small_in": 0.0,
        "super_out": 0.0,
        "large_out": 0.0,
        "medium_out": 0.0,
        "small_out": 0.0,
    }

    for record in records:
        amount = record.price * record.vol * 100.0
        direction = "in" if record.buyorsell == 0 else "out" if record.buyorsell == 1 else None
        if not direction:
            continue

        if amount > 1_000_000:
            stats[f"super_{direction}"] += amount
        elif amount > 200_000:
            stats[f"large_{direction}"] += amount
        elif amount > 40_000:
            stats[f"medium_{direction}"] += amount
        else:
            stats[f"small_{direction}"] += amount

    return FundFlow(**stats)


def _date_from_bar(bar: SecurityBar) -> int:
    return bar.year * 10000 + bar.month * 100 + bar.day


def _historical_fund_flow_from_records(
    date: int, records: list[TransactionRecord]
) -> HistoricalFundFlow:
    flow = _classify_fund_flow(records)
    year = date // 10000
    month = (date // 100) % 100
    day = date % 100
    return HistoricalFundFlow(
        year=year,
        month=month,
        day=day,
        super_in=flow.super_in,
        super_out=flow.super_out,
        large_in=flow.large_in,
        large_out=flow.large_out,
        medium_in=flow.medium_in,
        medium_out=flow.medium_out,
        small_in=flow.small_in,
        small_out=flow.small_out,
    )


# ============================================================
# 同步客户端
# ============================================================

_CACHE_DIR = Path.home() / ".easy_tdx" / "cache"
_CACHE_MAX_AGE = 86400  # 1 天


def _serialize_stocks(stocks: list[SecurityInfo]) -> list[dict]:
    return [{k: v for k, v in asdict(s).items() if k != "_raw"} for s in stocks]


def _deserialize_stocks(data: list[dict]) -> list[SecurityInfo]:
    return [SecurityInfo(**{**d, "market": Market(d["market"])}) for d in data]


def _load_cache() -> list[SecurityInfo] | None:
    path = _CACHE_DIR / "security_list_all.json"
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text("utf-8"))
        updated = datetime.fromisoformat(raw["updated"])
        if (datetime.now() - updated).total_seconds() > _CACHE_MAX_AGE:
            return None
        return _deserialize_stocks(raw["data"])
    except Exception:
        return None


def _save_cache(stocks: list[SecurityInfo]) -> None:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    data = {
        "updated": datetime.now().isoformat(),
        "count": len(stocks),
        "data": _serialize_stocks(stocks),
    }
    (_CACHE_DIR / "security_list_all.json").write_text(
        json.dumps(data, ensure_ascii=False), "utf-8"
    )


class TdxClient:
    """同步通达信行情客户端，支持 IP 优选与断线自动重连。

    使用示例::

        # 单台服务器
        with TdxClient("180.153.18.170") as c:
            bars = c.get_security_bars(Market.SH, "600000", KlineCategory.DAY, 0, 100)

        # 自动从候选列表中选延迟最低的服务器
        with TdxClient.from_best_host() as c:
            count = c.get_security_count(Market.SH)
    """

    def __init__(
        self,
        host: str = KNOWN_HOSTS[0],
        port: int = _DEFAULT_PORT,
        timeout: float = 15.0,
        auto_reconnect: bool = True,
    ) -> None:
        self._host = host
        self._port = port
        self._timeout = timeout
        self._auto_reconnect = auto_reconnect
        self._conn = TdxConnection(host, port, timeout)

    # ------------------------------------------------------------------ #
    # 工厂方法：自动优选最低延迟服务器
    # ------------------------------------------------------------------ #

    @classmethod
    def from_best_host(
        cls,
        hosts: list[str] = KNOWN_HOSTS,
        port: int = _DEFAULT_PORT,
        timeout: float = 15.0,
        ping_timeout: float = 5.0,
        auto_reconnect: bool = True,
    ) -> "TdxClient":
        """测量 hosts 中所有服务器延迟，选最低延迟的建立连接。

        若所有服务器均不可达，回退到 hosts[0]。
        """
        ranked = ping_all(hosts, port, ping_timeout)
        best = ranked[0][0] if ranked else hosts[0]
        return cls(best, port, timeout, auto_reconnect)

    @staticmethod
    def ping_all(
        hosts: list[str] = KNOWN_HOSTS,
        port: int = _DEFAULT_PORT,
        timeout: float = 5.0,
    ) -> list[tuple[str, float]]:
        """测量多台服务器延迟，返回按延迟排序的 (host, seconds) 列表。"""
        return ping_all(hosts, port, timeout)

    # ------------------------------------------------------------------ #
    # 连接管理
    # ------------------------------------------------------------------ #

    def connect(self) -> None:
        self._conn.connect()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "TdxClient":
        self.connect()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self.close()

    # ------------------------------------------------------------------ #
    # 内部执行：含自动重连
    # ------------------------------------------------------------------ #

    def _execute(self, cmd: "BaseCommand[_T]") -> _T:
        """执行命令；断线时尝试重连一次再重试（若 auto_reconnect=True）。"""
        try:
            return self._conn.execute(cmd)
        except TdxConnectionError:
            if not self._auto_reconnect:
                raise
            # 重连后重试一次
            self._conn.close()
            self._conn = TdxConnection(self._host, self._port, self._timeout)
            self._conn.connect()
            return self._conn.execute(cmd)

    # ------------------------------------------------------------------ #
    # 市场信息
    # ------------------------------------------------------------------ #

    def get_security_count(self, market: Market) -> int:
        """获取市场证券总数。"""
        return self._execute(GetSecurityCountCmd(market))

    def get_security_list(self, market: Market, start: int) -> pd.DataFrame:
        """获取证券列表（每页约1000条，按 start 分页）。"""
        return _to_df(self._execute(GetSecurityListCmd(market, start)))

    def get_security_list_all(self, pages: int | str = "all") -> pd.DataFrame:
        """获取沪深 A 股完整证券列表，并自动挂载行业信息。

        Args:
            pages: 拉取页数。每个市场每页 1000 条。
                   "all" 拉取全部（默认，结果会缓存到本地文件）。
                   整数 N 表示每个市场只拉前 N 页，不缓存。

        注意：
            `Market.BJ` 的证券列表请求长期存在服务器超时问题，当前版本暂不纳入此方法。
        """
        log = logging.getLogger(__name__)

        if pages == "all":
            cached = _load_cache()
            if cached is not None:
                log.info("从缓存加载沪深 A 股列表，共 %d 只", len(cached))
                return _to_df(cached)

        # 计算每个市场的最大起始偏移
        def _max_start(count: int) -> int:
            if pages == "all":
                return count
            return min(count, int(pages) * 1000)

        # 尝试获取行业配置
        industry_map: dict[str, tuple[str, str]] = {}
        try:
            cfg_data = self.get_report_file("tdxhy.cfg")
            if cfg_data:
                industry_map = parse_tdxhy_cfg(cfg_data)
                log.info("行业配置已加载，共 %d 条映射", len(industry_map))
        except Exception:
            log.warning("无法获取 tdxhy.cfg，行业字段将为空")

        all_stocks: list[SecurityInfo] = []
        for market in [Market.SH, Market.SZ]:
            count = self.get_security_count(market)
            limit = _max_start(count)
            total_pages = (limit + 999) // 1000
            for page_idx, start in enumerate(range(0, limit, 1000)):
                try:
                    stocks = self._execute(GetSecurityListCmd(market, start))
                except Exception:
                    log.warning(
                        "%s 第 %d/%d 页获取失败，跳过", market.name, page_idx + 1, total_pages
                    )
                    continue
                log.info(
                    "%s 第 %d/%d 页: %d 条", market.name, page_idx + 1, total_pages, len(stocks)
                )
                for s in stocks:
                    is_a_share = (market == Market.SH and s.code.startswith(("60", "68"))) or (
                        market == Market.SZ and s.code.startswith(("00", "30"))
                    )
                    if is_a_share:
                        if s.code in industry_map:
                            s.industry_tdx, s.industry_sw = industry_map[s.code]
                        all_stocks.append(s)

        log.info("沪深 A 股总数: %d", len(all_stocks))

        if pages == "all":
            _save_cache(all_stocks)

        return _to_df(all_stocks)

    def get_security_quotes(self, stocks: list[tuple[Market, str]]) -> pd.DataFrame:
        """批量获取实时五档行情（最多80只/次）。"""
        return _to_df(self._execute(GetSecurityQuotesCmd(stocks)))

    def get_price_limits(
        self, market: Market, code: str, name: str, pre_close: float
    ) -> tuple[float | None, float | None]:
        """按当前交易状态计算涨跌停价。

        对上市初期不设涨跌幅限制的标的，会先用日 K 线条数估算已上市交易天数。
        """
        listed_days: int | None = None
        no_limit_window_days = get_no_limit_window_days(market, code, name)
        if no_limit_window_days > 0:
            try:
                bars = self._execute(
                    GetSecurityBarsCmd(market, code, KlineCategory.DAY, 0, no_limit_window_days + 1)
                )
                listed_days = len(bars)
            except Exception:
                listed_days = None

        return compute_price_limits(
            market,
            code,
            name,
            pre_close,
            listed_days=listed_days,
        )

    # ------------------------------------------------------------------ #
    # K 线
    # ------------------------------------------------------------------ #

    def get_security_bars(
        self,
        market: Market,
        code: str,
        category: KlineCategory,
        start: int,
        count: int = 800,
    ) -> pd.DataFrame:
        """获取 K 线数据（最多800条/次，按 start 分页）。"""
        df = _to_df(self._execute(GetSecurityBarsCmd(market, code, category, start, count)))
        return _merge_bar_datetime(df, category in _DAILY_PLUS)

    def get_index_bars(
        self,
        market: Market,
        code: str,
        category: KlineCategory,
        start: int,
        count: int = 800,
    ) -> pd.DataFrame:
        """获取指数 K 线数据。"""
        df = _to_df(self._execute(GetIndexBarsCmd(market, code, category, start, count)))
        return _merge_bar_datetime(df, category in _DAILY_PLUS)

    # ------------------------------------------------------------------ #
    # 分时
    # ------------------------------------------------------------------ #

    def get_minute_time_data(self, market: Market, code: str) -> pd.DataFrame:
        """获取今日分时数据（240条，走历史分时接口）。"""
        today = _today_in_shanghai()
        bars = self._execute(GetHistoryMinuteTimeDataCmd(market, code, today))
        return _add_minute_datetime(_to_df(bars), today)

    def get_history_minute_time_data(self, market: Market, code: str, date: int) -> pd.DataFrame:
        """获取历史某日分时数据（date: YYYYMMDD）。"""
        bars = self._execute(GetHistoryMinuteTimeDataCmd(market, code, date))
        return _add_minute_datetime(_to_df(bars), date)

    # ------------------------------------------------------------------ #
    # 逐笔成交
    # ------------------------------------------------------------------ #

    def get_transaction_data(
        self, market: Market, code: str, start: int, count: int = 800
    ) -> pd.DataFrame:
        """获取当日逐笔成交（分页）。"""
        df = _to_df(self._execute(GetTransactionDataCmd(market, code, start, count)))
        return _merge_txn_datetime(df, _today_in_shanghai())

    def get_history_transaction_data(
        self, market: Market, code: str, date: int, start: int, count: int = 800
    ) -> pd.DataFrame:
        """获取历史逐笔成交（date: YYYYMMDD，分页）。"""
        df = _to_df(self._execute(GetHistoryTransactionDataCmd(market, code, date, start, count)))
        return _merge_txn_datetime(df, date)

    # ------------------------------------------------------------------ #
    # 财务 / 公司
    # ------------------------------------------------------------------ #

    def get_xdxr_info(self, market: Market, code: str) -> pd.DataFrame:
        """获取除权除息历史记录。"""
        return _to_df(self._execute(GetXdxrInfoCmd(market, code)))

    def get_finance_info(self, market: Market, code: str) -> pd.DataFrame:
        """获取最新财务数据。"""
        return _to_df(self._execute(GetFinanceInfoCmd(market, code)))

    def get_company_info_category(self, market: Market, code: str) -> pd.DataFrame:
        """获取公司信息文件目录。"""
        return _to_df(self._execute(GetCompanyInfoCategoryCmd(market, code)))

    def get_company_info_content(
        self, market: Market, code: str, filename: str, offset: int, length: int
    ) -> str:
        """读取公司信息文本。"""
        return self._execute(GetCompanyInfoContentCmd(market, code, filename, offset, length))

    def get_block_info(self, filename: str) -> pd.DataFrame:
        """获取并解析板块文件（行业、概念、风格等）。

        常用文件名：
          'block_zs.dat'  - 行业/指数板块
          'block_gn.dat'  - 概念板块
          'block_fg.dat'  - 风格板块
        """
        size, _hash = self._execute(GetBlockInfoMetaCmd(filename))
        full_data = bytearray()
        pos = 0
        chunk_size = 30000
        while pos < size:
            chunk = self._execute(GetBlockInfoCmd(filename, pos, chunk_size))
            if not chunk:
                break
            full_data.extend(chunk)
            pos += len(chunk)
        return _to_df(parse_block_dat(bytes(full_data), filename))

    def get_report_file(self, filename: str) -> bytes:
        """从服务器拉取大文件（如 'base_info.zip'）。"""
        full_data = bytearray()
        pos = 0
        chunk_size = 30000
        while True:
            chunk = self._execute(GetReportFileCmd(filename, pos, chunk_size))
            if not chunk:
                break
            full_data.extend(chunk)
            pos += len(chunk)
            if len(chunk) < chunk_size:
                break
        return bytes(full_data)

    @staticmethod
    def _download_from_host(
        host: str, filename: str, port: int = 7709, timeout: float = 15.0
    ) -> bytes:
        """从指定服务器创建临时连接并下载文件。"""
        conn = TdxConnection(host, port, timeout)
        try:
            conn.connect()
            full_data = bytearray()
            pos = 0
            chunk_size = 30000
            while True:
                chunk = conn.execute(GetReportFileCmd(filename, pos, chunk_size))
                if not chunk:
                    break
                full_data.extend(chunk)
                pos += len(chunk)
                if len(chunk) < chunk_size:
                    break
            return bytes(full_data)
        finally:
            conn.close()

    def get_financial_file_list(self, host: str = CALC_HOSTS[0]) -> pd.DataFrame:
        """获取可用的历史专业财报文件列表。

        连接到计算服务器，下载 tdxfin/gpcw.txt 并解析。
        """
        data = self._download_from_host(host, "tdxfin/gpcw.txt")
        raw_list = parse_financial_file_list(data)
        return _to_df([FinancialFileInfo(filename=f, hash=h, filesize=s) for f, h, s in raw_list])

    def get_financial_file(self, filename: str, host: str = CALC_HOSTS[0]) -> bytes:
        """从计算服务器下载财报 zip 文件。

        Args:
            filename: 如 'tdxfin/gpcw20260331.zip'
        """
        return self._download_from_host(host, filename)

    def get_financial_records(self, filename: str, host: str = CALC_HOSTS[0]) -> pd.DataFrame:
        """下载财报 zip 并解析为每只股票的记录列表。

        Args:
            filename: 如 'tdxfin/gpcw20260331.zip'
        """
        import io
        import re
        import zipfile

        zip_data = self.get_financial_file(filename, host)
        if not zip_data:
            return pd.DataFrame()

        with zipfile.ZipFile(io.BytesIO(zip_data)) as zf:
            dat_names = [n for n in zf.namelist() if n.endswith(".dat")]
            if not dat_names:
                return pd.DataFrame()
            dat_data = zf.read(dat_names[0])

        m = re.search(r"(\d{8})", filename)
        report_date = int(m.group(1)) if m else 0

        raw_records = parse_financial_dat(dat_data, report_date)
        records: list[FinancialRecord] = []
        for code, market_byte, rdate, fields in raw_records:
            market = Market.SH if market_byte == b"\x01" else Market.SZ
            records.append(
                FinancialRecord(code=code, market=market, report_date=rdate, fields=fields)
            )
        return _to_df(records)

    def get_market_stat(self) -> pd.DataFrame:
        """获取 A 股全市场涨跌统计概况（基于 880005 行情统计）。

        注意：
            `suspended_count` 是 `total - up - down - neutral` 的残差估算值，
            用于保证计数守恒，不应视为协议已明确验证的停牌字段。
        """
        # 通达信中 880005 是全市场行情统计，880001 是总市值指数，880006 是涨跌停统计
        quotes = self._execute(
            GetSecurityQuotesCmd(
                [(Market.SH, "880005"), (Market.SH, "880001"), (Market.SH, "880006")]
            )
        )
        if not quotes:
            raise RuntimeError("无法获取市场统计数据")
        q = quotes[0]
        up = int(q.price)
        down = int(q.open)
        neutral = int(q.low)
        total = int(q.high)
        market_cap = quotes[1].price * 1e10 if len(quotes) > 1 else 0.0
        limit_down = int(quotes[2].open) if len(quotes) > 2 else 0
        limit_up = int(quotes[2].price) if len(quotes) > 2 else 0
        return _to_df(
            MarketStat(
                up_count=up,
                down_count=down,
                neutral_count=neutral,
                suspended_count=max(0, total - up - down - neutral),
                total_count=total,
                total_amount=q.amount,
                total_volume=q.vol,
                total_market_cap=market_cap,
                limit_up_count=limit_up,
                limit_down_count=limit_down,
            )
        )

    def _collect_transaction_records(
        self,
        fetch_page: Callable[[int, int], list[TransactionRecord]],
        page_size: int,
        max_start: int = 10000,
    ) -> list[TransactionRecord]:
        all_recs: list[TransactionRecord] = []
        seen_sig: set[tuple[int, int, float, int, int, int]] = set()
        seen_page_sigs: set[
            tuple[
                tuple[int, int, float, int, int, int],
                tuple[int, int, float, int, int, int],
            ]
        ] = set()
        start = 0

        while start < max_start:
            recs = fetch_page(start, page_size)
            if not recs:
                break

            page_sig = _page_signature(recs)
            if page_sig in seen_page_sigs:
                break
            seen_page_sigs.add(page_sig)

            new_count = 0
            for record in recs:
                sig = _record_signature(record)
                if sig not in seen_sig:
                    seen_sig.add(sig)
                    all_recs.append(record)
                    new_count += 1

            if new_count == 0:
                break

            start += len(recs)
            if len(recs) < 100:
                break

        return all_recs

    def get_fund_flow(self, market: Market, code: str) -> pd.DataFrame:
        """获取个股当日资金流向分布（基于 L1 逐笔数据统计）。"""
        records = self._collect_transaction_records(
            lambda start, page_size: self._execute(
                GetTransactionDataCmd(market, code, start, page_size)
            ),
            2000,
        )
        return _to_df(_classify_fund_flow(records))

    def get_history_fund_flow(
        self, market: Market, code: str, start: int, count: int
    ) -> pd.DataFrame:
        """获取个股历史日线资金流向序列。

        优先走 Category 22 直连接口；若服务器返回空列表，则自动回退为
        "日 K 线取日期 + 历史逐笔成交重算资金流"的兼容实现。
        """
        try:
            direct = self._execute(GetHistoryFundFlowCmd(market, code, start, count))
        except Exception:
            direct = []
        if direct:
            return _to_df(direct)

        bars = self._execute(GetSecurityBarsCmd(market, code, KlineCategory.DAY, start, count))
        results: list[HistoricalFundFlow] = []
        for bar in bars:
            date = _date_from_bar(bar)
            records = self._collect_transaction_records(
                lambda page_start, page_size: self._execute(
                    GetHistoryTransactionDataCmd(market, code, date, page_start, page_size)
                ),
                800,
            )
            results.append(_historical_fund_flow_from_records(date, records))
        return _to_df(results)


# ============================================================
# 异步客户端
# ============================================================


class AsyncTdxClient:
    """异步通达信行情客户端（asyncio）。

    使用示例::

        async with AsyncTdxClient("180.153.18.170") as c:
            bars = await c.get_security_bars(Market.SH, "600000", KlineCategory.DAY, 0, 100)

    注意：
        单个 AsyncTdxClient 仅维护一条 TCP 连接；并发调用会在连接内串行执行。
    """

    def __init__(
        self,
        host: str = KNOWN_HOSTS[0],
        port: int = _DEFAULT_PORT,
        timeout: float = 15.0,
        auto_reconnect: bool = True,
        heartbeat_interval: float = 60.0,
    ) -> None:
        self._host = host
        self._port = port
        self._timeout = timeout
        self._auto_reconnect = auto_reconnect
        self._heartbeat_interval = heartbeat_interval
        self._conn = AsyncTdxConnection(host, port, timeout)
        self._execute_lock = asyncio.Lock()
        self._heartbeat_task: asyncio.Task[None] | None = None

    @classmethod
    def from_best_host(
        cls,
        hosts: list[str] = KNOWN_HOSTS,
        port: int = _DEFAULT_PORT,
        timeout: float = 15.0,
        ping_timeout: float = 5.0,
        auto_reconnect: bool = True,
        heartbeat_interval: float = 60.0,
    ) -> "AsyncTdxClient":
        """测量 hosts 中所有服务器延迟，选最低延迟的建立连接。"""
        ranked = ping_all(hosts, port, ping_timeout)
        best = ranked[0][0] if ranked else hosts[0]
        return cls(best, port, timeout, auto_reconnect, heartbeat_interval)

    @staticmethod
    def ping_all(
        hosts: list[str] = KNOWN_HOSTS,
        port: int = _DEFAULT_PORT,
        timeout: float = 5.0,
    ) -> list[tuple[str, float]]:
        """测量多台服务器延迟，返回按延迟排序的 (host, seconds) 列表。"""
        return ping_all(hosts, port, timeout)

    async def connect(self) -> None:
        await self._conn.connect()
        self._start_heartbeat()

    async def close(self) -> None:
        await self._stop_heartbeat()
        await self._conn.close()

    async def __aenter__(self) -> "AsyncTdxClient":
        await self.connect()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        await self.close()

    def _start_heartbeat(self) -> None:
        """启动后台心跳任务。"""
        if self._heartbeat_interval <= 0:
            return
        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

    async def _stop_heartbeat(self) -> None:
        """停止并清理心跳任务。"""
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
            self._heartbeat_task = None

    async def _heartbeat_loop(self) -> None:
        """心跳循环：定期发送轻量级请求保活。"""
        while True:
            try:
                await asyncio.sleep(self._heartbeat_interval)
                # 使用 get_security_count 作为心跳包
                await self.get_security_count(Market.SH)
            except asyncio.CancelledError:
                break
            except Exception:
                # 心跳失败通常意味着连接已断开
                # 下一次正常的业务请求或下一次心跳会通过 _execute 触发重连
                pass

    async def _execute(self, cmd: "BaseCommand[_T]") -> _T:
        """执行命令；断线时尝试重连一次再重试（若 auto_reconnect=True）。"""
        async with self._execute_lock:
            try:
                return await self._conn.execute(cmd)
            except TdxConnectionError:
                if not self._auto_reconnect:
                    raise
                await self._conn.close()
                self._conn = AsyncTdxConnection(self._host, self._port, self._timeout)
                await self._conn.connect()
                return await self._conn.execute(cmd)

    async def get_security_count(self, market: Market) -> int:
        return await self._execute(GetSecurityCountCmd(market))

    async def get_security_list(self, market: Market, start: int) -> pd.DataFrame:
        return _to_df(await self._execute(GetSecurityListCmd(market, start)))

    async def get_security_list_all(self, pages: int | str = "all") -> pd.DataFrame:
        """获取沪深 A 股完整证券列表，并自动挂载行业信息。

        Args:
            pages: 拉取页数。每个市场每页 1000 条。
                   "all" 拉取全部（默认，结果会缓存到本地文件）。
                   整数 N 表示每个市场只拉前 N 页，不缓存。

        注意：
            `Market.BJ` 的证券列表请求长期存在服务器超时问题，当前版本暂不纳入此方法。
        """
        log = logging.getLogger(__name__)

        if pages == "all":
            cached = _load_cache()
            if cached is not None:
                log.info("从缓存加载沪深 A 股列表，共 %d 只", len(cached))
                return _to_df(cached)

        def _max_start(count: int) -> int:
            if pages == "all":
                return count
            return min(count, int(pages) * 1000)

        industry_map: dict[str, tuple[str, str]] = {}
        try:
            cfg_data = await self.get_report_file("tdxhy.cfg")
            if cfg_data:
                industry_map = parse_tdxhy_cfg(cfg_data)
                log.info("行业配置已加载，共 %d 条映射", len(industry_map))
        except Exception:
            log.warning("无法获取 tdxhy.cfg，行业字段将为空")

        all_stocks: list[SecurityInfo] = []
        for market in [Market.SH, Market.SZ]:
            count = await self.get_security_count(market)
            limit = _max_start(count)
            total_pages = (limit + 999) // 1000
            for page_idx, start in enumerate(range(0, limit, 1000)):
                try:
                    stocks = await self._execute(GetSecurityListCmd(market, start))
                except Exception:
                    log.warning(
                        "%s 第 %d/%d 页获取失败，跳过", market.name, page_idx + 1, total_pages
                    )
                    continue
                log.info(
                    "%s 第 %d/%d 页: %d 条", market.name, page_idx + 1, total_pages, len(stocks)
                )
                for s in stocks:
                    is_a_share = (market == Market.SH and s.code.startswith(("60", "68"))) or (
                        market == Market.SZ and s.code.startswith(("00", "30"))
                    )
                    if is_a_share:
                        if s.code in industry_map:
                            s.industry_tdx, s.industry_sw = industry_map[s.code]
                        all_stocks.append(s)

        log.info("沪深 A 股总数: %d", len(all_stocks))
        if pages == "all":
            _save_cache(all_stocks)
        return _to_df(all_stocks)

    async def get_security_quotes(self, stocks: list[tuple[Market, str]]) -> pd.DataFrame:
        return _to_df(await self._execute(GetSecurityQuotesCmd(stocks)))

    async def get_price_limits(
        self, market: Market, code: str, name: str, pre_close: float
    ) -> tuple[float | None, float | None]:
        """按当前交易状态计算涨跌停价。"""
        listed_days: int | None = None
        no_limit_window_days = get_no_limit_window_days(market, code, name)
        if no_limit_window_days > 0:
            try:
                bars = await self._execute(
                    GetSecurityBarsCmd(market, code, KlineCategory.DAY, 0, no_limit_window_days + 1)
                )
                listed_days = len(bars)
            except Exception:
                listed_days = None

        return compute_price_limits(
            market,
            code,
            name,
            pre_close,
            listed_days=listed_days,
        )

    async def get_security_bars(
        self,
        market: Market,
        code: str,
        category: KlineCategory,
        start: int,
        count: int = 800,
    ) -> pd.DataFrame:
        df = _to_df(await self._execute(GetSecurityBarsCmd(market, code, category, start, count)))
        return _merge_bar_datetime(df, category in _DAILY_PLUS)

    async def get_index_bars(
        self,
        market: Market,
        code: str,
        category: KlineCategory,
        start: int,
        count: int = 800,
    ) -> pd.DataFrame:
        df = _to_df(await self._execute(GetIndexBarsCmd(market, code, category, start, count)))
        return _merge_bar_datetime(df, category in _DAILY_PLUS)

    async def get_minute_time_data(self, market: Market, code: str) -> pd.DataFrame:
        today = _today_in_shanghai()
        bars = await self._execute(GetHistoryMinuteTimeDataCmd(market, code, today))
        return _add_minute_datetime(_to_df(bars), today)

    async def get_history_minute_time_data(
        self, market: Market, code: str, date: int
    ) -> pd.DataFrame:
        bars = await self._execute(GetHistoryMinuteTimeDataCmd(market, code, date))
        return _add_minute_datetime(_to_df(bars), date)

    async def get_transaction_data(
        self, market: Market, code: str, start: int, count: int = 800
    ) -> pd.DataFrame:
        df = _to_df(await self._execute(GetTransactionDataCmd(market, code, start, count)))
        return _merge_txn_datetime(df, _today_in_shanghai())

    async def get_history_transaction_data(
        self, market: Market, code: str, date: int, start: int, count: int = 800
    ) -> pd.DataFrame:
        df = _to_df(
            await self._execute(GetHistoryTransactionDataCmd(market, code, date, start, count))
        )
        return _merge_txn_datetime(df, date)

    async def get_xdxr_info(self, market: Market, code: str) -> pd.DataFrame:
        return _to_df(await self._execute(GetXdxrInfoCmd(market, code)))

    async def get_finance_info(self, market: Market, code: str) -> pd.DataFrame:
        return _to_df(await self._execute(GetFinanceInfoCmd(market, code)))

    async def get_company_info_category(self, market: Market, code: str) -> pd.DataFrame:
        return _to_df(await self._execute(GetCompanyInfoCategoryCmd(market, code)))

    async def get_company_info_content(
        self, market: Market, code: str, filename: str, offset: int, length: int
    ) -> str:
        return await self._execute(GetCompanyInfoContentCmd(market, code, filename, offset, length))

    async def get_block_info(self, filename: str) -> pd.DataFrame:
        """获取并解析板块文件（行业、概念、风格等）。"""
        size, _hash = await self._execute(GetBlockInfoMetaCmd(filename))
        full_data = bytearray()
        pos = 0
        chunk_size = 30000
        while pos < size:
            chunk = await self._execute(GetBlockInfoCmd(filename, pos, chunk_size))
            if not chunk:
                break
            full_data.extend(chunk)
            pos += len(chunk)
        return _to_df(parse_block_dat(bytes(full_data), filename))

    async def get_report_file(self, filename: str) -> bytes:
        """从服务器拉取大文件。"""
        full_data = bytearray()
        pos = 0
        chunk_size = 30000
        while True:
            chunk = await self._execute(GetReportFileCmd(filename, pos, chunk_size))
            if not chunk:
                break
            full_data.extend(chunk)
            pos += len(chunk)
            if len(chunk) < chunk_size:
                break
        return bytes(full_data)

    @staticmethod
    async def _async_download_from_host(
        host: str, filename: str, port: int = 7709, timeout: float = 15.0
    ) -> bytes:
        """从指定服务器创建临时异步连接并下载文件。"""
        conn = AsyncTdxConnection(host, port, timeout)
        try:
            await conn.connect()
            full_data = bytearray()
            pos = 0
            chunk_size = 30000
            while True:
                chunk = await conn.execute(GetReportFileCmd(filename, pos, chunk_size))
                if not chunk:
                    break
                full_data.extend(chunk)
                pos += len(chunk)
                if len(chunk) < chunk_size:
                    break
            return bytes(full_data)
        finally:
            await conn.close()

    async def get_financial_file_list(self, host: str = CALC_HOSTS[0]) -> pd.DataFrame:
        """获取可用的历史专业财报文件列表（异步）。"""
        data = await self._async_download_from_host(host, "tdxfin/gpcw.txt")
        raw_list = parse_financial_file_list(data)
        return _to_df([FinancialFileInfo(filename=f, hash=h, filesize=s) for f, h, s in raw_list])

    async def get_financial_file(self, filename: str, host: str = CALC_HOSTS[0]) -> bytes:
        """从计算服务器下载财报 zip 文件（异步）。"""
        return await self._async_download_from_host(host, filename)

    async def get_financial_records(self, filename: str, host: str = CALC_HOSTS[0]) -> pd.DataFrame:
        """下载财报 zip 并解析为记录列表（异步）。"""
        import io
        import re
        import zipfile

        zip_data = await self.get_financial_file(filename, host)
        if not zip_data:
            return pd.DataFrame()

        with zipfile.ZipFile(io.BytesIO(zip_data)) as zf:
            dat_names = [n for n in zf.namelist() if n.endswith(".dat")]
            if not dat_names:
                return pd.DataFrame()
            dat_data = zf.read(dat_names[0])

        m = re.search(r"(\d{8})", filename)
        report_date = int(m.group(1)) if m else 0

        raw_records = parse_financial_dat(dat_data, report_date)
        records: list[FinancialRecord] = []
        for code, market_byte, rdate, fields in raw_records:
            market = Market.SH if market_byte == b"\x01" else Market.SZ
            records.append(
                FinancialRecord(code=code, market=market, report_date=rdate, fields=fields)
            )
        return _to_df(records)

    async def get_market_stat(self) -> pd.DataFrame:
        """获取 A 股全市场涨跌统计概况（基于 880005 行情统计）。

        注意：
            `suspended_count` 是 `total - up - down - neutral` 的残差估算值，
            用于保证计数守恒，不应视为协议已明确验证的停牌字段。
        """
        # 通达信中 880005 是全市场行情统计，880001 是总市值指数，880006 是涨跌停统计
        quotes = await self._execute(
            GetSecurityQuotesCmd(
                [(Market.SH, "880005"), (Market.SH, "880001"), (Market.SH, "880006")]
            )
        )
        if not quotes:
            raise RuntimeError("无法获取市场统计数据")
        q = quotes[0]
        up = int(q.price)
        down = int(q.open)
        neutral = int(q.low)
        total = int(q.high)
        market_cap = quotes[1].price * 1e10 if len(quotes) > 1 else 0.0
        limit_down = int(quotes[2].open) if len(quotes) > 2 else 0
        limit_up = int(quotes[2].price) if len(quotes) > 2 else 0
        return _to_df(
            MarketStat(
                up_count=up,
                down_count=down,
                neutral_count=neutral,
                suspended_count=max(0, total - up - down - neutral),
                total_count=total,
                total_amount=q.amount,
                total_volume=q.vol,
                total_market_cap=market_cap,
                limit_up_count=limit_up,
                limit_down_count=limit_down,
            )
        )

    async def _collect_transaction_records(
        self,
        fetch_page: Callable[[int, int], Awaitable[list[TransactionRecord]]],
        page_size: int,
        max_start: int = 10000,
    ) -> list[TransactionRecord]:
        all_recs: list[TransactionRecord] = []
        seen_sig: set[tuple[int, int, float, int, int, int]] = set()
        seen_page_sigs: set[
            tuple[
                tuple[int, int, float, int, int, int],
                tuple[int, int, float, int, int, int],
            ]
        ] = set()
        start = 0

        while start < max_start:
            recs = await fetch_page(start, page_size)
            if not recs:
                break

            page_sig = _page_signature(recs)
            if page_sig in seen_page_sigs:
                break
            seen_page_sigs.add(page_sig)

            new_count = 0
            for record in recs:
                sig = _record_signature(record)
                if sig not in seen_sig:
                    seen_sig.add(sig)
                    all_recs.append(record)
                    new_count += 1

            if new_count == 0:
                break

            start += len(recs)
            if len(recs) < 100:
                break

        return all_recs

    async def get_fund_flow(self, market: Market, code: str) -> pd.DataFrame:
        """获取个股当日资金流向分布（基于 L1 逐笔数据统计）。"""
        records = await self._collect_transaction_records(
            lambda start, page_size: self._execute(
                GetTransactionDataCmd(market, code, start, page_size)
            ),
            2000,
        )
        return _to_df(_classify_fund_flow(records))

    async def get_history_fund_flow(
        self, market: Market, code: str, start: int, count: int
    ) -> pd.DataFrame:
        """获取个股历史日线资金流向序列。

        优先走 Category 22 直连接口；若服务器返回空列表，则自动回退为
        "日 K 线取日期 + 历史逐笔成交重算资金流"的兼容实现。
        """
        try:
            direct = await self._execute(GetHistoryFundFlowCmd(market, code, start, count))
        except Exception:
            direct = []
        if direct:
            return _to_df(direct)

        bars = await self._execute(
            GetSecurityBarsCmd(market, code, KlineCategory.DAY, start, count)
        )
        results: list[HistoricalFundFlow] = []
        for bar in bars:
            date = _date_from_bar(bar)
            records = await self._collect_transaction_records(
                lambda page_start, page_size: self._execute(
                    GetHistoryTransactionDataCmd(market, code, date, page_start, page_size)
                ),
                800,
            )
            results.append(_historical_fund_flow_from_records(date, records))
        return _to_df(results)
