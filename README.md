# QuantBase

![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-Async-009688?logo=fastapi&logoColor=white)
![React](https://img.shields.io/badge/React-18-61DAFB?logo=react&logoColor=0B1220)
![TypeScript](https://img.shields.io/badge/TypeScript-Frontend-3178C6?logo=typescript&logoColor=white)
![License](https://img.shields.io/badge/License-Apache--2.0-blue)

[English](README_EN.md) | 简体中文

QuantBase 是一个开源量化研究工作台，用于真实行情同步、策略回测、paper/simulation 执行、信号审计和风险复盘。它保留从数据、策略、回测到模拟执行的工程闭环，但默认不连接真实资金，不提供收益承诺，也不把任何示例策略视为投资建议。

> 风险声明：QuantBase 仅用于技术研究、工程验证、回测和模拟盘演练。任何策略、信号、AI 输出、示例配置、界面展示或回测结果都不构成投资建议、收益承诺或风险承诺。真实交易、杠杆、衍生品和自动化下单都可能造成重大损失。完整说明见 [docs/disclaimer.md](docs/disclaimer.md)。

## 项目定位

QuantBase 不是单个交易脚本，而是一套可扩展、可审计、可二次开发的研究基础设施。

| 维度 | 说明 |
| --- | --- |
| 工程类型 | 全栈量化研究、回测、模拟执行和审计系统 |
| 主要技术 | Python 3.11、FastAPI、SQLite、React 18、TypeScript、Vite、Backtrader、CCXT |
| 默认边界 | paper/simulation first；真实账户读取和实盘执行默认关闭 |
| 数据原则 | 优先使用真实公开行情；缺数据应显式失败，不用 mock K 线伪装成功 |
| 开源范围 | 工程框架、策略接口、回测路径、模拟撮合、审计记录、页面工作台和公开 demo seed |

## 核心能力

- **行情与 K 线同步**：从公开交易所接口同步行情，并维护本地文件 K 线缓存和同步状态。
- **策略注册框架**：通过统一 `BaseStrategy` 合同接入现货、合约、套利、AI 辅助等策略类型。
- **Backtrader 回测**：使用真实历史 K 线和明确手续费/滑点配置生成可复查结果。
- **paper broker**：记录模拟账户、持仓、成交、权益曲线、手续费、资金费和风险事件。
- **信号与事件审计**：保留策略信号、投递状态、执行事件和操作日志，便于复盘。
- **前端工作台**：提供市场、策略、回测、模拟盘、监控、数据中心和 AI 研发页面。

## 安全默认值

- 本仓库默认面向研究、回测和模拟执行。
- 真实账户读取与实盘执行需要显式设置 `QUANTBASE_LIVE_TRADING_ENABLED=1`。
- MCP 实盘变更工具还需要单独设置 `QUANTBASE_MCP_ENABLE_LIVE_TRADING=1`。
- 前端社区版不提供登录页面；如需访问控制，请在二次开发或部署层自行接入。
- `.env`、API keys、webhook、生产数据库、日志和私有账户截图不得提交到仓库。

## 快速开始

需要本机具备 Python 3.11+、Node.js 18+ 和 npm。

```bash
git clone https://github.com/Shadowell/QuantBase.git
cd QuantBase
./init.sh
```

启动本地服务：

```bash
./start.sh
# Frontend: http://localhost:8888
# Backend:  http://localhost:8889
```

查看状态和停止服务：

```bash
./status.sh
./stop.sh
```

不启动长服务的基础检查：

```bash
./scripts/check.sh
```

更完整的本地运行说明见 [docs/local-deployment.md](docs/local-deployment.md)。

## 环境变量

复制后端环境模板：

```bash
cp backend/.env.example backend/.env
```

默认模板只包含占位符和安全关闭状态。涉及交易所私有 API、通知 webhook、AI provider key 或任何真实账户能力时，请只写入本地 `.env` 或部署密钥，不要提交到仓库。

Kairos/SuperPnL 这类本地模型能力是可选重依赖。基础安装不会拉取 torch 或模型仓库；需要本地模型推理时再安装：

```bash
pip install -r backend/requirements-ai.txt
```

## 示例策略

`data/seed/strategies.json` 内置 10 个社区版 demo seed，用于展示策略配置结构和 paper/simulation 启动方式。它们覆盖现货 CTA、合约 CTA、Donchian、网格、马丁、做市、市场中性、跨所资金费率和低杠杆趋势等样例；这些不是实盘建议，也不是经过收益目标筛选的交易资产。

## 项目结构

```text
backend/      FastAPI 应用、策略运行时、回测、paper broker 和持久化
frontend/     React/Vite 工作台
data/seed/    社区版 demo 策略 seed
docs/         产品规格、架构、页面和本地运行文档
scripts/      检查、种子导入和辅助脚本
tests/        风险边界、静态合同和核心服务测试
```

## 文档入口

| 文档 | 内容 |
| --- | --- |
| [docs/README.md](docs/README.md) | 文档总导航 |
| [docs/local-deployment.md](docs/local-deployment.md) | 本地部署和运行手册 |
| [docs/architecture.md](docs/architecture.md) | 技术架构和数据流 |
| [docs/spec.md](docs/spec.md) | 产品规格和行为边界 |
| [docs/pages/](docs/pages/) | 页面级设计文档 |
| [docs/open-source-scope.md](docs/open-source-scope.md) | 开源社区版保留与剥离范围 |
| [docs/disclaimer.md](docs/disclaimer.md) | 免责声明 |
| [docs/okx-signal-bot-json-format.md](docs/okx-signal-bot-json-format.md) | OKX Signal Bot 自定义 JSON payload 参考 |

## 开源治理

- License: Apache-2.0，见 [LICENSE](LICENSE)。
- 贡献说明见 [CONTRIBUTING.md](CONTRIBUTING.md)。
- 安全问题请按 [SECURITY.md](SECURITY.md) 报告。
- 发布前请使用 [docs/open-source-release-checklist.md](docs/open-source-release-checklist.md) 做公开边界检查。

QuantBase 的目标是提供一套可靠、可审计、可扩展的量化研究基础设施。请先研究，再模拟，最后再由你自己判断是否需要任何真实执行。
