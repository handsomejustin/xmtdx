# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Build / Test / Lint

```bash
# 单元测试（无需网络，使用 tests/fixtures/ 中的 hex 数据）
python -m pytest tests/unit/ -v

# 集成测试（需要网络，默认跳过）
XMTDX_LIVE=1 python -m pytest tests/integration/ -v

# 类型检查（strict mypy）
mypy src/

# lint + format
ruff check src/ tests/
ruff format --check src/ tests/
```

## 架构

```
src/easy_tdx/
├── client.py          # TdxClient / AsyncTdxClient（高层 API）
├── transport/
│   ├── sync.py        # TdxConnection（socket）+ ping_host / ping_all
│   └── async_.py      # AsyncTdxConnection（asyncio）
├── commands/          # 每条命令：build_request() + parse_response()，无 IO
├── codec/             # price / volume / datetime / frame 编解码
└── models/            # 纯 dataclass，无业务逻辑
```

commands 层不依赖 transport，可独立单测。修改 codec 或 commands 时不需要网络。

## 协议编解码注意事项

- **价格编码**：变长有符号整数（类 LEB128），bit8=继续，bit7=符号。差分编码（相邻 tick 存 delta）。
- **成交量编码**：4 字节自定义浮点（`_decode_volume`），字节 3=指数，字节 0-2=精度。**不可用于价格字段**。
- **握手**：连接后必须顺序发送 3 条 setup 命令，响应丢弃。
- **帧格式**：16 字节响应头，body 按需 zlib 解压。
- 新增编解码逻辑时务必在 `tests/fixtures/` 中补充 hex fixture 并编写对应的离线解析测试。

## 已知限制

- `Market.BJ` 的 `get_security_list()` 不能稳定获取（服务器端问题），不要尝试依赖它。
- `limit_up` / `limit_down` 在 `SecurityQuote` 中默认为 `None`，涨跌停价应通过 `get_price_limits()` 或 `compute_price_limits()` 计算。

## 代码风格

- ruff: line-length 100, target py310, rules: E/F/I/UP
- mypy strict mode
- 所有 `get_*` 公开方法返回 `pd.DataFrame`（通过 `_df._to_df()` 转换）。内部方法仍使用 dataclass 列表。
- 依赖：pandas（>=2.0）、tzdata（>=2024.1）。
