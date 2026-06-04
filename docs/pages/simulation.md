# 模拟盘 Page Design

## Route

- Path: `/live`
- Component: `frontend/src/pages/liveTrading/index.tsx` with `modeScope="paper"`
- Sidebar label/icon: `模拟` / `Activity`

## Purpose

模拟盘 is the paper strategy instance console. It manages running/stopped simulation instances, paper account metrics, positions, trades, events, logs, equity samples, contract paper liquidation events, and manual paper-only close actions.

## First-Screen Layout

- Header uses the simulation workspace title and create action.
- Filter row includes asset class, unlabeled strategy-type pills (`全部` / `CTA` / `马丁` / `AI` / `做市`), K-line timeframe, capital version, leverage, sorting, and fuzzy search.
- Cards show canonical strategy names, a green breathing light for running status, compact K-line timeframe, capital-version, and leverage pills such as `15M` / `100U` / `5X`, symbol scope, profit amount plus return rate, win rate, profit factor, and trade count. The card grid uses four columns on desktop-width and wide workspaces, with mobile fallbacks below that; search-filtered single results keep normal card width instead of stretching across the row. Primary and secondary metric numerals must not be truncated with ellipses, so KPI numbers remain fully visible at the fixed four-column density.
- Running paper strategy cards expose compact bottom-right trading controls: `暂停交易` / `继续交易` toggles the strategy engine state, while `关闭交易` stops the paper strategy without clearing persisted metrics.
- `暂停交易` / `关闭交易` / `详情` render as one centered, adaptive full-width action group: narrow cards may stack the controls, while normal card widths distribute them as three equal columns with matching height, icon treatment, and width rhythm. `详情` stays last so card headers remain focused on identity and status.
- Detail view contains KPI cards, separate collapsible B/S K-line review and account curve cards, positions, trades, events, diagnostics, and strategy logic summaries.
- The create wizard's strategy-selection step has a local fuzzy search over creatable stopped strategies, matching strategy name, symbol scope, K-line period, type, capital version, description, and config metadata.
- The create wizard's run-parameters step defaults paper `模拟初始资金 (USDT)` to `100`; when the selected strategy defines `initial_capital`, the field syncs to that strategy value. The `通用启动风控` sliders are generic startup defaults/manual controls, not strategy-parsed parameters; strategy-derived parameters remain in the left-side strategy-definition panel.

## Data Sources

- Strategy instance/status APIs.
- Batch running-strategy status for card metrics.
- `/live/dashboard` for detail view.
- `strategy_trades`, `strategy_events`, and `strategy_equity_samples` persisted broker/runtime state.
- Real market K-lines for B/S review.
- Contract paper liquidation trades/events persisted by `ContractPaperBroker`; Feishu liquidation alerts use the unified saved Feishu webhook when configured.

## Interactions

- Strategy-type filter resets K-line, capital, and leverage filters to `全部`; K-line filter resets capital and leverage to `全部`; capital filter resets leverage to `全部` so counts and visible rows stay consistent. `AI` uses the instance's AI-autonomous marker or canonical `[AI]` strategy-type tag; `做市` uses canonical `[做市]` names, `config.strategy_type=market_making`, or market-making strategy keys; `CTA` and `马丁` continue to use strategy naming/config semantics.
- Sorting defaults to `收益率 ↓`; only the active sort field appears selected.
- The dashboard loads strategy and paper-instance lists on first paint, then refreshes those lists with a slower non-overlapping background pass; card PnL, return rate, win rate, profit factor, and trade count continue to refresh through the dedicated metric path so `/strategies` is not polled every few seconds.
- Card-level trading controls use the shared confirmation dialog before pausing, resuming, or stopping a paper strategy.
- Guest sessions can view the simulation dashboard and details but run in read-only mode: create, pause/resume, stop, delete, clear, and paper position close controls are hidden or disabled. The backend still rejects paper mutations for guests.
- Detail B/S K-line review and account curve cards are expanded by default; clicking anywhere on either card header collapses or expands that card. The K-line review header shows the selected review symbol and the strategy-defined period as compact tokens, with the period rendered as an uppercase segmented-control token (`1H`, `15M`) using the same visual language as the account-curve range selector. The shared K-line review keeps EMA tooltip markers aligned with EMA line colors and formats EMA values to 5 decimal places.
- The B/S K-line review fetch is keyed by selected symbol, exchange, strategy timeframe, and the stable trade-marker timestamp set. Routine detail polling that returns the same trades must update markers without forcing the chart back into a loading state. B/S markers are plotted at the persisted execution price from `strategy_trades`; the marker is a short rectangle that shows only the single `B` or `S` letter, matches the candlestick body width, sits outside the candle high/low so it does not cover the K-line, and draws a same-color guide line to the exact execution-price K-line anchor, with a high-contrast border/shadow so the letter remains readable on dense candles. Markers outside the loaded K-line window are not rendered and must not contribute execution prices to the chart y-axis range. The action token stays available in the tooltip instead of being rendered inside the marker. The account curve defaults to the `1D` range. The detail route preloads the shared `WatchKlineChart` chunk, and bounded review-window K-line requests should return a sufficiently populated cached window without waiting for an OKX refresh.
- Detail `成交明细` refresh accepts persisted trade responses as either a bare array or wrapped `data` / `trades` / `items` / `results` payloads. When the dashboard still reports persisted trades, a transient empty, malformed, or failed trade refresh must preserve the current rows instead of showing `成交明细 (0)`.
- Current-position rows expose a compact front-of-symbol market button. Hover/focus opens a temporary `15m` preview, and clicking pins a closeable mini candlestick K-line panel so the operator can inspect the position symbol without leaving the detail page. The preview fetch uses an explicit in-flight guard instead of relying on React state-updater side effects, so the panel cannot stay on a skeleton screen without sending the K-line request.
- Manual position close is paper-only and must never call real account endpoints.
- Contract paper positions can be automatically liquidated when isolated position equity falls below maintenance margin. The detail view must show the resulting liquidation trade/event, while the runtime pauses the paper strategy and emits a red Feishu alert if `paper_liquidation_alert` delivery is available.
- Detail KPI `账户总额` displays current paper equity as `$`-prefixed money with two decimals; it is `initial capital + realized/unrealized paper PnL` rather than the unmodified initial capital.

## Empty/Error States

- Empty filters show `未找到匹配的模拟实例。`
- Paused/stopped paper instances keep persisted metrics instead of showing zeroed account state.

## Screenshot Contract

Community screenshot: not bundled by default.

The screenshot must show real paper instance cards or a real empty state documented in `CAPTURE.md`.
