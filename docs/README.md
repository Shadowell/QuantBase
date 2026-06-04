# QuantBase 文档导航

## 推荐阅读路线

- 新用户：先看 `README.md`、`docs/spec.md`、`docs/open-source-scope.md`。
- 想本地跑起来：看 `docs/local-deployment.md`。
- 开发者：再看 `docs/architecture.md`、`docs/codebase-guide.md`。
- 页面改动：先看 `docs/pages/README.md`，再看对应页面文档。
- 策略改动：先看 `docs/strategy-development-guide.md` 和 `data/seed/strategies.json`。
- 风险边界：看 `docs/disclaimer.md`、`SECURITY.md` 和 `CONTRIBUTING.md`。

## 核心入口

| 文档 | 用途 |
| --- | --- |
| `README.md` | 项目对外入口、快速开始、功能地图 |
| `docs/spec.md` | 公开版产品规格和默认边界 |
| `docs/open-source-scope.md` | 社区版保留内容、剥离内容和公开表述规则 |
| `docs/local-deployment.md` | 本地初始化、启动、健康检查和故障排查 |
| `docs/architecture.md` | 技术分层、数据流、持久化和验证入口 |
| `docs/codebase-guide.md` | 代码框架导览，帮助快速熟悉入口文件和主流程 |
| `docs/progress.md` | 当前整理进度和已知边界 |
| `docs/disclaimer.md` | 风险、投资建议、AI 和第三方服务免责声明 |

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

## 策略和协议资料

这些文档是当前产品和接口仍会直接引用的工程资料，不是收益承诺：

| 文档 | 内容 |
| --- | --- |
| `docs/strategy-development-guide.md` | 策略开发指南 |
| `docs/okx-signal-bot-json-format.md` | OKX Signal Bot payload 格式 |

历史研究、投资/期权/市场科普、旧技术参考和收益叙事类长文不随社区版发布。

## 质量和参考

| 目录/文档 | 内容 |
| --- | --- |
| `docs/qa/` | QA 模板和质量记录 |
| `docs/references/harness-design-long-running-apps.md` | 长运行应用脚本设计参考 |

## 新文档放哪里

- 页面相关：放 `docs/pages/`。
- 长期产品边界：更新 `docs/spec.md`。
- 架构或代码框架长期说明：更新 `docs/architecture.md` 或 `docs/codebase-guide.md`。
- 本地启动、检查或脚本行为：更新 `docs/local-deployment.md`。
- 策略开发说明：更新 `docs/strategy-development-guide.md`。
- 完成一轮整理：更新 `docs/progress.md`。
