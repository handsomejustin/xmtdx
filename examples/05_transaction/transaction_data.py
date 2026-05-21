"""演示：获取当日逐笔成交数据。"""

from easy_tdx import Market, TdxClient

with TdxClient.from_best_host() as c:
    df = c.get_transaction_data(Market.SH, "600000", 0, 20)
    df["方向"] = df["buyorsell"].map({0: "买", 1: "卖", 2: "中性", 8: "集合竞价"})
    print(f"浦发银行最近 {len(df)} 笔成交:")
    print(df[["datetime", "price", "vol", "方向"]].to_string(index=False))
