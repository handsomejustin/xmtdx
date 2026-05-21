"""今日分时 / 历史分时数据命令。

unknown_1 字段：pytdx 中被完全丢弃，保留供分析（疑似均价）。
"""

import struct

from .._binary import unpack_from
from ..codec.price import get_price
from ..models.enums import Market
from ..models.timeseries import MinuteBar
from .base import BaseCommand


class GetMinuteTimeDataCmd(BaseCommand[list[MinuteBar]]):
    """获取今日分时数据（全天 240 条）。"""

    def __init__(self, market: Market, code: str) -> None:
        self.market = market
        self.code = code.encode("utf-8")

    def build_request(self) -> bytes:
        header = bytes.fromhex("0c1b08000101 0e000e001d05".replace(" ", ""))
        return header + struct.pack("<H6sI", int(self.market), self.code, 0)

    def parse_response(self, body: bytes) -> list[MinuteBar]:
        return _parse_minute_body(body, skip=4)


class GetHistoryMinuteTimeDataCmd(BaseCommand[list[MinuteBar]]):
    """获取历史某日分时数据（date 格式 YYYYMMDD）。"""

    def __init__(self, market: Market, code: str, date: int) -> None:
        self.market = market
        self.code = code.encode("utf-8")
        self.date = date

    def build_request(self) -> bytes:
        # 历史分时：header + pack("<IB6s", date, market, code)
        header = bytes.fromhex("0c013000010 10d000d00b40f".replace(" ", ""))
        return header + struct.pack("<IB6s", self.date, int(self.market), self.code)

    def parse_response(self, body: bytes) -> list[MinuteBar]:
        # 历史分时：pytdx 中 pos 跳过 6 字节（2 num + 4 未知）
        return _parse_minute_body(body, skip=6)


def _parse_minute_body(body: bytes, skip: int = 4) -> list[MinuteBar]:
    (num,) = unpack_from("<H", body, 0, "minute_time header")
    pos = skip  # 今日分时 skip=4，历史分时 skip=6
    last_price = 0
    bars: list[MinuteBar] = []

    for _ in range(num):
        record_start = pos
        price_diff, pos = get_price(body, pos)
        unknown_1, pos = get_price(body, pos)  # pytdx 原丢弃，保留
        vol, pos = get_price(body, pos)

        last_price += price_diff
        bars.append(
            MinuteBar(
                price=last_price / 100.0,
                vol=vol,
                _unknown_1=unknown_1,
                _raw=body[record_start:pos],
            )
        )

    return bars
