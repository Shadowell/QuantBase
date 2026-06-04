# 数据中心 Page Design

## Route

- Path: `/data`
- Component: `frontend/src/pages/DataManager.tsx`
- Sidebar label/icon: `数据` / `Database`

## Purpose

数据中心 manages K-line coverage, trading-pair configuration, sync jobs, schedules, and data cleanup for real market data used by market charts, backtests, and strategy warmup.

## First-Screen Layout

- Header exposes global sync actions and schedule state.
- Summary cards show total records, data-pair count, visible trading-pair count, and spot/contract breakdowns. The visible trading-pair count includes configured pairs, pairs already present in `sync_metadata` / table stats, and pairs from the current sync job; the card also shows the smaller future-sync configured count.
- Filter row and the trading-pair list sit inside one bordered maintenance panel, so the whole list area reads as a single bounded module.
- The panel header contains the market-type switch, timeframe/search filters, add-symbol action, and a visible delete-symbol action.
- Trading pairs render in one bounded scroll panel inside that module; sync job detail is below.
- Timeframe coverage pills/cards are not limited to the backend default list. The page derives visible K-line periods from the union of `/sync/config.default_timeframes`, synced `tableStats`, synced `sync_metadata`, and current sync-job progress. The backend default config now includes `30m`, so the page must expose `1M / 5M / 15M / 30M / 1H / 4H / 1D` tokens immediately and continue to merge in any extra discovered periods automatically.

## Data Sources

- File K-line store and `sync_metadata`.
- `sync_jobs` and `sync_job_items`.
- `/sync/config`, `/sync/status`, `/sync/table-stats`, `/sync/jobs`, and schedule APIs.
- `/sync/symbols` add/remove configuration APIs.
- Market symbols API for add-symbol candidates.

## Interactions

- `合约 / 现货` switch defaults to `合约`.
- Add-symbol dialog can add spot and contract entries for the same base. It also provides an enabled-by-default `添加后同步历史数据` checkbox with `近1年 / 近半年 / 近3月 / 近1月` range choices; the default is `近1年`. When enabled, confirming the add starts a background `/sync/start` job for the newly added pairs across all configured Data Manager timeframes.
- The trading-pair list merges configured pairs with pairs that already have K-line metadata or appear in the current sync job, so ad-hoc/background sync tasks become visible as soon as their metadata or progress arrives.
- One-off production sync scripts may first write newly discovered OKX symbols into `data_sync_custom_symbols`; after that, the Data Manager list should show those pairs immediately even before all K-line periods finish syncing. Those scripts report progress through terminal logs and normal `sync_metadata` updates rather than screenshot-only mock rows.
- The panel-level `删除交易对` action opens a searchable selector for configured pairs in the current market type, then uses the shared confirmation dialog before removal.
- Configured trading-pair rows expose a compact remove action in the row header. Metadata-only rows show an informational icon instead of remove, because they are not in the future sync list. Removing a configured pair only updates future sync configuration and related dialog selections; it does not delete stored historical K-line files or metadata. Historical data deletion remains a separate expanded-card action.
- Sync actions open configuration dialogs before submitting background jobs.
- Expanded pair cards submit single-symbol/single-timeframe background sync jobs for `开始同步`, `增量`, and `按日期`; the selected end date includes that full calendar day. After the background acknowledgement returns, the page immediately enters polling mode and expands sync task detail so short or delayed jobs remain visible.
- Sync task detail stays mounted through polling and manual refreshes.
- Sync task progress treats failed terminal items as skipped/processed. A `部分失败` operation still shows separate failure count and error summary, but the processed count and progress bar include both successful items and failed skipped items, so a job that has attempted every pair/timeframe reaches 100% instead of appearing stuck at the success-only count.

## Empty/Error States

- Missing stats must not block the rest of the page shell.
- Failed sync jobs remain in task detail with error summaries.

## Screenshot Contract

Community screenshot: not bundled by default.

The screenshot must show real configured pairs, synced metadata-backed pairs, stats, or sync job state.
