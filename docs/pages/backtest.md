# 回测 Page Design

## Route

- Path: `/backtest`
- Component: `frontend/src/pages/Backtest.tsx`
- Sidebar label/icon: `回测` / `FlaskConical`

## Purpose

回测 is the asynchronous multi-instance backtest console. It starts real-data Backtrader jobs, merges running jobs with persisted results, and exposes result details, trade records, and K-line buy/sell review.

## First-Screen Layout

- Page shell uses the same full-width, left-aligned workspace container as market, monitor, data, watch, and simulation pages. It must not center the whole console inside a narrow `max-w-* mx-auto` wrapper, so external wide displays keep the backtest controls aligned near the sidebar.
- Top area introduces the backtest console and create action.
- Main list merges active/interrupted local instances and persisted `/backtest/results` rows.
- Rows are compact full-width cards with strategy name, asset/status chips, K-line period chips, KPI summary, logs, and actions. Strategy names get the widest desktop column and wrap instead of truncating, so canonical names remain visible on the instance list. The KPI label row (`收益` / `夏普` / `回撤` / `胜率` / `交易`) stays compact and readable with tighter spacing to leave room for the name.
- Row action buttons use one polished dark financial toolbar style. `详情` is blue, `日志` is neutral unless an error exists, destructive actions stay red, and completed rows use a brighter `成功` state with a check icon and success-specific emerald tone instead of sharing the generic green action style.
- Asset chips must stay accurate while strategy metadata is loading: contract rows can infer `合约` from the saved instance/result name such as `[合约]...`, instead of falling back to `现货`.
- Sort controls default to `创建时间 ↓` so newly created instances stay on top; `收益率`、`回撤`、`胜率`、`创建时间` are available in the same segmented control, with `回撤` defaulting to lower-first and all persisted-history sorts applied by the API before pagination.
- Instance fuzzy search sits inside the `回测实例` list card header, immediately after the title/count, so operators search the list where the records are shown instead of in the upper filter toolbar.

## Data Sources

- `backtest_jobs` for queued/running/cancelled/interrupted jobs.
- `backtest_results` for lightweight persisted result rows and `/backtest/result/{id}` for full persisted reports.
- File K-line store and real exchange fetch/cache fallback for historical bars.
- BTC/USDT 1D real K-lines for benchmark return and strategy excess return.
- Strategy registry/config for symbols, market type, costs, and timeframe.

## Interactions

- Create flow uses a staged modal wizard: fuzzy-search strategy selection, date/cost config, quick ranges, final confirmation.
- The strategy selector is a searchable combobox, not a native dropdown. It filters backtestable paper/research strategies by strategy name, symbol/range, asset type, and K-line timeframe, then shows matching rows with asset badge, canonical name, period, and scope.
- Parameter step includes a `周期模式` control:
  - `策略定义` uses the selected strategy's configured timeframe.
  - `指定周期` runs once on one selected K-line period.
  - `多周期矩阵` runs the same strategy/date/cost inputs across multiple selected periods and shows both period tabs and a comparison table in results.
- Period buttons use compact labels `1M / 5M / 15M / 30M / 1H / 4H / 1D`; the selected period must also drive the K-line review chart and the backend strategy config. Instance cards must show the effective period; matrix backtests show every selected period as separate chips.
- Stop/cancel and resume actions call backend job endpoints.
- Result details show one strategy title in the page header, then overview/performance/trades in the result card. The result card must not repeat the strategy title; its metadata row labels the date span as `回测时间范围` and keeps symbol scope / K-line period as secondary chips. Matrix backtest details expose a `周期详情` tab strip; switching it changes the displayed KPI groups, K-line review, performance statistics, and trade table to that period's persisted result while keeping the comparison table visible. The shared K-line review keeps EMA tooltip markers aligned with EMA line colors, formats EMA values to 5 decimal places, and renders B/S trade markers as short rectangular markers that show only the single `B` or `S` letter, match the candlestick body width, sit outside the candle high/low, and use same-color guide lines pointing to the exact execution-price K-line anchor. Markers outside the loaded K-line window are excluded from both rendering and y-axis range calculation. Benchmark comparison is always BTC/USDT close-to-close over the selected backtest date range, and `基准超额` is strategy cumulative return minus BTC/USDT benchmark return for the active period. Persisted history details should hydrate the same Crypto KPI set as freshly completed jobs; new records store the full result payload, while legacy records derive Calmar, fee drag, payoff, expectancy, and sparse equity-based volatility/Sortino only from stored summary/trade data.
- Overview KPI cards must keep numeric values readable without making them dominate the detail view: compact semi-bold tabular numbers, normal letter spacing, readable labels, smaller secondary captions, and a subtle colored left accent for the metric group. The metric labels should not use micro text that becomes unreadable on external displays.
- The trade table shows historical fill rows newest-first. Contract rows include `杠杆`, `保证金`, and `成交名义` in addition to historical fill price, quantity, realized PnL, fee, and reason. New persisted rows read these values from the Backtrader order log; older rows may show inferred notional and blank leverage/margin where the original fill did not store them.
- Persisted result list loading must stay lightweight and paginated. The console requests the first 20 `/backtest/results` rows, asks for one extra row to decide whether `加载更多回测记录` should be shown, and uses `offset` for subsequent pages. `收益率` / `回撤` / `胜率` / `创建时间` sort controls pass `sort_by` and `sort_dir` into the API so sorting is applied globally before pagination, not just within the loaded page. The instance-list search input is placed in the `回测实例` card header and passes `q` to `/backtest/results` so persisted history is fuzzy-filtered before pagination, while browser-local running/interrupted rows are filtered in memory by the same query across strategy name, symbol, date, status, and period. The first-screen list skips matrix summaries so it does not parse large matrix result blobs; full matrix equity curves, trade arrays, period summaries, and detailed reports are fetched only when opening `/backtest/result/{id}`.

## Empty/Error States

- No records should show a neutral empty list, not a fake placeholder strategy.
- Failed jobs expose logs through the log dialog.
- Missing K-lines fail explicitly with per-symbol detail.
- Reloaded local completed rows without a saved result payload are hidden so operators open the persisted history row instead of an empty report shell.

## Screenshot Contract

Community screenshot: not bundled by default.

The screenshot should show either real persisted records or active job rows with real status data.
