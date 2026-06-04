# Page Design Documents

This directory is the source of truth for QuantBase page-level UI behavior. README links this directory instead of duplicating every page specification.

When a page changes, update the matching page document in the same pull request. The community edition does not bundle production screenshots by default. If screenshots are added later, capture them from a real running app and document the source, viewport, data state, and whether any request interception or DOM injection was used.

| Page | Route | Design doc | Screenshot status |
| --- | --- | --- | --- |
| 首页 | `/` | [home.md](home.md) | not bundled |
| 行情 | `/market` | [market.md](market.md) | not bundled |
| 策略 | `/strategy` | [strategy.md](strategy.md) | not bundled |
| 回测 | `/backtest` | [backtest.md](backtest.md) | not bundled |
| 套利中心 | `/arbitrage` | [arbitrage.md](arbitrage.md) | not bundled |
| 链上 | `/onchain` | [onchain.md](onchain.md) | not bundled |
| 模拟盘 | `/live` | [simulation.md](simulation.md) | not bundled |
| 实盘 | `/live-real` | [live-real.md](live-real.md) | hidden from sidebar |
| 盯盘 | `/watch` | [watch.md](watch.md) | hidden from sidebar |
| 信号中心 | `/signals` | [signals.md](signals.md) | not bundled |
| 监控 | `/monitor` | [monitor.md](monitor.md) | not bundled |
| 数据中心 | `/data` | [data.md](data.md) | not bundled |
| AI研发 | `/ai-lab` | [ai-lab.md](ai-lab.md) | not bundled |

`/trading` redirects to `/` and does not have a standalone page contract.
