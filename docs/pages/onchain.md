# On-Chain Research Page

## Route

- Route: `/onchain`
- Frontend owner: `frontend/src/pages/OnchainResearch.tsx`
- Backend owner: `backend/app/api/v2/endpoints/onchain.py`
- Scope: read-only on-chain research dashboard

## Purpose

`链上` 是投资研究导向的链上数据看板。首版帮助操作者快速观察 DeFiLlama 覆盖的链 TVL、协议 TVL、协议费用、稳定币供给和稳定币收益池，从宏观资金流、协议基本面和收益机会三个角度筛选后续研究方向。

页面只读，不创建策略、不启动同步任务、不连接真实账户、不把链上指标自动注入交易逻辑。

## First-Screen Layout

- 顶部标题为 `链上数据`，左侧使用 `Network` 图标，右侧提供 `刷新` 按钮。
- 标题下方展示 `DeFiLlama`、中文 summary 状态和 `更新于` 时间。
- 页面使用全宽 `p-6 h-full` 工作台布局，直接进入数据看板，不做营销 hero。
- 顶部 KPI 栅格展示 `总锁仓量`、`稳定币供给`、`24H 协议费用`、`稳定币收益池`、`最大公链` 和 `最大协议`。
- 下方分段控件包含 `综合总览`、`协议研究`、`收益机会`。
- `综合总览` 默认展示链锁仓量和稳定币供给；`协议研究` 展示协议锁仓量与协议费用排行；`收益机会` 展示稳定币收益池与稳定币链分布。
- 底部保留 `DeFiLlama 数据源状态` chips，使用中文数据集名和中文状态，便于判断哪些数据集完整、部分失败或为空。
- 面向操作者的可见文案优先使用中文；`DeFiLlama`、`24H` 等来源名或行业缩写可保留，但必须放在中文语义里。

## Data Sources

- Summary API: `GET /api/v2/onchain/summary`
- Source provider: DeFiLlama public API, no API key required for the first slice.
- Endpoints:
  - `https://api.llama.fi/v2/chains`
  - `https://api.llama.fi/protocols`
  - `https://api.llama.fi/overview/fees`
  - `https://stablecoins.llama.fi/stablecoins?includePrices=true`
  - `https://yields.llama.fi/pools`
- Backend normalizes snake_case summary fields and the frontend receives camelCase through the shared API client.
- The backend keeps a short in-memory TTL cache so normal navigation does not call DeFiLlama for every render.
- 稳定币收益池会过滤到有有效锁仓量且年化收益有上限的数据；这些只是研究线索，不是推荐。

## Interactions

- `刷新` reloads `/api/v2/onchain/summary`.
- Segment buttons switch between overview, protocol research, and yield opportunity tables without triggering mutations.
- Tables use internal scrolling for long lists so the page shell remains stable on wide and narrow screens.
- Guest sessions may view the page because it only uses read-only v2 GET endpoints.

## Empty/Error States

- If every DeFiLlama endpoint fails or returns no rows, API status is `waiting_for_data`, arrays are empty, and `empty_reason` is `等待 DeFiLlama 返回真实链上数据`.
- If only some endpoints fail, API status is `partial`, successful datasets remain visible, and warnings identify the failed source groups.
- 前端在紧凑的 amber 提示面板中展示 warnings，并把已知 DeFiLlama 数据集 key 翻译为中文名称。
- Every table supports empty arrays with a stable empty panel.
- The page must not render fabricated protocol, chain, fee, stablecoin, or yield rows.

## Screenshot Contract

README screenshot is not captured yet. After this page is merged and deployed, screenshots must be captured from the deployed QuantBase page with real DeFiLlama-backed data. Do not intercept API responses, inject DOM rows, add screenshot-only records, or use generated images.
