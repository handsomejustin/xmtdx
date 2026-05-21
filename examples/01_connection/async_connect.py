"""演示：异步客户端连接与基本用法。"""

import asyncio

from easy_tdx import AsyncTdxClient, KlineCategory, Market


async def main():
    # 手动指定服务器
    async with AsyncTdxClient("180.153.18.170") as c:
        count = await c.get_security_count(Market.SH)
        print(f"沪市证券总数: {count}")

    # 自动优选服务器
    async with AsyncTdxClient.from_best_host() as c:
        df = await c.get_security_bars(Market.SH, "600000", KlineCategory.DAY, 0, 5)
        print(df.to_string(index=False))


asyncio.run(main())
