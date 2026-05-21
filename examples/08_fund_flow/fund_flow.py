"""演示：获取个股当日资金流向（基于 L1 逐笔数据统计）。

资金分为四级: 超大(>100万)、大(20-100万)、中(4-20万)、小(<4万)。
"""

import pandas as pd
from easy_tdx import Market, TdxClient

with TdxClient.from_best_host() as c:
    flow = c.get_fund_flow(Market.SH, "600519")
    # flow 是单行 DataFrame，转换为万元便于阅读
    in_cols = ["super_in", "large_in", "medium_in", "small_in"]
    out_cols = ["super_out", "large_out", "medium_out", "small_out"]
    df = pd.DataFrame(
        {
            "级别": ["超大单", "大单", "中单", "小单"],
            "流入(亿)": [flow[c].iloc[0] / 1e8 for c in in_cols],
            "流出(亿)": [flow[c].iloc[0] / 1e8 for c in out_cols],
        }
    )
    df["净流入(亿)"] = df["流入(亿)"] - df["流出(亿)"]
    print("贵州茅台 当日资金流向:")
    print(df.to_string(index=False))
