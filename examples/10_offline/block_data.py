"""演示：板块数据读取（本地 + 网络自动回退）。

系统板块获取优先级：
  1. 本地 .dat 文件（离线读取）
  2. TDX 服务器在线获取（自动回退）

自定义板块仅支持本地读取。
"""

from pathlib import Path

from easy_tdx import TdxClient
from easy_tdx.models.finance import TdxBlock
from easy_tdx.offline import detect_tdx_home, read_block_dat, read_customer_blocks


def _print_blocks(blocks: list[TdxBlock], title: str) -> None:
    print(f"\n{title} ({len(blocks)} 个板块):")
    for block in blocks[:5]:
        codes_preview = ", ".join(block.codes[:5])
        suffix = "..." if len(block.codes) > 5 else ""
        print(f"  {block.name} ({block.count}只): {codes_preview}{suffix}")
    if len(blocks) > 5:
        print(f"  ... 还有 {len(blocks) - 5} 个板块")


home = detect_tdx_home()

# --- 系统板块 ---
print("=" * 60)
print("系统板块")
print("=" * 60)

vipdoc = Path(home) / "vipdoc" if home else None
block_names = ["block_zs.dat", "block_gn.dat", "block_fg.dat"]
block_labels = {
    "block_zs.dat": "行业板块",
    "block_gn.dat": "概念板块",
    "block_fg.dat": "风格板块",
}

need_fetch = []
for name in block_names:
    local_path = vipdoc / name if vipdoc else None
    if local_path and local_path.is_file():
        blocks = read_block_dat(local_path)
        _print_blocks(blocks, f"{block_labels[name]} ({name}, 本地)")
    else:
        print(f"\n{block_labels[name]}: 本地文件不存在，将从服务器获取")
        need_fetch.append(name)

# 本地没有的板块，通过网络获取
if need_fetch:
    print(f"\n正在连接服务器获取 {len(need_fetch)} 个板块文件...")
    with TdxClient.from_best_host() as c:
        for name in need_fetch:
            df = c.get_block_info(name)
            print(f"\n{block_labels[name]} ({name}, 网络) ({len(df)} 个板块):")
            for _, row in df.head(5).iterrows():
                codes = row["codes"]
                codes_preview = ", ".join(str(c) for c in codes[:5])
                suffix = "..." if len(codes) > 5 else ""
                print(f"  {row['name']} ({row['count']}只): {codes_preview}{suffix}")
            if len(df) > 5:
                print(f"  ... 还有 {len(df) - 5} 个板块")

# --- 自定义板块 ---
print(f"\n{'=' * 60}")
print("自定义板块")
print("=" * 60)

if home:
    blocknew_dir = Path(home) / "T0002" / "blocknew"
    if blocknew_dir.is_dir():
        blocks = read_customer_blocks(blocknew_dir)
        if blocks:
            print(f"\n共 {len(blocks)} 个自定义板块:")
            for block in blocks[:10]:
                codes_preview = ", ".join(block.codes[:5])
                suffix = "..." if len(block.codes) > 5 else ""
                print(f"  {block.blockname} ({len(block.codes)}只): {codes_preview}{suffix}")
        else:
            print("未找到自定义板块数据")
    else:
        print(f"自定义板块目录不存在: {blocknew_dir}")
else:
    print("需要本地通达信安装目录才能读取自定义板块")
