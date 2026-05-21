"""离线 fixture 测试：将录制的原始 body 字节喂给各命令 parse_response，验证解析结果。

fixtures/ 目录下每个 .hex 文件是一次真实服务器响应的 body（已解压），
对应的 .json 文件记录关键预期值，供手工核对。
此测试文件直接断言解析结果，无需网络连接。
"""

from __future__ import annotations

import pathlib
import struct

FIXTURES = pathlib.Path(__file__).parent.parent / "fixtures"


def load_hex(name: str) -> bytes:
    return bytes.fromhex((FIXTURES / f"{name}.hex").read_text().strip())


# ---------------------------------------------------------------------------
# security_count
# ---------------------------------------------------------------------------


def test_security_count_parse():
    from easy_tdx.commands.security_count import GetSecurityCountCmd
    from easy_tdx.models.enums import Market

    body = load_hex("security_count")
    cmd = GetSecurityCountCmd(Market.SH)
    count = cmd.parse_response(body)

    assert isinstance(count, int)
    assert count > 0
    # 体积固定为 2 字节，结果与录制时完全一致
    assert count == 26885


# ---------------------------------------------------------------------------
# security_list
# ---------------------------------------------------------------------------


def test_security_list_parse():
    from easy_tdx.commands.security_list import GetSecurityListCmd
    from easy_tdx.models.enums import Market

    body = load_hex("security_list")
    cmd = GetSecurityListCmd(Market.SH, 0)
    records = cmd.parse_response(body)

    assert len(records) == 1000

    r0 = records[0]
    assert r0.code == "999999"
    assert r0.name == "上证指数"
    assert abs(r0.pre_close - 3966.171142578125) < 0.01

    # _raw present and non-empty for every record
    assert all(len(r._raw) > 0 for r in records)


def test_security_list_pre_close_uses_tdx_float_for_a_share():
    from easy_tdx.commands.security_list import GetSecurityListCmd
    from easy_tdx.models.enums import Market

    body = struct.pack("<H", 1) + struct.pack(
        "<6sH8s4sBI4s",
        b"600000",
        100,
        "\u6d66\u53d1\u94f6\u884c".encode("gbk"),
        b"\x00\x00\x00\x00",
        2,
        0x411B851F,
        b"\x00\x00\x00\x00",
    )

    record = GetSecurityListCmd(Market.SH, 24000).parse_response(body)[0]

    assert record.code == "600000"
    assert record.name == "浦发银行"
    assert abs(record.pre_close - 9.72) < 0.01


def test_security_list_gbk_no_crash():
    """Bug #2 修复验证：GBK 解码不崩溃，所有记录均有 code。"""
    from easy_tdx.commands.security_list import GetSecurityListCmd
    from easy_tdx.models.enums import Market

    body = load_hex("security_list")
    cmd = GetSecurityListCmd(Market.SH, 0)
    records = cmd.parse_response(body)
    assert all(r.code for r in records)


# ---------------------------------------------------------------------------
# security_bars
# ---------------------------------------------------------------------------


def test_security_bars_parse():
    from easy_tdx.commands.security_bars import GetSecurityBarsCmd
    from easy_tdx.models.enums import KlineCategory, Market

    body = load_hex("security_bars")
    cmd = GetSecurityBarsCmd(Market.SH, "600000", KlineCategory.DAY, 0, 5)
    bars = cmd.parse_response(body)

    assert len(bars) == 5

    b0 = bars[0]
    assert abs(b0.open - 10.25) < 0.01
    assert abs(b0.high - 10.25) < 0.01
    assert abs(b0.low - 10.08) < 0.01
    assert abs(b0.close - 10.12) < 0.01
    assert b0.vol > 0

    # OHLC sanity: high ≥ open,close,low; low ≤ open,close
    for bar in bars:
        assert bar.high >= bar.open - 0.001
        assert bar.high >= bar.close - 0.001
        assert bar.low <= bar.open + 0.001
        assert bar.low <= bar.close + 0.001
        assert bar.vol > 0
        assert len(bar._raw) > 0


# ---------------------------------------------------------------------------
# security_quotes
# ---------------------------------------------------------------------------


def test_security_quotes_parse():
    from easy_tdx.commands.security_quotes import GetSecurityQuotesCmd
    from easy_tdx.models.enums import Market

    body = load_hex("security_quotes")
    cmd = GetSecurityQuotesCmd([(Market.SH, "600000")])
    quotes = cmd.parse_response(body)

    assert len(quotes) == 1

    q = quotes[0]
    assert q.code == "600000"
    assert abs(q.pre_close - 9.93) < 0.01

    # unknown fields are captured (not discarded)
    assert hasattr(q, "unknown_2")
    assert hasattr(q, "unknown_3")
    assert hasattr(q, "unknown_5")
    assert hasattr(q, "unknown_6")
    assert hasattr(q, "unknown_7")
    assert hasattr(q, "unknown_8")
    assert hasattr(q, "rise_speed")
    assert len(q._raw) > 0

    # fixed values from frozen fixture
    assert q.unknown_2 == -1
    assert q.unknown_3 == 22694


# ---------------------------------------------------------------------------
# minute_time
# ---------------------------------------------------------------------------


def test_minute_time_parse():
    from easy_tdx.commands.minute_time import GetMinuteTimeDataCmd
    from easy_tdx.models.enums import Market

    body = load_hex("minute_time")
    cmd = GetMinuteTimeDataCmd(Market.SH, "600000")
    bars = cmd.parse_response(body)

    assert len(bars) == 240

    b0 = bars[0]
    assert isinstance(b0.price, float)
    assert isinstance(b0.vol, int)
    # Bug #5 fix: _unknown_1 is preserved, not discarded
    assert hasattr(b0, "_unknown_1")
    assert isinstance(b0._unknown_1, int)
    assert len(b0._raw) > 0

    # fixed values
    assert abs(b0.price - 0.01) < 0.001
    assert b0.vol == 48
    assert b0._unknown_1 == 54


# ---------------------------------------------------------------------------
# history_minute_time
# ---------------------------------------------------------------------------


def test_history_minute_time_parse():
    from easy_tdx.commands.minute_time import GetHistoryMinuteTimeDataCmd
    from easy_tdx.models.enums import Market

    body = load_hex("history_minute_time")
    cmd = GetHistoryMinuteTimeDataCmd(Market.SH, "600000", 20250108)
    bars = cmd.parse_response(body)

    assert len(bars) == 240

    b0 = bars[0]
    assert abs(b0.price - 10.29) < 0.01
    assert b0.vol == 10044
    assert hasattr(b0, "_unknown_1")
    assert len(b0._raw) > 0


# ---------------------------------------------------------------------------
# transaction (current day)
# ---------------------------------------------------------------------------


def test_transaction_parse():
    from easy_tdx.commands.transaction import GetTransactionDataCmd
    from easy_tdx.models.enums import Market

    body = load_hex("transaction")
    cmd = GetTransactionDataCmd(Market.SH, "600000", 0, 10)
    recs = cmd.parse_response(body)

    assert len(recs) == 10

    r0 = recs[0]
    assert r0.hour == 14
    assert r0.minute == 59
    assert abs(r0.price - 9.9) < 0.01
    assert r0.vol == 0

    # Bug #4 fix: unknown_last captured
    assert hasattr(r0, "unknown_last")
    assert len(r0._raw) > 0

    # buyorsell: 0=buy, 1=sell, 2=neutral, 8=auction — field is an int
    for r in recs:
        assert isinstance(r.buyorsell, int)


# ---------------------------------------------------------------------------
# history_transaction
# ---------------------------------------------------------------------------


def test_history_transaction_parse():
    from easy_tdx.commands.transaction import GetHistoryTransactionDataCmd
    from easy_tdx.models.enums import Market

    body = load_hex("history_transaction")
    cmd = GetHistoryTransactionDataCmd(Market.SH, "600000", 20250108, 0, 10)
    recs = cmd.parse_response(body)

    assert len(recs) == 10

    r0 = recs[0]
    assert r0.hour == 14
    assert r0.minute == 56
    assert abs(r0.price - 10.3) < 0.01
    assert r0.vol == 50

    assert hasattr(r0, "unknown_last")
    assert len(r0._raw) > 0

    for r in recs:
        assert isinstance(r.buyorsell, int)


# ---------------------------------------------------------------------------
# xdxr_info
# ---------------------------------------------------------------------------


def test_xdxr_info_parse():
    from easy_tdx.commands.xdxr_info import GetXdxrInfoCmd
    from easy_tdx.models.enums import Market

    body = load_hex("xdxr_info")
    cmd = GetXdxrInfoCmd(Market.SH, "600000")
    recs = cmd.parse_response(body)

    assert len(recs) == 87

    r0 = recs[0]
    assert r0.year == 1999
    assert r0.month == 11
    assert r0.day == 10
    assert r0.category == 5

    # Bug #1 fix: each record has a unique date (not all reading from body[:7])
    dates = {(r.year, r.month, r.day) for r in recs}
    assert len(dates) > 1, "All records have the same date — Bug #1 not fixed!"

    # category == 1 字段应已从“每10股”归一化为“每股”
    cash = next(r for r in recs if (r.year, r.month, r.day, r.category) == (2000, 7, 6, 1))
    assert abs(cash.fenhong - 0.15) < 1e-6

    bonus = next(r for r in recs if (r.year, r.month, r.day, r.category) == (2002, 8, 22, 1))
    assert abs(bonus.fenhong - 0.2) < 1e-6
    assert abs(bonus.songzhuangu - 0.5) < 1e-6

    assert all(len(r._raw) > 0 for r in recs)

    # share count decode: 通达信自定义浮点，单位万股，与 FinanceInfo.zong_guben/10000 一致
    stock_recs = [r for r in recs if 2 <= r.category <= 10]
    last = stock_recs[-1]
    # 最近一条 hou_zongguben ≈ 3_330_583.75 万股
    # 与 FinanceInfo.zong_guben 33_305_837_500 ÷ 10000 完全吻合
    assert last.hou_zongguben is not None
    assert abs(last.hou_zongguben - 3_330_583.75) < 1.0


def test_xdxr_info_category_1_normalizes_per_10_share_fields():
    from easy_tdx.commands.xdxr_info import GetXdxrInfoCmd
    from easy_tdx.models.enums import Market

    body = bytearray(b"\x00" * 9)
    body.extend(struct.pack("<H", 1))
    body.extend(struct.pack("<B6s", 1, b"600000"))
    body.extend(b"\x00")
    body.extend(struct.pack("<I", 20200102))
    body.extend(struct.pack("<B", 1))
    body.extend(struct.pack("<ffff", 2.0, 8.0, 5.0, 3.0))

    rec = GetXdxrInfoCmd(Market.SH, "600000").parse_response(bytes(body))[0]

    assert abs(rec.fenhong - 0.2) < 1e-6
    assert abs(rec.songzhuangu - 0.5) < 1e-6
    assert abs(rec.peigu - 0.3) < 1e-6
    assert abs(rec.peigujia - 8.0) < 1e-6


# ---------------------------------------------------------------------------
# finance_info
# ---------------------------------------------------------------------------


def test_finance_info_parse():
    from easy_tdx.commands.finance_info import GetFinanceInfoCmd
    from easy_tdx.models.enums import Market

    body = load_hex("finance_info")
    cmd = GetFinanceInfoCmd(Market.SH, "600000")
    info = cmd.parse_response(body)

    # Check key fields are present and reasonable
    assert info.liutong_guben > 0
    assert info.zong_guben > 0
    assert info.meigujing_zichan > 0

    # Fixed values from frozen fixture
    assert abs(info.liutong_guben - 33305837500.0) < 1e6
    assert abs(info.zong_guben - 33305837500.0) < 1e6
    assert abs(info.meigujing_zichan - 22.13) < 0.1

    assert len(info._raw) > 0


# ---------------------------------------------------------------------------
# company_info_category
# ---------------------------------------------------------------------------


def test_company_info_category_parse():
    from easy_tdx.commands.company_info import GetCompanyInfoCategoryCmd
    from easy_tdx.models.enums import Market

    body = load_hex("company_info_category")
    cmd = GetCompanyInfoCategoryCmd(Market.SH, "600000")
    cats = cmd.parse_response(body)

    assert len(cats) == 16

    c0 = cats[0]
    assert c0.name == "最新提示"
    assert c0.filename == "600000.txt"
    assert c0.start == 0
    assert c0.length == 11426


# ---------------------------------------------------------------------------
# company_info_content
# ---------------------------------------------------------------------------


def test_company_info_content_parse():
    from easy_tdx.commands.company_info import GetCompanyInfoContentCmd
    from easy_tdx.models.enums import Market

    body = load_hex("company_info_content")
    cmd = GetCompanyInfoContentCmd(Market.SH, "600000", "600000.txt", 0, 11426)
    text = cmd.parse_response(body)

    assert isinstance(text, str)
    assert len(text) == 8070
    assert "600000" in text
    assert "浦发银行" in text
