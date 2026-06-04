# 信号中心 Page Design

## Route

- Path: `/signals`
- Component: `frontend/src/pages/SignalCenter.tsx`
- Sidebar entry: hidden by default; keep direct-route access to `/signals`
- Page title/icon: `信号中心` / `Send`

## Purpose

信号中心 is the OKX Signal Bot audit and delivery workspace. It reviews strategy signals, channel bindings, manual confirmation settings, payload preview, and delivery history before external webhook delivery.

## First-Screen Layout

- Left strategy selector defaults to `已启用`.
- Main board displays OKX signal channel binding and selected strategy signal controls.
- Signal rows expose status, payload preview, manual confirmation, and delivery audit.
- Channel rows use binding status lights instead of front checkboxes.

## Data Sources

- `strategy_signals`, `strategy_signal_events`, and signal approval state.
- `signal_channels`, strategy-channel bindings, and delivery records.
- OKX Signal Bot webhook configuration stored server-side/operator-side.

## Interactions

- Operators can bind/unbind channels to a selected strategy.
- Manual confirmation toggles whether new signals enter review or are delivered automatically.
- Payload preview must stay close to sizing fields such as `investmentType` and `amount`.

## Empty/Error States

- Missing webhook/channel configuration disables send actions with explicit reasons.
- Delivery failures must remain visible in audit rows.

## Screenshot Contract

Community screenshot: not bundled by default.

The screenshot must show real channel or strategy-signal state, with secrets masked by the app.
