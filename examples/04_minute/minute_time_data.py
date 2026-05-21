"""演示：获取今日分时数据（240 条）。"""

from easy_tdx import Market, TdxClient

with TdxClient.from_best_host() as c:
    df = c.get_minute_time_data(Market.SH, "600000")
    print(f"浦发银行今日分时，共 {len(df)} 条:")
    print(df.head(20).to_string(index=False))
