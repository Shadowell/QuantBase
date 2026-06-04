# QuantBase

![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-Async-009688?logo=fastapi&logoColor=white)
![React](https://img.shields.io/badge/React-18-61DAFB?logo=react&logoColor=0B1220)
![TypeScript](https://img.shields.io/badge/TypeScript-Frontend-3178C6?logo=typescript&logoColor=white)
![License](https://img.shields.io/badge/License-Apache--2.0-blue)

English | [简体中文](README.md)

QuantBase is an open-source quantitative research base for real market-data sync, backtesting, paper/simulation execution, signal audit, and risk review. It keeps the engineering loop while defaulting away from real funds, return promises, and investment advice.

> Risk notice: QuantBase is for technical research, engineering validation, backtesting, and paper simulation. Strategies, signals, AI output, examples, dashboards, and backtest results are not investment advice, return guarantees, or risk guarantees. Real trading, leverage, derivatives, and automated order placement can cause material losses. See [docs/DISCLAIMER.md](docs/DISCLAIMER.md).

## What It Is

QuantBase is not a single trading script. It is a full-stack research workbench:

| Area | Description |
| --- | --- |
| System type | Quant research, backtesting, paper execution, and audit stack |
| Stack | Python 3.11, FastAPI, SQLite, React 18, TypeScript, Vite, Backtrader, CCXT |
| Default boundary | Paper/simulation only; real-account execution is disabled by default |
| Data principle | Prefer real public market data and explicit failures over fabricated candles |
| Open-source scope | Framework, strategy interface, backtest path, paper broker, audit records, UI workbench, and demo seeds |

## Core Capabilities

- Public market-data and K-line synchronization.
- Unified `BaseStrategy` interface for strategy registration.
- Backtrader-based historical validation.
- Paper broker for simulated accounts, positions, trades, equity, fees, funding, and risk events.
- Signal audit records for strategy behavior review.
- React workbench for market, strategy, backtest, simulation, monitor, data, and AI research pages.

## Quick Start

```bash
git clone https://github.com/Shadowell/QuantBase.git
cd QuantBase
./init.sh
```

Run locally:

```bash
./start.sh
# Frontend: http://localhost:8888
# Backend:  http://localhost:8889/api/v2
```

Run non-service checks:

```bash
./scripts/check.sh
```

## Configuration

```bash
cp backend/.env.example backend/.env
```

Keep exchange private keys, notification webhooks, AI provider keys, and deployment secrets out of git. The 10 demo seeds in `data/seed/strategies.json` cover spot CTA, contract CTA, Donchian, grid, martingale, market making, market neutral, cross-exchange funding, and low-leverage trend examples. They are examples only, not trading recommendations.

Kairos/SuperPnL local model inference is optional. The base install does not pull torch or model repositories. Install the optional stack only when needed:

```bash
pip install -r backend/requirements-ai.txt
```

## Documentation

| Document | Purpose |
| --- | --- |
| [docs/README.md](docs/README.md) | Documentation map |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Architecture and data flow |
| [docs/spec.md](docs/spec.md) | Product behavior and boundaries |
| [docs/pages/](docs/pages/) | Page-level design docs |
| [docs/OPEN_SOURCE_SCOPE.md](docs/OPEN_SOURCE_SCOPE.md) | Community-edition scope |
| [docs/DISCLAIMER.md](docs/DISCLAIMER.md) | Risk disclaimer |

## Governance

- License: Apache-2.0, see [LICENSE](LICENSE).
- Contribution guide: [CONTRIBUTING.md](CONTRIBUTING.md).
- Security reporting: [SECURITY.md](SECURITY.md).

QuantBase exists to make quant research infrastructure easier to inspect, reuse, and extend. Research first, simulate second, and treat any live execution as your own responsibility.
