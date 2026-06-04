# Local Deployment Guide

This guide covers the repository-owned local workflow for running QuantBase on a developer machine or demo host. It is not a production deployment guide.

## Requirements

- Python 3.11 or newer
- Node.js 18 or newer
- npm
- `curl` and `lsof` for health checks and port inspection

QuantBase uses local files, SQLite, and safe disabled defaults for the community edition. Do not place real credentials in tracked files.

## First Run

```bash
./init.sh
```

The initialization script:

1. checks Python, pip, Node.js, and npm;
2. creates `backend/venv` when missing;
3. installs backend dependencies from `backend/requirements.txt`;
4. installs frontend dependencies;
5. copies `backend/.env.example` to `backend/.env` only when `.env` is missing;
6. initializes SQLite tables;
7. imports the 10 public demo strategy seeds from `data/seed/strategies.json`.

Use `./init.sh --force-seed` only when you intentionally want to overwrite matching demo seed entries.

## Start Services

```bash
./start.sh
```

Default local ports:

| Service | URL |
| --- | --- |
| Frontend | `http://localhost:8888` |
| Backend | `http://localhost:8889` |
| API docs | `http://localhost:8889/docs` |
| Backend health | `http://localhost:8889/api/v1/health` |
| Backend v2 health | `http://localhost:8889/api/v2/system/health` |

Component-only startup is available for local debugging:

```bash
./start.sh --backend-only
./start.sh --frontend-only
```

Logs and PID files are written under `logs/`.

## Inspect And Stop

```bash
./status.sh
./status.sh --json
./stop.sh
./restart.sh
```

`status.sh` checks PID files, ports, health endpoints, log files, database files, disk space, and URLs. `stop.sh` first uses PID files, then process patterns and port fallback. `restart.sh` composes stop and start with the same component flags.

## Non-Service Verification

```bash
./scripts/check.sh
```

The check script runs the frontend build, frontend lint, backend compile checks, and a focused public-boundary test suite. It uses `backend/venv/bin/python` when the virtual environment exists.

Use targeted checks for focused changes:

```bash
pytest -q tests/test_project_disclaimers.py
npm --prefix frontend run build
python3 -m compileall -q backend/app
```

## Configuration

Copy the template only for local use:

```bash
cp backend/.env.example backend/.env
```

Important defaults:

| Variable | Default | Meaning |
| --- | --- | --- |
| `OKX_TESTNET` | `true` | OKX private API examples default to testnet placeholders. |
| `QUANTBASE_AUTH_ENABLED` | `false` | Frontend community build has no login page; enabling this protects v2 APIs and requires custom access handling. |
| `QUANTBASE_LIVE_TRADING_ENABLED` | `false` | Real-account reads and live execution are disabled. |
| `QUANTBASE_MCP_ENABLE_LIVE_TRADING` | `0` | MCP live mutation tools are disabled. |
| `HERMES_AGENT_ENABLED` | `false` | Optional external agent integration is disabled. |

Never commit `.env`, API keys, exchange credentials, webhook URLs, local databases, logs, or screenshots with private account data.

## Expected Local Smoke Test

After `./start.sh`, a healthy local run should satisfy:

```bash
curl -fsS http://127.0.0.1:8889/api/v1/health
curl -fsS http://127.0.0.1:8889/api/v2/system/health
curl -fsS http://127.0.0.1:8888/
./status.sh --json
```

Stop services after verification when you do not need them running:

```bash
./stop.sh
```

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Port 8888 or 8889 is occupied | Run `./status.sh`, then stop the existing QuantBase process or choose a clean shell. |
| Backend exits during startup | Inspect `logs/backend.log`; confirm `backend/venv` exists and dependencies are installed. |
| Frontend exits during startup | Inspect `logs/frontend.log`; confirm `frontend/node_modules` exists. |
| API returns 401 | Confirm `QUANTBASE_AUTH_ENABLED=false` for the community frontend, or provide your own auth flow. |
| Real-account endpoints return disabled errors | This is expected unless `QUANTBASE_LIVE_TRADING_ENABLED=1` is intentionally set. |
