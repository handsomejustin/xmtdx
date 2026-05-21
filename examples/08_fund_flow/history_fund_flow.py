"""演示：获取个股历史日线资金流向序列。"""

from easy_tdx import Market, TdxClient

with TdxClient.from_best_host() as c:
    df = c.get_history_fund_flow(Market.SH, "600519", 0, 10)
    print(f"贵州茅台 历史资金流向，共 {len(df)} 天:")
    print(df.to_string(index=False))
