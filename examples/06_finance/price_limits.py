"""演示：计算个股涨跌停价格。"""

from easy_tdx import Market, TdxClient

CODE = "600519"
NAME = "贵州茅台"

with TdxClient.from_best_host() as c:
    quotes = c.get_security_quotes([(Market.SH, CODE)])
    if not quotes.empty:
        q = quotes.iloc[0]
        limit_up, limit_down = c.get_price_limits(Market.SH, CODE, NAME, q["pre_close"])
        print(f"代码: {CODE}  名称: {NAME}")
        print(f"昨收: {q['pre_close']}")
        print(f"涨停价: {limit_up}")
        print(f"跌停价: {limit_down}")
