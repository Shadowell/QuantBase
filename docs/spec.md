# QuantBase Product Spec

## 产品定位

QuantBase 是面向量化策略研究、工程验证和模拟运行的开源框架。它把行情同步、策略注册、Backtrader 回测、paper broker、信号审计、监控页面和可选 AI 研究能力放在同一个工作流里，帮助开发者验证策略想法，而不是提供投资建议或托管交易服务。

## 默认边界

- 默认运行 paper/simulation。
- Demo seed 仅用于展示配置形状和平台流程。
- 真实账户、交易所私有 API、通知 webhook 和 AI provider key 都必须由操作者本地配置，并默认关闭。
- 真实账户读取和实盘执行默认关闭；相关 HTTP 入口需要操作者显式设置 `QUANTBASE_LIVE_TRADING_ENABLED=1`。
- MCP 实盘变更工具需要单独显式设置 `QUANTBASE_MCP_ENABLE_LIVE_TRADING=1`，不能因为配置了交易所密钥而自动开启。
- 前端社区版不提供登录页面；`QUANTBASE_AUTH_ENABLED=1` 仅适合二次开发、API 调试或由部署层提供访问控制的场景。
- 任何真实交易、信号投递或账户读取风险都由操作者自行承担。
- 文档、示例、回测和模型输出不得被描述为收益承诺。

## 主要用户

- 研究者：需要可重复的策略回测和模拟运行流程。
- 开发者：需要扩展策略、broker、API、页面和数据服务。
- 运维/操作者：需要观察模拟策略状态、诊断事件、数据覆盖和审计记录。

## 核心流程

1. 同步或读取真实市场数据。
2. 通过 `BaseStrategy` 编写或选择策略。
3. 使用 Backtrader 路径回测。
4. 启动 paper/simulation 实例。
5. 查看权益、成交、持仓、事件和诊断。
6. 可选地配置信号审计、通知或私有执行路径。

## 技术形状

- Frontend：React、TypeScript、Vite、Tailwind、ECharts。
- Backend：FastAPI、async Python、SQLite、CCXT/OKX public data path。
- Runtime：`StrategyEngine`、`PaperBroker`、`ContractPaperBroker`、Backtrader adapter。
- Storage：SQLite 保存策略、paper 实例、成交、事件、回测任务和同步状态；文件 K 线 store 保存历史 K 线。
- Seeds：`data/seed/strategies.json` 提供公开 demo 策略配置。
- Optional AI：Kairos/SuperPnL 本地模型依赖放在 `backend/requirements-ai.txt`，基础安装不拉取 torch 或模型仓库。

## 本地运行合同

- `./init.sh` 是首次运行入口，负责依赖安装、`.env` 模板复制、SQLite 初始化和 demo seed 导入。
- `./start.sh` 启动本地后端和前端，默认端口为后端 8889、前端 8888。
- `./status.sh` 检查 PID、端口、健康接口、日志和本地数据库。
- `./stop.sh` 停止本地服务。
- `./scripts/check.sh` 是不启动长服务的默认验证入口。
- 详细说明维护在 `docs/local-deployment.md`。

## 非目标

- 不提供投资建议、跟单、托管账户或收益承诺。
- 不内置生产服务器部署配置。
- 不提交真实账户数据、生产数据库、私有回测记录或运行日志。
- 不用 mock K 线或合成结果证明策略有效。

## 文档维护规则

- 长期产品边界写在本文件。
- 架构和数据流写在 `docs/architecture.md`。
- 页面工作流写在 `docs/pages/`。
- 开源边界写在 `docs/open-source-scope.md`。
- 每轮有意义的项目整理写入 `docs/progress.md`。

## 验收方向

一个可接受的公开变更应满足：

- 不引入真实密钥、私有服务器、生产数据库或个人账户信息。
- 新行为有清晰 paper/simulation 默认边界。
- 相关代码通过最小必要检查。
- 文档同步说明用户能如何理解和验证变化。
