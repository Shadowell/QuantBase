# 监控 Page Design

## Route

- Path: `/monitor`
- Component: `frontend/src/pages/Monitor.tsx`
- Sidebar label/icon: `监控` / `Eye`

## Purpose

监控 is the portfolio-level operations center. It summarizes paper and live strategy health, PnL, alerts, push-card settings, and strategy-level drill-down entry points.

## First-Screen Layout

- Page header icon matches sidebar.
- Top overview separates paper/simulation and live summaries.
- Top overview KPI cards use refined in-card hierarchy: compact semibold labels, tabular numeric values, and readable 11px caption text with a subtle divider so secondary metric context remains legible on colored card backgrounds.
- Running strategy cards show PnL, return, account total, positions, order/trade counts, and detail actions.
- Profit push settings are vertical card sections with consistent ON/OFF, interval, and send-now controls.

## Data Sources

- Aggregated monitor APIs.
- Strategy runtime status and paper broker state.
- Live account read-only snapshots for live monitoring blocks.
- Feishu/Telegram notifier readiness and push configuration.
- Paper profit-card snapshots are titled `模拟收益卡片`; live profit-card snapshots include only currently executing live subscriptions and exclude paused/stopped subscriptions.

## Interactions

- Alert creation lives in dialogs with scenario templates.
- Profit-card manual send is disabled when no effective webhook exists.
- `详情` routes to the appropriate simulation/live workspace.
- `实盘监控` strategy cards are sorted by `收益率` descending after status and asset-type filters, matching the default operator focus on best-to-worst live performance.

## Empty/Error States

- Empty running strategy sets should show zero/empty state rather than stale last values.
- Push-card failures must expose last error/delivery mode.

## Screenshot Contract

Community screenshot: not bundled by default.

The screenshot must include real monitor metrics or documented real empty state.
