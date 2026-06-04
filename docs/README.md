# QuantBase 文档导航

## 推荐阅读路线

- 新用户：先看 `README.md`、`docs/spec.md`、`docs/OPEN_SOURCE_SCOPE.md`。
- 开发者：再看 `docs/ARCHITECTURE.md`、`docs/codebase-guide.md`、`docs/Technical_Reference.md`。
- 页面改动：先看 `docs/pages/README.md`，再看对应页面文档。
- 策略改动：先看 `docs/strategy_development_guide.md` 和 `data/seed/strategies.json`。
- 风险边界：看 `docs/DISCLAIMER.md`、`SECURITY.md` 和 `CONTRIBUTING.md`。

## 核心入口

| 文档 | 用途 |
| --- | --- |
| `README.md` | 项目对外入口、快速开始、功能地图 |
| `docs/spec.md` | 公开版产品规格和默认边界 |
| `docs/OPEN_SOURCE_SCOPE.md` | 社区版保留内容、剥离内容和公开表述规则 |
| `docs/ARCHITECTURE.md` | 技术分层、数据流、持久化和验证入口 |
| `docs/codebase-guide.md` | 代码框架导览，帮助快速熟悉入口文件和主流程 |
| `docs/progress.md` | 当前整理进度和已知边界 |
| `docs/DISCLAIMER.md` | 风险、投资建议、AI 和第三方服务免责声明 |

## 页面文档

页面文档统一放在 `docs/pages/`。修改一级页面时，建议同步更新对应页面文档。

| 文档 | 内容 |
| --- | --- |
| `docs/pages/README.md` | 页面文档索引和维护规则 |
| `docs/pages/home.md` | 首页 |
| `docs/pages/market.md` | 行情 |
| `docs/pages/strategy.md` | 策略中心 |
| `docs/pages/backtest.md` | 回测 |
| `docs/pages/arbitrage.md` | 跨所套利 |
| `docs/pages/onchain.md` | 链上研究 |
| `docs/pages/simulation.md` | 模拟盘 |
| `docs/pages/live-real.md` | 实盘工作台 |
| `docs/pages/signals.md` | 信号中心 |
| `docs/pages/watch.md` | 盯盘 |
| `docs/pages/monitor.md` | 监控 |
| `docs/pages/data.md` | 数据中心 |
| `docs/pages/ai-lab.md` | AI 研发 |
| `docs/pages/login.md` | 登录门禁 |

## 策略和市场资料

这些文档偏学习和工程参考，不是收益承诺：

| 文档 | 内容 |
| --- | --- |
| `docs/Quant_Strategies_Guide.md` | 量化策略基础材料 |
| `docs/strategy_development_guide.md` | 策略开发指南 |
| `docs/Strategy_Research_Notes.md` | 策略研究笔记 |
| `docs/Crypto_Beginner_Guide.md` | 加密货币基础 |
| `docs/Top50_Cryptocurrencies.md` | Top50 加密资产资料 |
| `docs/Exchange_Fees_API_Analysis.md` | 交易所费率和 API 参考 |
| `docs/okx_signal_bot_json_format.md` | OKX Signal Bot payload 格式 |

## 质量和参考

| 目录/文档 | 内容 |
| --- | --- |
| `docs/qa/` | QA 模板和质量记录 |
| `docs/references/harness-design-long-running-apps.md` | 长运行应用脚本设计参考 |
| `docs/Technical_Reference.md` | 技术参考和模块说明 |

## 新文档放哪里

- 页面相关：放 `docs/pages/`。
- 长期产品边界：更新 `docs/spec.md`。
- 架构或代码框架长期说明：更新 `docs/ARCHITECTURE.md` 或 `docs/codebase-guide.md`。
- 策略开发说明：更新 `docs/strategy_development_guide.md`。
- 完成一轮整理：更新 `docs/progress.md`。
