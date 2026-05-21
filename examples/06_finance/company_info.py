"""演示：获取公司信息目录与各个分类的详细内容。"""

from easy_tdx import Market, TdxClient

CODE = "600519"
NAME = "贵州茅台"
MARKET = Market.SH

# 要展示的分类，按需注释/取消注释
SHOW_CATEGORIES = [
    "最新提示",
    "公司概况",
    "财务分析",
    "股本结构",
    "股东研究",
    "机构持股",
    "分红融资",
    "高管治理",
    "资金动向",
    "资本运作",
    "热点题材",
    "公司公告",
    "公司报道",
    "经营分析",
    "行业分析",
    "研报评级",
]


def show_category_content(client, categories, category_name, max_chars=500):
    """获取并展示指定分类的内容。"""
    row = categories[categories["name"] == category_name]
    if row.empty:
        print(f"  未找到分类: {category_name}")
        return

    r = row.iloc[0]
    content = client.get_company_info_content(
        MARKET, CODE, r["filename"], int(r["start"]), int(r["length"])
    )
    text = content.strip()
    if len(text) > max_chars:
        text = text[:max_chars] + f"\n... (共 {len(content.strip())} 字，仅显示前 {max_chars} 字)"
    print(f"\n{'=' * 60}")
    print(f"【{r['name']}】 (共 {r['length']} 字节)")
    print(f"{'=' * 60}")
    print(text)


with TdxClient.from_best_host() as c:
    categories = c.get_company_info_category(MARKET, CODE)

    # 1. 显示目录
    print(f"{NAME} 公司信息目录:")
    print(categories.to_string(index=False))

    # 2. 显示所有分类内容（每个分类默认只显示前500字）
    for name in SHOW_CATEGORIES:
        show_category_content(c, categories, name)

    # 3. 也可以单独获取某个分类的完整内容，例如：
    # show_category_content(c, categories, "公司概况", max_chars=99999)
