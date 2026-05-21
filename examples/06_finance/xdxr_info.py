"""演示：获取除权除息历史记录。"""

from easy_tdx import Market, TdxClient

with TdxClient.from_best_host() as c:
    df = c.get_xdxr_info(Market.SH, "600519")
    print(f"贵州茅台 除权除息记录，共 {len(df)} 条:")
    print(df.tail(10).to_string(index=False))
