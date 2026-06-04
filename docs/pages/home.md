# 首页 Page Design

## Route

- Path: `/`
- Component: `frontend/src/pages/Home.tsx`
- Sidebar label/icon: `首页` / `LayoutDashboard`

## Purpose

首页 is the operator landing page for market breadth and system context. It shows market-wide summary/ranking signals only and does not expose the full trade-pair browser.

## First-Screen Layout

- Dark financial shell inherited from `MainLayout`.
- Top page header introduces `市场大盘` and keeps navigation density low.
- Market overview is the primary data section. The first module is a compact `OKX 市场概览 · 大盘情绪指数` grid: one large综合分 card plus market breadth, turnover, strongest/weakest symbols, and five sentiment cards in the same visual block. It must avoid long explanatory paragraphs, duplicated section titles, or a standalone `OKX 市场榜单` description card. The OKX-app-style ranking board remains below it with `热门榜`、`新币榜`、`TradFi`、`涨幅榜`、`跌幅榜`; the ranking board uses a two-column desktop layout: the left side is the compact榜单 selector, and the right side is the selected榜单 detail table with latest price, 24h change, sparkline, 24h volume, 24h turnover, and 24h range. Each ranking tab shows Top 10 rows inside a fixed-height internal scroll body.
- `大盘情绪指数` sits inside the fused overview module and computes a 0-100 summary score from real OKX ticker breadth/turnover plus OKX funding-rate data when available. It breaks the score into five compact operator-facing cards: `市场热度`、`杠杆情绪`、`多空拥挤`、`风险偏好`、`宏观事件`.
- The `市场热度` card surfaces turnover concentration and must not repeat the overview band's `上涨家数` count.
- The sentiment module must not invent unavailable data. Long/short account-ratio and economic-calendar integrations are labeled as pending/read-only reminder inputs until real backend sources are wired.
- The page must not render the full trade-pair browsing table, search, favorite toggles, or the `合约 / 现货 / 币币 / 自选` tabs. The Home ranking detail table is limited to the selected Top 10榜单 rows.
- Summary cards and ranked market panels must use real OKX ticker-backed data. `新币榜` and `TradFi` only show configured symbols that the market API actually returns; they do not fabricate rows when OKX data is missing.

## Data Sources

- OKX public ticker data through QuantBase market APIs.
- Contract ranking turnover uses quote-currency/USDT turnover. For OKX contract tickers, raw `volCcy24h` is base-asset volume, so QuantBase must use the quote turnover field when available or derive `last * volCcy24h`.
- OKX funding-rate data through the existing QuantBase funding API for the leverage sentiment card.
- Strategy and runtime summary APIs for cross-page entry cards.
- The Home summary does not expose favorites; symbol selection continues through ranking rows or the Market page detail selector.

## Interactions

- Ranking detail row clicks navigate to `/market` and select the clicked symbol.
- Home does not expose per-symbol search, sortable market table, or favorite toggles.
- Entry cards navigate to specialist pages instead of opening deep modal workflows on the home page.

## Empty/Error States

- If market data is unavailable, show an explicit loading/error state; do not fill overview panels with synthetic tickers.
- The page should still render navigation and static entry points when a secondary data block fails.

## Screenshot Contract

Community screenshot: not bundled by default.

The screenshot must show real dashboard summary/ranking data with the left榜单 selector and right榜单 detail table. It must not show the full trade-pair browser, search, favorite stars, or the `合约 / 现货 / 币币 / 自选` tabs.
