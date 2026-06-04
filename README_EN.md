# QuantBase

![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-Async-009688?logo=fastapi&logoColor=white)
![React](https://img.shields.io/badge/React-18-61DAFB?logo=react&logoColor=0B1220)
![TypeScript](https://img.shields.io/badge/TypeScript-Frontend-3178C6?logo=typescript&logoColor=white)
![License](https://img.shields.io/badge/License-Apache--2.0-blue)

English | [简体中文](README.md)

QuantBase is an open-source quantitative research workbench for real market-data sync, backtesting, paper/simulation execution, signal audit, and risk review. It keeps the engineering loop from data to strategy to simulated execution while defaulting away from real funds, return promises, and investment advice.

> Risk notice: QuantBase is for technical research, engineering validation, backtesting, and paper simulation. Strategies, signals, AI output, examples, dashboards, and backtest results are not investment advice, return guarantees, or risk guarantees. Real trading, leverage, derivatives, and automated order placement can cause material losses. See [docs/disclaimer.md](docs/disclaimer.md).

## What It Is

QuantBase is not a single trading script. It is an extensible, inspectable research infrastructure stack.

| Area | Description |
| --- | --- |
| System type | Full-stack quant research, backtesting, paper execution, and audit system |
| Stack | Python 3.11, FastAPI, SQLite, React 18, TypeScript, Vite, Backtrader, CCXT |
| Default boundary | Paper/simulation first; real-account reads and live execution are disabled by default |
| Data principle | Prefer real public market data and explicit failures over fabricated candles |
| Open-source scope | Framework, strategy interface, backtest path, paper broker, audit records, UI workbench, and public demo seeds |

## Core Capabilities

- Public market-data and K-line synchronization.
- Unified `BaseStrategy` contract for strategy registration.
- Backtrader-based historical validation using explicit fee and slippage assumptions.
- Paper broker for simulated accounts, positions, trades, equity, fees, funding, and risk events.
- Signal and event audit records for strategy behavior review.
- React workbench for market, strategy, backtest, simulation, monitor, data, and AI research pages.

## Safety Defaults

- The repository defaults to research, backtesting, and paper simulation.
- Real-account reads and live execution require `QUANTBASE_LIVE_TRADING_ENABLED=1`.
- MCP live mutation tools also require `QUANTBASE_MCP_ENABLE_LIVE_TRADING=1`.
- The community frontend does not ship a login page; add access control in your own fork or deployment layer if needed.
- Do not commit `.env` files, API keys, webhooks, production databases, logs, or screenshots with private account data.

## Quick Start

Install Python 3.11+, Node.js 18+, and npm first.

```bash
git clone https://github.com/Shadowell/QuantBase.git
cd QuantBase
./init.sh
```

Run locally:

```bash
./start.sh
# Frontend: http://localhost:8888
# Backend:  http://localhost:8889
```

Check status and stop services:

```bash
./status.sh
./stop.sh
```

Run non-service checks:

```bash
./scripts/check.sh
```

See [docs/local-deployment.md](docs/local-deployment.md) for the full local operations guide.

## Configuration

```bash
cp backend/.env.example backend/.env
```

The default template contains placeholders and safe disabled states only. Keep exchange private keys, notification webhooks, AI provider keys, and deployment secrets out of git.

Kairos/SuperPnL local model inference is optional. The base install does not pull torch or model repositories. Install the optional stack only when needed:

```bash
pip install -r backend/requirements-ai.txt
```

## Demo Strategies

`data/seed/strategies.json` contains 10 demo seeds for the community edition. They illustrate strategy configuration and paper/simulation startup paths across spot CTA, contract CTA, Donchian, grid, martingale, market making, market neutral, cross-exchange funding, and low-leverage trend examples. They are examples only, not trading recommendations.

## Project Layout

```text
backend/      FastAPI app, strategy runtime, backtesting, paper broker, persistence
frontend/     React/Vite workbench
data/seed/    Community demo strategy seeds
docs/         Product, architecture, page, and local operations docs
scripts/      Checks, seed import, and helper scripts
tests/        Risk-boundary, static-contract, and service tests
```

## Documentation

| Document | Purpose |
| --- | --- |
| [docs/README.md](docs/README.md) | Documentation map |
| [docs/local-deployment.md](docs/local-deployment.md) | Local deployment and operations guide |
| [docs/architecture.md](docs/architecture.md) | Architecture and data flow |
| [docs/spec.md](docs/spec.md) | Product behavior and boundaries |
| [docs/pages/](docs/pages/) | Page-level design docs |
| [docs/open-source-scope.md](docs/open-source-scope.md) | Community-edition scope |
| [docs/disclaimer.md](docs/disclaimer.md) | Risk disclaimer |
| [docs/okx-signal-bot-json-format.md](docs/okx-signal-bot-json-format.md) | OKX Signal Bot custom JSON payload reference |

## Governance

- License: Apache-2.0, see [LICENSE](LICENSE).
- Contribution guide: [CONTRIBUTING.md](CONTRIBUTING.md).
- Security reporting: [SECURITY.md](SECURITY.md).
- Before publishing, run through [docs/open-source-release-checklist.md](docs/open-source-release-checklist.md).

QuantBase exists to make quant research infrastructure easier to inspect, reuse, and extend. Research first, simulate second, and treat any live execution as your own responsibility.
