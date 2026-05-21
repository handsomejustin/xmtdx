"""演示：获取个股 K 线数据。

K 线类别:
  KlineCategory.MIN_1  / MIN_5  / MIN_15 / MIN_30 / MIN_60
  KlineCategory.DAY    / WEEK   / MONTH  / YEAR
"""

from easy_tdx import KlineCategory, Market, TdxClient

with TdxClient.from_best_host() as c:
    df = c.get_security_bars(Market.SZ, "002176", KlineCategory.DAY, 0, 100)
    print("江特电机 日K线:")
    print(df.to_string(index=False))
