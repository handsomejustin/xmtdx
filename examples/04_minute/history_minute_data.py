"""演示：获取历史某日分时数据。date 参数为 YYYYMMDD 格式的整数。"""

from easy_tdx import Market, TdxClient

with TdxClient.from_best_host() as c:
    date = 20250110
    df = c.get_history_minute_time_data(Market.SH, "600000", date)
    print(f"浦发银行 {date} 分时数据，共 {len(df)} 条:")
    print(df.head(20).to_string(index=False))
