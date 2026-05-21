"""演示：获取全市场涨跌统计概况。"""

from easy_tdx import TdxClient

with TdxClient.from_best_host() as c:
    stat = c.get_market_stat()
    print(stat)
