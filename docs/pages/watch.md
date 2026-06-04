# 盯盘 Page Design

## Route

- Path: `/watch`
- Component: `frontend/src/pages/WatchMarket.tsx`
- Sidebar label/icon: `盯盘` / `ScanLine`

## Purpose

盯盘 tracks symbols that matter to active/live operations. It focuses on K-line inspection, B/S markers, open live-related context, and fast mobile-friendly market review.

## First-Screen Layout

- Header shows watch-market identity and compact controls.
- The first row keeps `合约持仓` on the left and `盯盘列表` on the right with matching 420px outer heights and independent internal scroll areas.
- `订单明细` sits below the first row as a full-width panel and defaults to the latest 100 live order rows so the table is readable without squeezing into the left column; its header includes a fuzzy search input for symbol, strategy name, direction, status, order ids, and visible numeric fields, shows compact counts for the filtered visible rows, strategy orders, external orders, and failed/rejected rows, and the strategy-source column wraps the full strategy name instead of truncating it.
- The `合约持仓` header includes compact position count, total margin, and total unrealized PnL chips; the `盯盘列表` header wraps the timeframe selector and watched-symbol grid and includes watched-symbol count, total related order count, and active timeframe chips.
- Main chart uses `WatchKlineChart` with EMA, volume, MACD, and B/S markers; each marker is a short rectangle that shows only the single `B` or `S` letter, with no Chinese action text and no vertical English action token inside the marker. Live watch marker payloads still normalize separate execution `action=open/close` and `side=long/short` into shared action tokens (`open_long`, `close_long`, `open_short`, `close_short`) for same-candle de-duplication and tooltip detail. The marker width matches the candlestick body width, its height stays compact, its label coordinate is included in the main price-axis range, and it is positioned outside the candle high/low so it does not cover the K-line. Markers outside the loaded K-line window are not snapped to the first/last visible candle, and their execution prices must not contribute to the visible chart's y-axis range, so old hidden fills cannot flatten current candles. Repeated same-action fills in the same candle are collapsed into one visible marker with the merged count available in the tooltip. A same-color guide line extends from the marker to the execution-price anchor, and the marker keeps a visible border/shadow so the B/S letter stays readable on dense candles. Header legends use small colored swatches rather than extra horizontal `B/S` chips so only real trade markers carry B/S labels. EMA/MACD line legends render as clean line-only icons without circular point markers, and EMA tooltip markers match the plotted line colors with EMA values fixed to 5 decimal places.
- Position/order context appears beside or below the chart depending on viewport.
- Timeframe controls use compact labels consistent with Market.

## Data Sources

- OKX public market K-lines and ticker data.
- Live signal executions and currently relevant strategy symbols.
- Optional private account/position context only when available through the guarded live flow.

## Interactions

- Symbol/timeframe changes reload real market data.
- B/S markers come from actual strategy/live execution records, not inferred chart points.
- Mobile layout must remain scannable with the same data hierarchy.

## Empty/Error States

- If there are no watch targets, show the real empty state and route operators back to live/simulation context.
- Chart errors must not be hidden behind a blank canvas.

## Screenshot Contract

Community screenshot: not bundled by default.

The screenshot must show either a real watched symbol chart or the real no-watch-target state documented in `CAPTURE.md`.
