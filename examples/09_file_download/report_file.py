"""演示：通过 get_report_file 从服务器下载文件。

行情服务器（KNOWN_HOSTS）当前稳定提供的文件:
  'tdxhy.cfg'      - 行业映射配置（~149KB）
  'block_zs.dat'   - 行业/指数板块（~330KB）
  'block_gn.dat'   - 概念板块（~757KB）
  'block_fg.dat'   - 风格板块（~453KB）

计算服务器（CALC_HOSTS）提供专业财务数据:
  'tdxfin/gpcw.txt'                - 文件列表
  'tdxfin/gpcwYYYYMMDD.zip'        - 历史财报

行情服务器已失效（返回空包）:
  'base_info.zip', 'gpcw.txt'
"""

from pathlib import Path

from easy_tdx import CALC_HOSTS, TdxClient

OUTPUT_DIR = Path(__file__).parent / "downloads"

# 行情服务器可用的文件
AVAILABLE_FILES = [
    "tdxhy.cfg",
    "block_zs.dat",
    "block_gn.dat",
    "block_fg.dat",
]

# 行情服务器已失效的文件
PROBE_FILES = ["base_info.zip", "gpcw.txt"]

# ── 1. 行情服务器文件下载 ─────────────────────────────

with TdxClient.from_best_host() as c:
    OUTPUT_DIR.mkdir(exist_ok=True)

    print("=" * 50)
    print("探测已失效文件（预期返回空包）")
    print("=" * 50)
    for filename in PROBE_FILES:
        data = c.get_report_file(filename)
        status = "空包" if len(data) == 0 else f"{len(data):,} 字节"
        print(f"  {filename}: {status}")

    print()
    print("=" * 50)
    print("下载可用文件")
    print("=" * 50)
    for filename in AVAILABLE_FILES:
        data = c.get_report_file(filename)
        out_path = OUTPUT_DIR / filename
        out_path.write_bytes(data)
        print(f"  {filename} ({len(data):,} 字节) 已保存")

    print()
    print("=" * 50)
    print("行业板块 (block_zs.dat)")
    print("=" * 50)
    blocks = c.get_block_info("block_zs.dat")
    print(blocks[["name", "category", "count"]].head(5).to_string(index=False))
    print(f"  ... 共 {len(blocks)} 个")

# ── 2. 计算服务器：专业财务数据 ────────────────────────

print()
print("=" * 50)
print("专业财务数据（计算服务器）")
print("=" * 50)

calc_host = CALC_HOSTS[0]
with TdxClient(calc_host) as c:
    # 获取文件列表
    file_list = c.get_financial_file_list()
    print(file_list.head(5).to_string(index=False))
    print(f"  ... 共 {len(file_list)} 个文件")

    # 下载并解析最近一期有实际数据的财报
    real_files = file_list[file_list["filesize"] > 10000]
    if not real_files.empty:
        latest = real_files.iloc[0]
        fname = f"tdxfin/{latest['filename']}"
        print(f"\n下载: {fname} ({latest['filesize']:,} 字节)")

        # 保存原始 .zip
        zip_data = c.get_financial_file(fname)
        zip_path = OUTPUT_DIR / latest["filename"]
        zip_path.write_bytes(zip_data)
        print(f"  .zip 已保存到 {zip_path}")

        # 解析财报记录
        records = c.get_financial_records(fname)
        print(f"  解析出 {len(records)} 只股票")
        if not records.empty:
            print(records[["market", "code", "report_date"]].head(5).to_string(index=False))
            print(f"    ... 共 {len(records)} 只")
