"""演示：获取指数 K 线数据。

常用指数代码:
  上证指数: Market.SH, "000001"
  深证成指: Market.SZ, "399001"
  创业板指: Market.SZ, "399006"
"""

from easy_tdx import KlineCategory, Market, TdxClient

with TdxClient.from_best_host() as c:
    df = c.get_index_bars(Market.SH, "999999", KlineCategory.DAY, 0, 10)
    print("上证指数 日K线:")
    print(df.to_string(index=False))
