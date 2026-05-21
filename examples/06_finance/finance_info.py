"""演示：获取最新财务数据。"""

from easy_tdx import Market, TdxClient

with TdxClient.from_best_host() as c:
    info = c.get_finance_info(Market.SH, "600519")
    print("贵州茅台 最新财务数据:")
    print(info.T.to_string(header=False))
