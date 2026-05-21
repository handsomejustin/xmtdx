"""演示：获取板块信息（行业、概念、风格）。

常用板块文件:
  'block_zs.dat' - 行业/指数板块
  'block_gn.dat' - 概念板块
  'block_fg.dat' - 风格板块
"""

from easy_tdx import TdxClient

with TdxClient.from_best_host() as c:
    df = c.get_block_info("block_gn.dat")
    print(f"概念板块，共 {len(df)} 个:")
    print(df[["name", "category", "count"]].head(20).to_string(index=False))
