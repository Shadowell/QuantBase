# 行情 Page Design

## Route

- Path: `/market`
- Component: `frontend/src/pages/Market.tsx`
- Sidebar label/icon: `行情` / `TrendingUp`

## Purpose

行情 is the focused real-time market inspection workspace. It lets operators switch between contract and spot instruments, inspect K-lines, order-book context, EMA overlays, and optional AI prediction comparison without rendering the separate full trade-pair universe list.

## First-Screen Layout

- Header left of the K-line workspace contains the detail-view `合约 / 现货` switch and symbol selector.
- Timeframe controls sit on the same header row, aligned to the right.
- Main area prioritizes the K-line chart with EMA legend/value strip; EMA line colors, legend/tooltip markers, and value-strip colors must match, and EMA tooltip/value-strip numbers render with 5 decimal places.
- Right-side panels provide order-book and auxiliary market context.
- Toolbar actions stay compact: AI prediction, refresh, and connection state.
- The page does not render the full `市场列表` panel, market-list search, favorite stars, 24h range rows, or `合约 / 现货 / 币币 / 自选` universe tabs above the chart.

## Data Sources

- `/api/v2/market/klines` from the file K-line store first, then legacy fallback/fetch behavior.
- `/api/v2/market/indicators` for EMA/technical overlays.
- OKX public ticker/order-book data for live quote context.
- AI prediction compare endpoints only when AI prediction is explicitly enabled.

## Interactions

- Changing market type converts the current base between `BASE/USDT:USDT` and `BASE/USDT` when possible.
- Changing symbol, timeframe, or prediction mode must ignore stale in-flight responses.
- Home ranking row clicks may navigate here with the selected symbol already applied.
- AI prediction mode keeps real K-line context and does not fabricate future OHLCV when model output is unavailable.

## Empty/Error States

- Missing real K-lines should surface as a clear chart/data error.
- AI model errors must be explicit and must not fall back to random-walk or template data.

## Screenshot Contract

Community screenshot: not bundled by default.

The screenshot must show a rendered chart with market data, detail controls, EMA context, and order-book/detail panels. It must not show the removed full `市场列表` browser above the chart.
