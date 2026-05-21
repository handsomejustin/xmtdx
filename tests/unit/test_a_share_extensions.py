"""针对本轮 A 股增强功能的单元测试。"""

import asyncio
import struct
from unittest.mock import patch

import pandas as pd

from easy_tdx import AsyncTdxClient, Market, TdxClient
from easy_tdx.client import _classify_fund_flow
from easy_tdx.commands.minute_time import (
    GetHistoryMinuteTimeDataCmd,
)
from easy_tdx.commands.security_bars import GetSecurityBarsCmd
from easy_tdx.commands.security_list import GetSecurityListCmd
from easy_tdx.commands.security_quotes import GetSecurityQuotesCmd
from easy_tdx.commands.transaction import (
    GetHistoryTransactionDataCmd,
    GetTransactionDataCmd,
)
from easy_tdx.models.bar import SecurityBar
from easy_tdx.models.quote import SecurityQuote
from easy_tdx.models.security import SecurityInfo
from easy_tdx.models.timeseries import MinuteBar, TransactionRecord


@patch("easy_tdx.client.TdxConnection")
def test_get_fund_flow_logic(_mock_conn_cls):
    """测试资金流分类计算逻辑。"""
    client = TdxClient("127.0.0.1")

    mock_recs = [
        TransactionRecord(10, 0, 100.0, 101, 0, 0),  # super_in
        TransactionRecord(10, 1, 10.0, 250, 1, 0),  # large_out
        TransactionRecord(10, 2, 10.0, 10, 0, 0),  # small_in
    ]

    def mock_execute(cmd):
        if isinstance(cmd, GetTransactionDataCmd):
            return mock_recs
        return []

    with patch.object(TdxClient, "_execute", side_effect=mock_execute):
        flow = client.get_fund_flow(Market.SH, "600000")
        assert isinstance(flow, pd.DataFrame)
        assert flow["super_in"].iloc[0] == 1010000.0
        assert flow["large_out"].iloc[0] == 250000.0
        assert flow["small_in"].iloc[0] == 10000.0


def test_classify_fund_flow_exact_thresholds_use_lower_bucket():
    """恰好命中阈值时，应落入较低一档。"""
    flow = _classify_fund_flow(
        [
            TransactionRecord(10, 0, 100.0, 100, 0, 0),  # 100w -> large
            TransactionRecord(10, 1, 100.0, 20, 0, 0),  # 20w -> medium
            TransactionRecord(10, 2, 100.0, 4, 0, 0),  # 4w -> small
        ]
    )

    assert flow.super_in == 0.0
    assert flow.large_in == 1000000.0
    assert flow.medium_in == 200000.0
    assert flow.small_in == 40000.0


@patch("easy_tdx.client.TdxConnection")
def test_get_security_list_all_filtering(_mock_conn_cls):
    """测试三市 A 股过滤与行业挂载逻辑。"""
    client = TdxClient("127.0.0.1")

    industry_cfg = b"1|600000|T01|||X01\n0|000001|T02|||X02\n2|830000|T03|||X03"

    def mock_execute(cmd):
        if isinstance(cmd, GetSecurityListCmd):
            if cmd.market == Market.SH:
                return [
                    SecurityInfo(Market.SH, "600000", "SH_A", 100, 2, 10.0),
                    SecurityInfo(Market.SH, "999999", "INDEX", 100, 2, 3000.0),
                ]
            if cmd.market == Market.SZ:
                return [SecurityInfo(Market.SZ, "000001", "SZ_A", 100, 2, 10.0)]
            return []
        return []

    with (
        patch.object(TdxClient, "_execute", side_effect=mock_execute),
        patch.object(TdxClient, "get_report_file", return_value=industry_cfg),
        patch.object(TdxClient, "get_security_count", return_value=1),
    ):
        all_stocks = client.get_security_list_all(pages=1)

        assert isinstance(all_stocks, pd.DataFrame)
        assert len(all_stocks) == 2
        codes = all_stocks["code"].tolist()
        assert "600000" in codes
        assert "000001" in codes
        assert "830000" not in codes
        row = all_stocks[all_stocks["code"] == "600000"].iloc[0]
        assert row["industry_tdx"] == "T01"


@patch("easy_tdx.client.TdxConnection")
def test_get_market_stat_mapping(_mock_conn_cls):
    """测试市场统计字段映射。"""
    client = TdxClient("127.0.0.1")

    mock_quote = SecurityQuote(
        Market.SH,
        "880005",
        price=3000.0,  # up = int(price)
        pre_close=0,
        open=2000.0,  # down = int(open)
        high=5500.0,  # total = int(high)
        low=500.0,  # neutral = int(low)
        vol=1000000.0,
        cur_vol=0,
        amount=50000000.0,
        s_vol=0,
        b_vol=0,
        active1=0,
        active2=0,
        bid1=0,
        bid_vol1=0,
        bid2=0,
        bid_vol2=0,
        bid3=0,
        bid_vol3=0,
        bid4=0,
        bid_vol4=0,
        bid5=0,
        bid_vol5=0,
        ask1=0,
        ask_vol1=0,
        ask2=0,
        ask_vol2=0,
        ask3=0,
        ask_vol3=0,
        ask4=0,
        ask_vol4=0,
        ask5=0,
        ask_vol5=0,
        rise_speed=0,
        limit_up=0,
        limit_down=0,
    )

    def mock_execute(cmd):
        if isinstance(cmd, GetSecurityQuotesCmd):
            return [mock_quote]
        return []

    with patch.object(TdxClient, "_execute", side_effect=mock_execute):
        stat = client.get_market_stat()
        assert isinstance(stat, pd.DataFrame)
        assert stat["up_count"].iloc[0] == 3000
        assert stat["down_count"].iloc[0] == 2000
        assert stat["neutral_count"].iloc[0] == 500
        assert stat["total_count"].iloc[0] == 5500


def test_get_history_fund_flow_parsing():
    """测试历史资金流序列解析逻辑。"""
    from easy_tdx.commands.fund_flow import GetHistoryFundFlowCmd

    body = bytearray(9)
    body.extend(struct.pack("<H", 1))

    date = 20250108
    record = struct.pack("<IIIIIIIII", date, 100, 200, 300, 400, 500, 600, 700, 800)
    body.extend(record)

    cmd = GetHistoryFundFlowCmd(Market.SH, "600000", 0, 1)
    res = cmd.parse_response(bytes(body))

    assert len(res) == 1
    assert res[0].year == 2025
    assert res[0].month == 1
    assert res[0].day == 8


@patch("easy_tdx.client.TdxConnection")
def test_get_history_fund_flow_fallback(_mock_conn_cls):
    """Category 22 空回包时，自动回退到历史逐笔重算。"""
    from easy_tdx.commands.fund_flow import GetHistoryFundFlowCmd

    client = TdxClient("127.0.0.1")

    bars = [
        SecurityBar(10, 10, 10, 10, 0, 0, 2025, 1, 8, 15, 0),
        SecurityBar(10, 10, 10, 10, 0, 0, 2025, 1, 9, 15, 0),
    ]
    txn_map = {
        20250108: [
            TransactionRecord(10, 0, 100.0, 101, 0, 0),
            TransactionRecord(10, 1, 10.0, 250, 1, 0),
        ],
        20250109: [
            TransactionRecord(10, 0, 10.0, 10, 0, 0),
        ],
    }

    def mock_execute(cmd):
        if isinstance(cmd, GetHistoryFundFlowCmd):
            return []
        if isinstance(cmd, GetSecurityBarsCmd):
            return bars
        if isinstance(cmd, GetHistoryTransactionDataCmd):
            if cmd.start > 0:
                return []
            return txn_map.get(cmd.date, [])
        return []

    with patch.object(TdxClient, "_execute", side_effect=mock_execute):
        flows = client.get_history_fund_flow(Market.SH, "600000", 0, 2)

    assert isinstance(flows, pd.DataFrame)
    assert len(flows) == 2
    row0 = flows.iloc[0]
    assert row0["super_in"] == 1010000.0
    assert row0["large_out"] == 250000.0
    row1 = flows.iloc[1]
    assert row1["small_in"] == 10000.0


@patch("easy_tdx.client.TdxConnection")
def test_get_price_limits_uses_listing_window(_mock_conn_cls):
    """client.get_price_limits 应结合日 K 条数判断上市初期限价窗口。"""
    client = TdxClient("127.0.0.1")

    def mock_execute_5(cmd):
        if isinstance(cmd, GetSecurityBarsCmd):
            return [SecurityBar(0, 0, 0, 0, 0, 0, 2025, 1, 1, 15, 0)] * 5
        return []

    with patch.object(TdxClient, "_execute", side_effect=mock_execute_5):
        assert client.get_price_limits(Market.SH, "600001", "主板新股", 10.0) == (
            None,
            None,
        )

    def mock_execute_6(cmd):
        if isinstance(cmd, GetSecurityBarsCmd):
            return [SecurityBar(0, 0, 0, 0, 0, 0, 2025, 1, 1, 15, 0)] * 6
        return []

    with patch.object(TdxClient, "_execute", side_effect=mock_execute_6):
        assert client.get_price_limits(Market.SH, "600001", "主板老股", 10.0) == (
            11.0,
            9.0,
        )


@patch("easy_tdx.client.TdxConnection")
def test_get_minute_time_data_uses_history_endpoint(_mock_conn_cls):
    """今日分时走历史分时接口。"""
    client = TdxClient("127.0.0.1")
    expected = [MinuteBar(price=9.7, vol=13694)]

    def mock_execute(cmd):
        if isinstance(cmd, GetHistoryMinuteTimeDataCmd):
            return expected
        return []

    with (
        patch("easy_tdx.client._today_in_shanghai", return_value=20260422),
        patch.object(TdxClient, "_execute", side_effect=mock_execute) as mock_exec,
    ):
        result = client.get_minute_time_data(Market.SH, "600000")

    assert isinstance(result, pd.DataFrame)
    assert result["price"].iloc[0] == 9.7
    history_calls = [
        c for c in mock_exec.call_args_list if isinstance(c[0][0], GetHistoryMinuteTimeDataCmd)
    ]
    assert len(history_calls) == 1


def test_async_get_minute_time_data_uses_history_endpoint():
    """异步客户端走历史分时接口。"""
    expected = [MinuteBar(price=9.7, vol=13694)]

    async def run_test() -> None:
        with patch("easy_tdx.client.AsyncTdxConnection"):
            client = AsyncTdxClient("127.0.0.1")

            async def mock_execute(cmd):
                if isinstance(cmd, GetHistoryMinuteTimeDataCmd):
                    return expected
                return []

            with (
                patch("easy_tdx.client._today_in_shanghai", return_value=20260422),
                patch.object(AsyncTdxClient, "_execute", side_effect=mock_execute),
            ):
                result = await client.get_minute_time_data(Market.SH, "600000")

            assert isinstance(result, pd.DataFrame)
            assert result["price"].iloc[0] == 9.7

    asyncio.run(run_test())
