# 实盘 Page Design

## Route

- Path: `/live-real`
- Component: `frontend/src/pages/liveTrading/index.tsx` with `modeScope="live"`
- Sidebar label/icon: `实盘` / `Rocket`

## Purpose

实盘 is the guarded OKX live execution workspace. It binds live accounts, selects reviewed source paper strategies, runs preflight checks, and creates account-level live subscriptions only after explicit operator confirmation.

## First-Screen Layout

- Top account management area separates account selection/addition from asset summaries.
- Middle strategy list shows source paper strategies eligible for live subscription.
- Right live panel shows bound subscriptions, current positions, orders, and audit context.
- Risk style uses neutral surfaces with red only for explicit risk borders/text/badges.

## Data Sources

- `live_accounts`, account binding APIs, and OKX private read endpoints.
- `live_strategy_settings` and `live_strategy_subscriptions`.
- Source paper strategy runtime status APIs.
- `live_signal_executions` for order audit and error history.

## Interactions

- Add account and bind account require explicit operator input.
- Strategies already added to `实盘策略列表` do not expose a delete/remove action in the middle list. The row remains as the durable audit/workbench record; operators can unbind accounts or pause/resume/stop live subscriptions, but cannot delete the list entry from the page.
- Preflight validates account permissions, mode, instrument, sizing, and order-precheck before deployment.
- The preflight-result step opens the latest result automatically after a preflight response, can be pinned by clicking `预检结果`, and can be closed explicitly. Explicit close must keep the popover hidden even while hover/focus remains inside the result group or the pointer moves elsewhere; only clicking `预检结果` again or receiving a new preflight/deploy result reopens it.
- Pause/stop affect only live subscriptions, not the source paper strategy.
- Contract position actions (`平仓` / `市价全平`) must reuse the live broker's OKX direction logic: when OKX returns `posSide=net`, the page and close request resolve direction from explicit `side` first, then fall back to signed size only if the exchange omitted side.
- Right-panel pause/resume/stop controls follow the visible `live_strategy_subscriptions` row for the selected account; a subscription remains controllable even if its middle-list workspace binding was previously removed.
- Stopping a live subscription checks the selected account's current contract positions first. If related non-zero positions exist, the confirmation dialog lists the symbols; confirming closes the matched positions first and then stops the subscription, while canceling leaves both positions and subscription unchanged.

## Empty/Error States

- Missing live account permissions must block deployment with actionable errors.
- Empty live positions/orders should show a read-only empty state, not simulated rows.

## Screenshot Contract

Community screenshot: not bundled by default.

The screenshot must show real account/workspace state. If sensitive details are masked by the app, keep the app-rendered mask.
