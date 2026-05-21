"""板块信息单元测试。"""

import asyncio
import struct
from unittest.mock import patch

import pandas as pd

from easy_tdx.client import AsyncTdxClient, TdxClient
from easy_tdx.codec.block import parse_block_dat
from easy_tdx.models.finance import TdxBlock


@patch("easy_tdx.client.AsyncTdxConnection")
def test_async_get_block_info_logic(mock_conn_cls):
    """测试 AsyncTdxClient.get_block_info 的异步拉取逻辑。"""
    mock_conn = mock_conn_cls.return_value

    # 模拟异步 execute
    async def mock_execute(cmd):
        from easy_tdx.commands.block_info import GetBlockInfoCmd, GetBlockInfoMetaCmd

        if isinstance(cmd, GetBlockInfoMetaCmd):
            return 100, "hash"
        if isinstance(cmd, GetBlockInfoCmd):
            return b"B" * min(cmd.length, 100 - cmd.start)
        return None

    mock_conn.execute.side_effect = mock_execute
    mock_conn.connect.return_value = None
    mock_conn.close.return_value = None

    async def main():
        client = AsyncTdxClient("127.0.0.1")
        with patch("easy_tdx.client.parse_block_dat") as mock_parse:
            mock_parse.return_value = []
            res = await client.get_block_info("test.dat")

            assert isinstance(res, pd.DataFrame)
            assert mock_conn.execute.call_count == 2  # 1 meta + 1 data

    asyncio.run(main())


def test_parse_block_dat_empty():
    assert parse_block_dat(b"") == []
    assert parse_block_dat(b"A" * 385) == []


def test_parse_block_dat_basic():
    # 构造一个极小的合法 .dat 文件
    # Header 384 + Count 2 + Record 2813
    data = bytearray(384)
    data.extend(struct.pack("<H", 1))  # 1 block

    # Block Record: 9s (name) + H (count) + H (type) + 2800s (codes)
    name = "测试板块".encode("gbk")
    record = bytearray((name + b"\x00" * 9)[:9])
    record.extend(struct.pack("<HH", 2, 1))  # 2 stocks, type 1

    # 2 stocks: 600000, 000001
    codes = "600000\x00000001\x00".encode("ascii")
    record.extend((codes + b"\x00" * 2800)[:2800])

    data.extend(record)

    blocks = parse_block_dat(bytes(data), "block_gn.dat")

    assert len(blocks) == 1
    b = blocks[0]
    assert b.name == "测试板块"
    assert b.count == 2
    assert b.category == 2  # 'gn' in filename -> 2
    assert b.codes == ["600000", "000001"]


@patch("easy_tdx.client.TdxConnection")
def test_get_block_info_logic(mock_conn_cls):
    """测试 TdxClient.get_block_info 的分片拉取逻辑。"""
    mock_conn = mock_conn_cls.return_value

    client = TdxClient("127.0.0.1")

    # 模拟 GetBlockInfoMeta 响应：size=35000 (需要2次拉取)
    def mock_execute(cmd):
        from easy_tdx.commands.block_info import GetBlockInfoCmd, GetBlockInfoMetaCmd

        if isinstance(cmd, GetBlockInfoMetaCmd):
            return 35000, "dummy_hash"
        if isinstance(cmd, GetBlockInfoCmd):
            # 返回对应长度的填充数据
            return b"A" * min(cmd.length, 35000 - cmd.start)
        return None

    mock_conn.execute.side_effect = mock_execute

    # 我们主要测试循环是否正确
    with patch("easy_tdx.client.parse_block_dat") as mock_parse:
        mock_parse.return_value = [TdxBlock("Test", 1, 0, [])]
        res = client.get_block_info("test.dat")

        assert len(res) == 1
        # 应该调用了 1 (meta) + 2 (data: 30000 + 5000) = 3 次 execute
        assert mock_conn.execute.call_count == 3

        # 验证最后一次拉取的参数
        last_call_args = mock_conn.execute.call_args_list[-1][0][0]
        assert last_call_args.start == 30000
        assert last_call_args.length == 30000
