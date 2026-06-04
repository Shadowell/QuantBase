# AI研发 Page Design

## Route

- Path: `/ai-lab`
- Component: `frontend/src/pages/AILab.tsx`
- Sidebar label/icon: `AI研发` / `Sparkles`

## Purpose

AI研发 collects AI-assisted strategy workspaces: automatic trading-agent research, AI strategy research, autonomous paper trading, truthful OKX Orbit posting, and existing-strategy optimization. It is a research surface plus a controlled operator automation surface, not a live-trading bypass.

## First-Screen Layout

- Default tab opens the automatic trading-agent workspace.
- Tabs expose research, optimization, autonomous trading, and Orbit posting workflows without changing the page shell.
- Workbench cards use the same dark financial style and compact action buttons as other pages.
- Model selection is drawn from server-side global LLM model settings.
- The autonomous trading tab defaults to a Hermes / Codex provider card, `gpt-5.5`, `多空双向`, system Top30 OKX USDT perpetual symbols, and a 100U paper-only risk envelope with a 10x max-leverage cap; DashScope / Qwen remains available as an alternate provider.
- The `星球发帖` tab uses a compact control-room layout: top metrics for eligible positions, trigger threshold, scan interval, per-run cap, and Orbit login status; a left config panel for single-account automation; a right candidate/history panel for auditable posting.

## Data Sources

- Agent research run APIs and persisted agent tasks.
- Optional Hermes bridge state when configured server-side.
- Hermes/Codex autonomous decisions come from the server-local Hermes bridge and must return JSON-only decisions; the UI never stores Codex credentials or API keys.
- Strategy optimizer config/results.
- OKX Orbit auto-post config, eligible live contract-position candidates, login status, and post history from `/api/v2/agent/orbit-auto-post/*`.
- Global LLM model settings from backend settings APIs.
- Real market snapshots for auto-collected research; no synthetic opportunities.
- Real OKX private live positions for Orbit candidates; no mock/fabricated PnL, ROI, chart, or engagement data.

## Interactions

- Automatic research runs persist and can resume after backend restart.
- Automatic research runs and scheduled runs default to `preferred_direction=auto`, so long and short candidates can both enter the Hermes deep-research flow; an explicit long/short preference may still filter the candidate set.
- New-strategy research and optimizer workflows choose an AI model from server-supported candidates.
- AI自主交易 start/edit forms expose provider, model and direction. `多空双向` allows both `open_long` and `open_short` inside the paper risk envelope; optional `只做空` mode is still enforced in the backend strategy runtime, so an `open_long` suggestion becomes a logged risk rejection.
- Accepted candidates return to normal strategy/backtest/simulation flows.
- Live execution still requires `/live-real` preflight and confirmation.
- Orbit posting can be enabled/disabled, thresholded by margin ROI, manually run once, and manually publish a selected eligible candidate.
- Orbit copy can use the selected Qwen/DashScope model, but must preserve true position facts and include risk wording.
- Orbit publishing depends on the server-side browser publisher command and persistent OKX login profile; login/config failures are shown in status/history and do not count as successful cooldown events.

## Empty/Error States

- Missing LLM/model/Hermes configuration must show explicit blocked states.
- No-market-snapshot or model failures must not produce fake trade intents.
- Missing OKX Orbit login or publisher configuration must show explicit `login_required` / failed states and must not mark a post as published.
- No eligible live contract position should show an empty candidate state, not generated demo content.

## Screenshot Contract

README screenshots:

- Community screenshot: not bundled by default.
- Optimizer screenshot: not bundled by default.

Screenshots must show real server state, saved config, or explicit unavailable states.
