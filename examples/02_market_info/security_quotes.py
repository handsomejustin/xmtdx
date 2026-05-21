"""演示：批量获取实时五档行情。最多支持 80 只/次。"""

from easy_tdx import Market, TdxClient

with TdxClient.from_best_host() as c:
    stocks = [
        (Market.SH, "600000"),  # 浦发银行
        (Market.SH, "600519"),  # 贵州茅台
        (Market.SZ, "000001"),  # 平安银行
        (Market.SZ, "000858"),  # 五粮液
    ]
    df = c.get_security_quotes(stocks)
    df["change_pct"] = (df["price"] - df["pre_close"]) / df["pre_close"] * 100
    print(
        df[
            ["code", "price", "change_pct", "open", "high", "low", "pre_close", "vol", "amount"]
        ].to_string(index=False)
    )
