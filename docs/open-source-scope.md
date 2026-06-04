# QuantBase Open Source Scope

QuantBase 是公开社区版。它用于展示量化研究和工程验证框架，同时把私有策略资产和生产运维细节留在私有仓库之外。

社区版的目标是让开发者可以审阅架构、运行本地工作台、扩展策略合同、复现 paper/simulation 流程，并理解风险边界。它不是投资产品、托管服务或生产部署模板。

## 保留内容

- FastAPI 后端和 React/TypeScript 前端。
- 公开行情同步路径。
- 策略注册框架和 `BaseStrategy` 合同。
- Backtrader 回测集成。
- Paper broker 和 paper account 建模。
- 信号与事件审计结构。
- 架构、页面、风险边界和本地验证文档。
- 用于解释配置结构、并让用户初始化后能看到模拟策略的 10 个 demo seed。

## 移除或替换内容

- 私有 `.env` 文件和生产密钥。
- 运行时数据库、日志、K 线缓存和生成截图。
- 生产部署 workflow 与服务器专属部署脚本。
- 私有研究合同、历史进度记录和生产策略验证记录。
- 编码私有参数或收益叙事的策略 seed。

## 可选能力

部分模块支持交易所私有 API、AI provider、通知渠道或真实执行研究路径。QuantBase 社区版要求这些能力默认关闭，只有操作者自行配置后才启用。真实账户读取和实盘执行需要 `QUANTBASE_LIVE_TRADING_ENABLED=1`；MCP 实盘变更工具还需要 `QUANTBASE_MCP_ENABLE_LIVE_TRADING=1`。公开示例应聚焦研究和模拟。

前端社区版不包含登录页面。后端仍保留可选 session 中间件和 auth API，供二次开发或部署层集成；公开默认配置必须保持关闭。

## 本地运行边界

- 本地运行脚本只面向开发和演示：`init.sh`、`start.sh`、`status.sh`、`stop.sh`、`restart.sh`。
- `docs/local-deployment.md` 记录本地初始化、启动、健康检查和故障排查。
- 生产域名、反向代理、远程数据库、runner、服务器路径、密钥注入和监控告警由使用者自行设计，不随社区版发布。

## 公开表述规则

- 不把 demo 策略描述为可盈利系统。
- 不把私有账户结果作为项目证据。
- 不把项目描述成投资建议、资产管理、跟单或托管执行服务。
- 不用伪造 K 线、mock 结果或截图概念图证明策略质量。
