"""演示：获取历史逐笔成交数据。date 参数为 YYYYMMDD 格式的整数。"""

from easy_tdx import Market, TdxClient

with TdxClient.from_best_host() as c:
    date = 20250110
    df = c.get_history_transaction_data(Market.SH, "600000", date, 0, 20)
    df["方向"] = df["buyorsell"].map({0: "买", 1: "卖", 2: "中性", 8: "集合竞价"})
    print(f"浦发银行 {date} 最近 {len(df)} 笔成交:")
    print(df[["datetime", "price", "vol", "方向"]].to_string(index=False))
