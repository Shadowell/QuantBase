# QuantBase 技术文档

> 版本: 1.0 | 最后更新: 2026-02-07 | 作者: QuantBase Team

---

## 目录

1. [项目概述](#1-项目概述)
2. [系统架构](#2-系统架构)
3. [技术栈](#3-技术栈)
4. [目录结构](#4-目录结构)
5. [数据库设计](#5-数据库设计)
6. [后端 API 参考](#6-后端-api-参考)
7. [前端页面模块](#7-前端页面模块)
8. [后端服务层](#8-后端服务层)
9. [交易所集成层](#9-交易所集成层)
10. [配置说明](#10-配置说明)
11. [WebSocket 协议](#11-websocket-协议)
12. [数据同步机制](#12-数据同步机制)

---

## 1. 项目概述

**QuantBase** 是一个全功能的加密货币量化交易平台，支持行情监控、策略开发、历史回测、模拟盘交易、实盘交易及风控管理。

### 核心能力

| 能力 | 说明 |
|------|------|
| 多交易所支持 | OKX、Binance，通过 CCXT 统一接口 |
| 实时行情 | WebSocket 推送 + REST 轮询双模式 |
| 策略引擎 | 沙箱化 Python 脚本执行，支持自定义策略 |
| 回测系统 | 支持做多/做空、止损止盈、滑点手续费模拟 |
| 数据管理 | 本地 SQLite 存储，按 timeframe 分表，支持增量同步 |
| 风控体系 | 仓位管理、熔断机制、每日止损、最大回撤控制 |
| 通知推送 | Telegram Bot 集成 |

---

## 2. 系统架构

```
┌─────────────────────────────────────────────────────────┐
│                    前端 (React + TypeScript)              │
│  ┌──────┬──────┬──────┬──────┬──────┬──────┬──────┬───┐ │
│  │ 首页 │ 行情 │ 交易 │ 策略 │ 回测 │ 实盘 │ 监控 │数据│ │
│  └──┬───┴──┬───┴──┬───┴──┬───┴──┬───┴──┬───┴──┬───┴─┬─┘ │
│     │      │      │      │      │      │      │     │   │
│     └──────┴──────┴──────┴──────┴──────┴──────┴─────┘   │
│                    ↕ REST API + WebSocket                 │
└─────────────────────────────────────────────────────────┘
                           ↕
┌─────────────────────────────────────────────────────────┐
│                 后端 (FastAPI + Python)                   │
│  ┌─────────────────────────────────────────────────────┐ │
│  │                   API 路由层                         │ │
│  │  market | funding | trading | strategy | backtest   │ │
│  │  monitor | data_sync | live_trading | paper_trading │ │
│  │  auto_trade | health | websocket                    │ │
│  └────────────────────┬────────────────────────────────┘ │
│                       ↕                                   │
│  ┌─────────────────────────────────────────────────────┐ │
│  │                  服务层 (Services)                    │ │
│  │  MarketService | FundingService | TradingService    │ │
│  │  StrategyEngine | DataSyncService | AutoTrader      │ │
│  │  RiskManager | PaperTradingEngine | ProBacktest     │ │
│  │  WebSocketService | TelegramNotifier                │ │
│  └────────────────────┬────────────────────────────────┘ │
│                       ↕                                   │
│  ┌──────────────┐ ┌──────────────────┐                   │
│  │ Exchange 层  │ │  数据层 (SQLite)  │                   │
│  │ OKX|Binance  │ │  local_db.py     │                   │
│  │ Mock         │ │  WAL + 线程安全   │                   │
│  └──────────────┘ └──────────────────┘                   │
└─────────────────────────────────────────────────────────┘
```

---

## 3. 技术栈

### 后端

| 技术 | 版本 | 用途 |
|------|------|------|
| Python | 3.10+ | 运行时环境 |
| FastAPI | 0.100+ | Web 框架 |
| Uvicorn | - | ASGI 服务器 |
| CCXT | 4.x | 交易所统一接口 |
| SQLite | 内置 | 本地持久化（WAL 模式） |
| Pydantic | v2 | 数据校验与序列化 |
| pydantic-settings | - | 环境变量配置管理 |
| python-dotenv | - | .env 文件加载 |
| NumPy | - | 技术指标计算 |
| APScheduler | - | 定时任务调度 |

### 前端

| 技术 | 版本 | 用途 |
|------|------|------|
| React | 18 | UI 框架 |
| TypeScript | 5.x | 类型安全 |
| Vite | 5.x | 构建工具 |
| Zustand | - | 状态管理 |
| ECharts | 5.x | 图表可视化（K线、权益曲线） |
| Tailwind CSS | 3.x | 样式框架 |
| Axios | - | HTTP 客户端 |
| Lucide React | - | 图标库 |
| React Router | v6 | 路由管理 |

---

## 4. 目录结构

```
QuantBase/
├── backend/
│   ├── .env                          # 环境变量配置
│   ├── app/
│   │   ├── main.py                   # FastAPI 应用入口
│   │   ├── core/
│   │   │   └── config.py             # Pydantic Settings 配置类
│   │   ├── api/
│   │   │   ├── api.py                # 路由聚合注册
│   │   │   └── endpoints/            # 各模块 API 端点
│   │   │       ├── market.py         # 行情数据
│   │   │       ├── funding.py        # 资金费率
│   │   │       ├── trading.py        # 交易下单
│   │   │       ├── strategy.py       # 策略管理
│   │   │       ├── backtest.py       # 回测
│   │   │       ├── monitor.py        # 监控告警
│   │   │       ├── data_sync.py      # 数据同步
│   │   │       ├── live_trading.py   # 实盘交易
│   │   │       ├── paper_trading.py  # 模拟盘
│   │   │       ├── auto_trade.py     # 自动化交易
│   │   │       ├── health.py         # 健康检查
│   │   │       └── websocket.py      # WebSocket
│   │   ├── services/                 # 业务逻辑服务层
│   │   ├── exchange/                 # 交易所集成层
│   │   └── db/
│   │       └── local_db.py           # SQLite 数据库封装
│   └── venv/                         # Python 虚拟环境
├── frontend/
│   ├── src/
│   │   ├── App.tsx                   # 路由配置
│   │   ├── pages/                    # 页面组件
│   │   ├── components/               # 公共组件
│   │   ├── stores/                   # Zustand 状态管理
│   │   ├── hooks/                    # 自定义 Hook
│   │   ├── api/
│   │   │   └── client.ts            # API 客户端
│   │   └── types/
│   │       └── index.ts             # TypeScript 类型定义
│   └── vite.config.ts               # Vite 配置（含代理）
└── docs/                             # 文档
```

---

## 5. 数据库设计

QuantBase 使用 **SQLite** 作为本地数据库引擎，启用 **WAL (Write-Ahead Logging)** 模式实现并发读写，并通过 `threading.local` 实现线程安全的连接管理。

数据库文件默认路径:
- macOS: `~/Library/Application Support/QuantBase/crypto_data.db`
- Windows: `~/AppData/Roaming/QuantBase/crypto_data.db`
- Linux: `~/.local/share/QuantBase/crypto_data.db`

### 5.1 表总览

| 序号 | 表名 | 类型 | 说明 |
|------|------|------|------|
| 1 | `kline_history` | 数据表 | K线历史数据（统一表，向后兼容） |
| 2 | `kline_5m` | 数据分表 | 5分钟K线数据 |
| 3 | `kline_15m` | 数据分表 | 15分钟K线数据 |
| 4 | `kline_1h` | 数据分表 | 1小时K线数据 |
| 5 | `kline_4h` | 数据分表 | 4小时K线数据 |
| 6 | `kline_1d` | 数据分表 | 1天K线数据 |
| 7 | `funding_rate_history` | 数据表 | 资金费率历史 |
| 8 | `funding_rate_realtime` | 缓存表 | 资金费率实时数据 |
| 9 | `open_interest_history` | 数据表 | 持仓量历史 |
| 10 | `liquidation_history` | 数据表 | 爆仓历史 |
| 11 | `trades_history` | 数据表 | 链上成交历史 |
| 12 | `strategies` | 业务表 | 策略配置与脚本 |
| 13 | `strategy_trades` | 业务表 | 策略交易记录 |
| 14 | `backtest_results` | 业务表 | 回测结果 |
| 15 | `alerts` | 业务表 | 告警配置 |
| 16 | `exchange_configs` | 配置表 | 交易所密钥配置 |
| 17 | `sync_metadata` | 元数据表 | 数据同步进度跟踪 |
| 18 | `ticker_cache` | 缓存表 | 行情快照缓存 |

---

### 5.2 `kline_history` — K线历史数据（统一表）

> 旧版统一存储表，所有 timeframe 的K线混合存储在一张表中。新版分表上线后保留以向后兼容，写入时双写。

| 字段名 | 数据类型 | 约束 | 说明 |
|--------|----------|------|------|
| `id` | INTEGER | PRIMARY KEY AUTOINCREMENT | 自增主键 |
| `exchange` | TEXT | NOT NULL | 交易所标识，如 `okx`、`binance` |
| `symbol` | TEXT | NOT NULL | 交易对标识，如 `BTC/USDT`、`ETH/USDT` |
| `timeframe` | TEXT | NOT NULL | K线周期，如 `1m`、`5m`、`15m`、`1h`、`4h`、`1d` |
| `timestamp` | INTEGER | NOT NULL | K线开盘时间，Unix 毫秒时间戳 |
| `open` | REAL | NOT NULL | 开盘价（USDT 计价） |
| `high` | REAL | NOT NULL | 最高价（USDT 计价） |
| `low` | REAL | NOT NULL | 最低价（USDT 计价） |
| `close` | REAL | NOT NULL | 收盘价（USDT 计价） |
| `volume` | REAL | NOT NULL | 成交量（以基础货币计，如 BTC） |
| `quote_volume` | REAL | 可空 | 成交额（以计价货币计，如 USDT） |
| `trades_count` | INTEGER | 可空 | 该K线周期内的成交笔数（部分交易所不返回） |

**唯一约束**: `UNIQUE(exchange, symbol, timeframe, timestamp)` — 同一交易所、交易对、周期、时间戳不可重复。

**索引**: `idx_kline_symbol_time ON kline_history(exchange, symbol, timeframe, timestamp)` — 加速按交易对+时间范围查询。

**写入策略**: `INSERT OR IGNORE` — 遇到重复记录静默跳过，支持幂等写入。

---

### 5.3 `kline_5m` / `kline_15m` / `kline_1h` / `kline_4h` / `kline_1d` — K线分表

> 按 timeframe 拆分的独立表，无需存储 `timeframe` 字段，查询性能更优。表结构完全一致，以 `kline_1h` 为例说明。

| 字段名 | 数据类型 | 约束 | 说明 |
|--------|----------|------|------|
| `id` | INTEGER | PRIMARY KEY AUTOINCREMENT | 自增主键 |
| `exchange` | TEXT | NOT NULL | 交易所标识，如 `okx`、`binance` |
| `symbol` | TEXT | NOT NULL | 交易对标识，如 `BTC/USDT` |
| `timestamp` | INTEGER | NOT NULL | K线开盘时间，Unix 毫秒时间戳 |
| `open` | REAL | NOT NULL | 开盘价 |
| `high` | REAL | NOT NULL | 最高价 |
| `low` | REAL | NOT NULL | 最低价 |
| `close` | REAL | NOT NULL | 收盘价 |
| `volume` | REAL | NOT NULL | 成交量（基础货币） |
| `quote_volume` | REAL | 可空 | 成交额（计价货币） |

**唯一约束**: `UNIQUE(exchange, symbol, timestamp)` — 注意：因为已经按 timeframe 分表，所以唯一键不包含 timeframe。

**索引**: `idx_kline_{tf}_sym_ts ON kline_{tf}(exchange, symbol, timestamp)`

**分表与统一表的关系**:
- 写入时**双写**: 同时写入分表和 `kline_history` 统一表
- 读取时**分表优先**: 先查分表，分表为空则自动回退到 `kline_history`
- 分表覆盖的 timeframe: `5m`, `15m`, `1h`, `4h`, `1d`
- 其他周期（如 `1m`, `30m`, `1w`）仍使用 `kline_history`

**数据量估算**（单交易对，1年数据）:

| 分表 | 周期 | 预计行数/交易对/年 | 说明 |
|------|------|-------------------|------|
| `kline_5m` | 5分钟 | ~105,120 | 365 × 24 × 12 |
| `kline_15m` | 15分钟 | ~35,040 | 365 × 24 × 4 |
| `kline_1h` | 1小时 | ~8,760 | 365 × 24 |
| `kline_4h` | 4小时 | ~2,190 | 365 × 6 |
| `kline_1d` | 1天 | ~365 | 365 |

---

### 5.4 `funding_rate_history` — 资金费率历史

> 记录永续合约的历史资金费率，用于资金费率套利分析和回测。

| 字段名 | 数据类型 | 约束 | 说明 |
|--------|----------|------|------|
| `id` | INTEGER | PRIMARY KEY AUTOINCREMENT | 自增主键 |
| `exchange` | TEXT | NOT NULL | 交易所标识 |
| `symbol` | TEXT | NOT NULL | 交易对（永续合约），如 `BTC/USDT` |
| `timestamp` | INTEGER | NOT NULL | 结算时间，Unix 毫秒时间戳 |
| `funding_rate` | REAL | NOT NULL | 资金费率值，如 `0.0001` 表示万分之一 |
| `mark_price` | REAL | 可空 | 结算时的标记价格（用于计算实际费用） |

**唯一约束**: `UNIQUE(exchange, symbol, timestamp)`

**索引**: `idx_funding_symbol_time ON funding_rate_history(exchange, symbol, timestamp)`

**资金费率说明**:
- 正值（如 `0.0003`）: 多头支付空头
- 负值（如 `-0.0002`）: 空头支付多头
- 通常每 8 小时结算一次（OKX / Binance 默认）

---

### 5.5 `funding_rate_realtime` — 资金费率实时数据

> 缓存各交易对的最新资金费率及预测值，定期刷新。

| 字段名 | 数据类型 | 约束 | 说明 |
|--------|----------|------|------|
| `id` | INTEGER | PRIMARY KEY AUTOINCREMENT | 自增主键 |
| `exchange` | TEXT | NOT NULL | 交易所标识 |
| `symbol` | TEXT | NOT NULL | 交易对 |
| `current_rate` | REAL | 可空 | 当期资金费率（最近一次结算的费率） |
| `predicted_rate` | REAL | 可空 | 预测下期资金费率（交易所提供的估算值） |
| `next_funding_time` | INTEGER | 可空 | 下次结算时间，Unix 毫秒时间戳 |
| `mark_price` | REAL | 可空 | 当前标记价格（用于保证金和强平计算的公允价格） |
| `index_price` | REAL | 可空 | 指数价格（多个现货交易所的加权平均价格） |
| `updated_at` | TIMESTAMP | DEFAULT CURRENT_TIMESTAMP | 记录最后更新时间 |

**唯一约束**: `UNIQUE(exchange, symbol)` — 每个交易所每个交易对只保留一行最新数据。

---

### 5.6 `open_interest_history` — 持仓量历史

> 记录合约市场的未平仓合约数量变化，反映市场参与度。

| 字段名 | 数据类型 | 约束 | 说明 |
|--------|----------|------|------|
| `id` | INTEGER | PRIMARY KEY AUTOINCREMENT | 自增主键 |
| `exchange` | TEXT | NOT NULL | 交易所标识 |
| `symbol` | TEXT | NOT NULL | 交易对 |
| `timestamp` | INTEGER | NOT NULL | 数据时间，Unix 毫秒时间戳 |
| `open_interest` | REAL | NOT NULL | 未平仓合约数量（以基础货币计，如 BTC） |
| `open_interest_value` | REAL | 可空 | 未平仓合约价值（以 USDT 计） |

**唯一约束**: `UNIQUE(exchange, symbol, timestamp)`

---

### 5.7 `liquidation_history` — 爆仓历史

> 记录市场上的强制平仓（爆仓）事件，用于情绪分析和风控。

| 字段名 | 数据类型 | 约束 | 说明 |
|--------|----------|------|------|
| `id` | INTEGER | PRIMARY KEY AUTOINCREMENT | 自增主键 |
| `exchange` | TEXT | NOT NULL | 交易所标识 |
| `symbol` | TEXT | NOT NULL | 交易对 |
| `timestamp` | INTEGER | NOT NULL | 爆仓时间，Unix 毫秒时间戳 |
| `side` | TEXT | NOT NULL | 被爆仓方向: `long`（多头被爆）/ `short`（空头被爆） |
| `price` | REAL | NOT NULL | 爆仓触发价格 |
| `quantity` | REAL | NOT NULL | 爆仓数量（基础货币） |
| `value` | REAL | NOT NULL | 爆仓金额（USDT） |

**索引**: `idx_liq_time ON liquidation_history(timestamp)` — 按时间排序查询。

**注意**: 此表无唯一约束，因为同一时间可能发生多笔爆仓。

---

### 5.8 `trades_history` — 成交历史

> 记录交易所公开的逐笔成交数据（Public Trades），用于微观结构分析。

| 字段名 | 数据类型 | 约束 | 说明 |
|--------|----------|------|------|
| `id` | INTEGER | PRIMARY KEY AUTOINCREMENT | 自增主键 |
| `exchange` | TEXT | NOT NULL | 交易所标识 |
| `symbol` | TEXT | NOT NULL | 交易对 |
| `trade_id` | TEXT | NOT NULL | 交易所返回的成交ID（唯一标识一笔成交） |
| `timestamp` | INTEGER | NOT NULL | 成交时间，Unix 毫秒时间戳 |
| `side` | TEXT | NOT NULL | 主动方向: `buy`（主买）/ `sell`（主卖） |
| `price` | REAL | NOT NULL | 成交价格 |
| `quantity` | REAL | NOT NULL | 成交数量（基础货币） |
| `quote_quantity` | REAL | 可空 | 成交额（计价货币） |
| `is_maker` | INTEGER | 可空 | 是否为挂单方成交: `1`=是（Maker）, `0`=否（Taker） |

**唯一约束**: `UNIQUE(exchange, symbol, trade_id)`

---

### 5.9 `strategies` — 策略配置表

> 存储用户创建的量化策略，包含策略代码（Python 脚本）和运行配置。

| 字段名 | 数据类型 | 约束 | 说明 |
|--------|----------|------|------|
| `id` | INTEGER | PRIMARY KEY AUTOINCREMENT | 策略唯一ID |
| `name` | TEXT | NOT NULL UNIQUE | 策略名称（唯一，如"双均线策略"） |
| `description` | TEXT | 可空 | 策略描述文字 |
| `script_content` | TEXT | NOT NULL | 策略 Python 脚本源代码 |
| `config` | TEXT | 可空 | 策略参数配置，JSON 字符串。示例: `{"fast_period": 5, "slow_period": 20}` |
| `status` | TEXT | DEFAULT 'stopped' | 策略运行状态: `stopped` / `running` / `error` |
| `exchange` | TEXT | 可空 | 绑定的交易所标识 |
| `symbols` | TEXT | 可空 | 绑定的交易对列表，JSON 数组字符串。示例: `["BTC/USDT","ETH/USDT"]` |
| `created_at` | TIMESTAMP | DEFAULT CURRENT_TIMESTAMP | 策略创建时间 |
| `updated_at` | TIMESTAMP | DEFAULT CURRENT_TIMESTAMP | 策略最后更新时间 |

**策略脚本规范**:
- 必须定义 `on_tick(ctx)` 函数，策略引擎会在每个周期调用
- `ctx` 对象提供 `klines`、`position`、`buy()`、`sell()` 等接口
- 脚本在沙箱环境中执行，部分危险模块被禁止导入

---

### 5.10 `strategy_trades` — 策略交易记录

> 记录策略引擎执行过程中产生的每一笔交易（买入/卖出）。

| 字段名 | 数据类型 | 约束 | 说明 |
|--------|----------|------|------|
| `id` | INTEGER | PRIMARY KEY AUTOINCREMENT | 自增主键 |
| `strategy_id` | INTEGER | NOT NULL, FOREIGN KEY → strategies(id) | 所属策略ID |
| `exchange` | TEXT | NOT NULL | 执行交易的交易所 |
| `symbol` | TEXT | NOT NULL | 交易对 |
| `order_id` | TEXT | 可空 | 交易所返回的订单ID（模拟盘时可能为空） |
| `timestamp` | INTEGER | NOT NULL | 成交时间，Unix 毫秒时间戳 |
| `side` | TEXT | NOT NULL | 交易方向: `buy` / `sell` |
| `type` | TEXT | NOT NULL | 订单类型: `market`（市价）/ `limit`（限价） |
| `price` | REAL | NOT NULL | 成交价格 |
| `quantity` | REAL | NOT NULL | 成交数量 |
| `fee` | REAL | 可空 | 手续费金额 |
| `fee_asset` | TEXT | 可空 | 手续费币种（如 `USDT`、`BNB`） |
| `pnl` | REAL | 可空 | 该笔交易的盈亏（平仓时计算） |

**索引**: `idx_strategy_trades_id ON strategy_trades(strategy_id, timestamp)` — 按策略+时间查询。

**外键**: `strategy_id` → `strategies(id)`, 删除策略时级联删除关联交易记录。

---

### 5.11 `backtest_results` — 回测结果表

> 存储策略回测运行的绩效指标和详细交易记录。

| 字段名 | 数据类型 | 约束 | 说明 |
|--------|----------|------|------|
| `id` | INTEGER | PRIMARY KEY AUTOINCREMENT | 回测结果ID |
| `strategy_id` | INTEGER | NOT NULL, FOREIGN KEY → strategies(id) | 关联策略ID |
| `start_date` | TEXT | NOT NULL | 回测起始日期，格式 `YYYY-MM-DD` |
| `end_date` | TEXT | NOT NULL | 回测结束日期，格式 `YYYY-MM-DD` |
| `initial_capital` | REAL | NOT NULL | 初始资金（USDT） |
| `final_capital` | REAL | 可空 | 回测结束时的总资产 |
| `total_return` | REAL | 可空 | 总收益率，如 `0.35` 表示 35% |
| `annual_return` | REAL | 可空 | 年化收益率 |
| `max_drawdown` | REAL | 可空 | 最大回撤，如 `0.12` 表示 12% |
| `sharpe_ratio` | REAL | 可空 | 夏普比率（风险调整后收益指标） |
| `win_rate` | REAL | 可空 | 胜率，如 `0.65` 表示 65% |
| `profit_factor` | REAL | 可空 | 盈亏比（总盈利 / 总亏损） |
| `total_trades` | INTEGER | 可空 | 总交易笔数 |
| `trades_detail` | TEXT | 可空 | 详细交易记录，JSON 字符串 |
| `status` | TEXT | DEFAULT 'running' | 回测状态: `running` / `completed` / `failed` |
| `created_at` | TIMESTAMP | DEFAULT CURRENT_TIMESTAMP | 回测执行时间 |

**外键**: `strategy_id` → `strategies(id)`

**绩效指标说明**:
- `sharpe_ratio`: > 1 为良好, > 2 为优秀, > 3 为卓越
- `max_drawdown`: 越小越好，通常期望 < 0.2 (20%)
- `profit_factor`: > 1 表示整体盈利, > 2 为优秀
- `win_rate`: 配合盈亏比使用，高胜率 + 高盈亏比 = 优秀策略

---

### 5.12 `alerts` — 告警配置表

> 用户创建的价格/指标告警规则。

| 字段名 | 数据类型 | 约束 | 说明 |
|--------|----------|------|------|
| `id` | INTEGER | PRIMARY KEY AUTOINCREMENT | 告警ID |
| `name` | TEXT | NOT NULL | 告警名称，如"BTC 突破 10万" |
| `type` | TEXT | NOT NULL | 告警类型: `price_above` / `price_below` / `funding_rate` / `volume_spike` / `custom` |
| `symbol` | TEXT | 可空 | 监控的交易对（如 `BTC/USDT`，部分全局告警可为空） |
| `condition` | TEXT | NOT NULL | 触发条件，JSON 字符串。示例: `{"threshold": 100000, "exchange": "okx"}` |
| `notification` | TEXT | 可空 | 通知配置，JSON 字符串。示例: `{"telegram_chat_id": "xxx", "webhook_url": "xxx"}` |
| `enabled` | INTEGER | DEFAULT 1 | 是否启用: `1`=启用, `0`=禁用 |
| `last_triggered_at` | TIMESTAMP | 可空 | 上次触发时间（用于防止频繁告警） |
| `created_at` | TIMESTAMP | DEFAULT CURRENT_TIMESTAMP | 创建时间 |

---

### 5.13 `exchange_configs` — 交易所配置表

> 存储各交易所的 API 密钥配置（敏感数据，生产环境建议加密存储）。

| 字段名 | 数据类型 | 约束 | 说明 |
|--------|----------|------|------|
| `id` | INTEGER | PRIMARY KEY AUTOINCREMENT | 自增主键 |
| `exchange` | TEXT | NOT NULL UNIQUE | 交易所标识，如 `okx`、`binance` |
| `api_key` | TEXT | 可空 | API Key（公钥） |
| `api_secret` | TEXT | 可空 | API Secret（私钥） |
| `passphrase` | TEXT | 可空 | API 口令（OKX 特有，Binance 无此字段） |
| `testnet` | INTEGER | DEFAULT 0 | 是否使用测试网: `1`=测试网, `0`=主网 |
| `enabled` | INTEGER | DEFAULT 1 | 是否启用: `1`=启用, `0`=禁用 |
| `created_at` | TIMESTAMP | DEFAULT CURRENT_TIMESTAMP | 创建时间 |

**安全提醒**: 此表存储敏感信息，在实际部署中应考虑:
1. 数据库文件权限设为 600
2. `api_secret` 和 `passphrase` 建议 AES 加密后存储
3. 优先使用 `.env` 环境变量方式配置密钥

---

### 5.14 `sync_metadata` — 数据同步元数据

> 跟踪每个「交易所 × 交易对 × 周期」的同步进度，支持断点续传和增量同步。

| 字段名 | 数据类型 | 约束 | 说明 |
|--------|----------|------|------|
| `id` | INTEGER | PRIMARY KEY AUTOINCREMENT | 自增主键 |
| `exchange` | TEXT | NOT NULL | 交易所标识 |
| `symbol` | TEXT | NOT NULL | 交易对 |
| `timeframe` | TEXT | NOT NULL | K线周期 |
| `data_type` | TEXT | NOT NULL DEFAULT 'kline' | 数据类型: `kline`（K线）/ `funding`（资金费率） |
| `first_timestamp` | INTEGER | 可空 | 本地最早数据时间戳（毫秒） |
| `last_timestamp` | INTEGER | 可空 | 本地最新数据时间戳（毫秒），增量同步从此处继续 |
| `total_records` | INTEGER | DEFAULT 0 | 本地已存储的总记录数 |
| `status` | TEXT | DEFAULT 'idle' | 同步状态: `idle`（空闲）/ `syncing`（同步中）/ `completed`（已完成）/ `error`（失败） |
| `last_sync_at` | TIMESTAMP | 可空 | 最后一次成功同步的时间 |
| `error_message` | TEXT | 可空 | 最近一次同步失败的错误信息 |
| `created_at` | TIMESTAMP | DEFAULT CURRENT_TIMESTAMP | 记录创建时间 |
| `updated_at` | TIMESTAMP | DEFAULT CURRENT_TIMESTAMP | 记录最后更新时间 |

**唯一约束**: `UNIQUE(exchange, symbol, timeframe, data_type)`

**索引**: `idx_sync_meta ON sync_metadata(exchange, symbol, timeframe, data_type)`

**增量同步原理**:
1. 首次同步时，`last_timestamp` 为空，从当前时间回溯 `history_days` 天开始拉取
2. 后续同步时，从 `last_timestamp + interval_ms` 开始拉取新数据
3. 每拉取 10 批数据更新一次 `last_timestamp`，实现断点续传
4. 如果同步中断，下次启动时自动从上次进度继续

---

### 5.15 `ticker_cache` — 行情快照缓存

> 缓存各交易对的最新行情快照，用于减少 API 调用频率。

| 字段名 | 数据类型 | 约束 | 说明 |
|--------|----------|------|------|
| `id` | INTEGER | PRIMARY KEY AUTOINCREMENT | 自增主键 |
| `exchange` | TEXT | NOT NULL | 交易所标识 |
| `symbol` | TEXT | NOT NULL | 交易对 |
| `last` | REAL | 可空 | 最新成交价 |
| `bid` | REAL | 可空 | 最优买价（买一价） |
| `ask` | REAL | 可空 | 最优卖价（卖一价） |
| `high` | REAL | 可空 | 24小时最高价 |
| `low` | REAL | 可空 | 24小时最低价 |
| `volume` | REAL | 可空 | 24小时成交量（基础货币） |
| `quote_volume` | REAL | 可空 | 24小时成交额（计价货币） |
| `change` | REAL | 可空 | 24小时价格变动（绝对值，USDT） |
| `change_percent` | REAL | 可空 | 24小时涨跌幅（百分比），如 `2.5` 表示涨 2.5% |
| `timestamp` | INTEGER | 可空 | 行情时间，Unix 毫秒时间戳 |
| `updated_at` | TIMESTAMP | DEFAULT CURRENT_TIMESTAMP | 缓存更新时间 |

**唯一约束**: `UNIQUE(exchange, symbol)` — 每个交易对只保留最新一条快照。

---

### 5.16 ER 图（实体关系）

```
strategies (1) ──────< (N) strategy_trades
     │
     │
     └──── (1) ──────< (N) backtest_results

sync_metadata ──── 跟踪 ──── kline_history / kline_5m ... kline_1d

exchange_configs ──── 对应 ──── 各交易所实例
```

---

## 6. 后端 API 参考

所有 API 前缀: `/api/v1`

### 6.1 健康检查 (`/health`)

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/health/` | 系统健康检查，返回 `{"status": "ok"}` |
| GET | `/health/exchanges` | 检查各交易所连接状态 |

### 6.2 行情数据 (`/market`)

| 方法 | 路径 | 参数 | 说明 |
|------|------|------|------|
| GET | `/market/ticker` | `exchange`, `symbol` | 获取单个交易对实时行情 |
| GET | `/market/tickers` | `exchange`, `symbols`(逗号分隔) | 获取多个交易对行情 |
| GET | `/market/klines` | `exchange`, `symbol`, `timeframe`, `limit` | 获取K线数据 |
| GET | `/market/orderbook` | `exchange`, `symbol`, `limit` | 获取订单簿深度 |
| GET | `/market/trades` | `exchange`, `symbol`, `limit` | 获取最近成交记录 |
| GET | `/market/symbols` | `exchange`, `quote`(默认USDT) | 获取交易对列表 |

### 6.3 资金费率 (`/funding`)

| 方法 | 路径 | 参数 | 说明 |
|------|------|------|------|
| GET | `/funding/rates` | `exchange`, `symbols` | 获取资金费率列表 |
| GET | `/funding/rate/{symbol}` | `exchange` | 获取单个交易对资金费率详情 |
| GET | `/funding/history` | `exchange`, `symbol`, `limit` | 获取资金费率历史 |
| GET | `/funding/opportunities` | `exchange`, `min_rate`, `limit` | 获取套利机会 |
| GET | `/funding/summary` | - | 获取多交易所资金费率汇总 |

### 6.4 交易 (`/trading`)

| 方法 | 路径 | 参数 | 说明 |
|------|------|------|------|
| GET | `/trading/balance` | `exchange` | 获取合并账户余额 |
| GET | `/trading/balance/detail` | `exchange` | 获取分账户余额（交易/资金） |
| POST | `/trading/transfer` | Body: `TransferRequest` | 资金划转 |
| GET | `/trading/positions` | `exchange`, `symbol` | 获取持仓 |
| POST | `/trading/spot/order` | Body: `SpotOrderRequest` | 现货下单 |
| POST | `/trading/futures/order` | Body: `FuturesOrderRequest` | 合约下单 |
| POST | `/trading/futures/close-all` | `exchange`, `symbol` | 一键平仓 |
| GET | `/trading/orders/open` | `exchange`, `symbol` | 获取未成交订单 |
| GET | `/trading/orders/history` | `exchange`, `symbol`, `limit` | 获取历史订单 |
| GET | `/trading/order/{order_id}` | `exchange`, `symbol` | 获取订单详情 |
| DELETE | `/trading/order/{order_id}` | `exchange`, `symbol` | 撤单 |
| DELETE | `/trading/orders/all` | `exchange`, `symbol` | 撤销全部订单 |
| GET | `/trading/trades` | `exchange`, `symbol`, `limit` | 获取成交记录 |
| POST | `/trading/check-risk` | Body: 风险检查参数 | 下单前风险检查 |

### 6.5 策略管理 (`/strategy`)

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/strategy/list` | 获取策略列表 |
| GET | `/strategy/{id}` | 获取策略详情 |
| POST | `/strategy/create` | 创建策略 |
| PUT | `/strategy/{id}` | 更新策略 |
| DELETE | `/strategy/{id}` | 删除策略（级联删除交易记录和回测结果） |
| POST | `/strategy/{id}/start` | 启动策略 |
| POST | `/strategy/{id}/stop` | 停止策略 |
| GET | `/strategy/{id}/trades` | 获取策略交易记录 |
| GET | `/strategy/{id}/status` | 获取策略运行状态 |

### 6.6 回测 (`/backtest`)

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/backtest/run_sync` | 同步运行回测 |
| POST | `/backtest/run` | 异步运行回测 |
| GET | `/backtest/strategies` | 获取可用回测策略列表 |
| GET | `/backtest/status/{strategy_id}` | 获取回测状态 |
| GET | `/backtest/results` | 获取回测结果列表 |
| GET | `/backtest/result/{backtest_id}` | 获取回测结果详情 |

### 6.7 监控告警 (`/monitor`)

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/monitor/alerts` | 获取告警列表 |
| POST | `/monitor/alert` | 创建告警 |
| PUT | `/monitor/alert/{id}` | 启用/禁用告警 |
| DELETE | `/monitor/alert/{id}` | 删除告警 |
| GET | `/monitor/running-strategies` | 获取运行中策略列表 |
| GET | `/monitor/liquidations` | 获取爆仓数据 |
| GET | `/monitor/long-short-ratio` | 获取多空比 |
| GET | `/monitor/open-interest` | 获取持仓量 |

### 6.8 数据同步 (`/data_sync`)

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/data_sync/start` | 启动批量同步（后台运行） |
| POST | `/data_sync/sync_one` | 同步单个交易对（同步等待） |
| GET | `/data_sync/status` | 获取同步状态 |
| GET | `/data_sync/data` | 获取已同步数据清单 |
| POST | `/data_sync/daily_update` | 触发每日增量更新 |
| GET | `/data_sync/config` | 获取默认同步配置 |
| GET | `/data_sync/table_stats` | 获取分表统计信息 |
| POST | `/data_sync/delete` | 删除指定数据 |

### 6.9 实盘交易 (`/live`)

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/live/configure` | 配置实盘系统 |
| POST | `/live/start` | 启动实盘 |
| POST | `/live/stop` | 停止实盘 |
| POST | `/live/pause` | 暂停实盘 |
| POST | `/live/resume` | 恢复实盘 |
| GET | `/live/dashboard` | 实时监控仪表盘 |
| GET | `/live/events` | 获取交易事件 |
| GET | `/live/equity_curve` | 获取权益曲线 |
| GET | `/live/strategy_info` | 获取当前策略信息 |
| POST | `/live/test_telegram` | 测试 Telegram 推送 |
| GET | `/live/telegram_history` | 获取 Telegram 消息历史 |
| POST | `/live/pre_flight` | 实盘前飞行检查 |
| GET | `/live/strategies` | 获取可用实盘策略列表 |

### 6.10 模拟盘 (`/paper_trading`)

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/paper_trading/run` | 运行模拟盘 |
| GET | `/paper_trading/status` | 获取模拟盘状态 |
| GET | `/paper_trading/signals` | 获取信号记录 |
| POST | `/paper_trading/stress_test` | 压力测试 |
| GET | `/paper_trading/strategies` | 获取可用策略列表 |
| GET | `/paper_trading/risk_config` | 获取默认风控配置 |
| GET | `/paper_trading/live_signals` | 获取实盘信号 |
| GET | `/paper_trading/signal_stats` | 获取信号统计 |
| POST | `/paper_trading/pre_flight` | 飞行检查 |

### 6.11 自动化交易 (`/auto-trade`)

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/auto-trade/configure` | 配置自动交易系统 |
| POST | `/auto-trade/start` | 启动自动交易 |
| POST | `/auto-trade/stop` | 停止自动交易 |
| POST | `/auto-trade/pause` | 暂停 |
| POST | `/auto-trade/resume` | 恢复 |
| GET | `/auto-trade/status` | 获取自动交易完整状态 |
| GET | `/auto-trade/events` | 获取交易事件列表 |
| GET | `/auto-trade/equity-curve` | 权益曲线数据 |
| GET | `/auto-trade/strategies` | 列出所有 Pro 策略 |
| GET | `/auto-trade/analyze` | 实时技术分析 |
| POST | `/auto-trade/risk/reset-circuit-breaker` | 解除风控熔断 |
| POST | `/auto-trade/backtest/run` | 运行 Pro 策略回测 |
| POST | `/auto-trade/backtest/run-all` | 批量回测 |
| GET | `/auto-trade/backtest/results` | 获取回测结果 |
| GET | `/auto-trade/backtest/compare` | 策略对比 |

---

## 7. 前端页面模块

| 路由 | 页面组件 | 说明 |
|------|----------|------|
| `/` | `Home.tsx` | 首页 — 市场总览，显示 Top50 交易对的实时行情、涨跌幅、成交量、迷你K线图 |
| `/market` | `Market.tsx` | 行情 — K线图表（含MA均线）、订单簿深度、实时价格 |
| `/trading` | `Trading.tsx` | 交易 — 现货/合约下单、账户余额、持仓管理、订单管理 |
| `/strategy` | `Strategy.tsx` | 策略 — 策略编辑器、策略模板广场、启动/停止控制 |
| `/backtest` | `Backtest.tsx` | 回测 — 策略回测配置、绩效指标展示、权益曲线图、交易记录 |
| `/live` | `LiveTrading.tsx` | 模拟/实盘 — 策略选择向导、参数配置、飞行检查、运行监控 |
| `/monitor` | `Monitor.tsx` | 监控 — 运行中策略、告警管理、市场情绪（多空比、持仓量） |
| `/data` | `DataManager.tsx` | 数据 — 数据同步管理、分表统计、增量/全量同步、数据删除 |

---

## 8. 后端服务层

| 服务 | 文件 | 核心职责 |
|------|------|----------|
| `MarketService` | `market_service.py` | 行情数据获取与缓存，统一封装交易所 API |
| `FundingService` | `funding_service.py` | 资金费率获取、套利机会识别、多所汇总 |
| `TradingService` | `trading_service.py` | 交易执行（现货/合约）、订单管理、风险检查 |
| `StrategyService` | `strategy_service.py` | 策略 CRUD、生命周期管理 |
| `StrategyEngine` | `strategy_engine.py` | 策略执行引擎、沙箱化代码运行、服务重启时自动恢复 |
| `DataSyncService` | `data_sync_service.py` | K线数据同步、增量拉取、断点续传 |
| `AutoTrader` | `auto_trader.py` | 自动化交易编排、策略循环、PnL 跟踪 |
| `RiskManager` | `risk_manager.py` | 仓位管理、止损止盈、熔断机制、最大回撤控制 |
| `PaperTradingEngine` | `paper_trading.py` | 模拟盘引擎，真实行情 + 模拟执行 |
| `ProBacktestEngine` | `pro_backtest.py` | Pro 策略回测引擎 |
| `BacktestEngine` / `backtrader_engine` | `backtrader_engine.py` | 唯一回测执行路径：Backtrader + `BaseStrategy`（含 `run_sync`、优化器、Agent）；已移除 `strategy_backtest.py` |
| `WebSocketService` | `websocket_service.py` | 实时数据推送、订阅管理 |
| `TelegramNotifier` | `telegram_notifier.py` | Telegram 通知推送 |
| `SignalAnalyzer` | `signal_analyzer.py` | 多指标综合信号分析 |
| `SchedulerService` | `scheduler_service.py` | APScheduler 定时任务调度 |
| 技术指标库 | `indicators.py` | SMA/EMA/MACD/RSI/KDJ/BBANDS/ATR 等，纯 NumPy 实现 |

---

## 9. 交易所集成层

### 9.1 架构

```
BaseExchange (抽象基类)
├── OKXExchange        # OKX 交易所
├── BinanceExchange    # Binance 交易所
└── MockExchange       # 模拟交易所（开发测试用）

ExchangeManager (统一管理器)
└── 根据 .env 配置初始化，支持 Mock 模式切换
```

### 9.2 `BaseExchange` 公共方法

| 方法 | 参数 | 返回 | 说明 |
|------|------|------|------|
| `fetch_ticker(symbol)` | 交易对 | Dict | 获取实时行情 |
| `fetch_tickers(symbols)` | 交易对列表 | List[Dict] | 批量获取行情 |
| `fetch_ohlcv(symbol, timeframe, limit, since)` | - | List[Dict] | 获取K线数据 |
| `fetch_order_book(symbol, limit)` | - | Dict | 获取订单簿 |
| `fetch_trades(symbol, limit)` | - | List[Dict] | 获取最近成交 |
| `fetch_balance()` | - | Dict | 获取账户余额 |
| `create_order(symbol, type, side, amount, price)` | - | Dict | 创建订单 |
| `cancel_order(order_id, symbol)` | - | Dict | 撤销订单 |
| `fetch_open_orders(symbol)` | - | List[Dict] | 获取未成交订单 |
| `fetch_positions(symbol)` | - | List[Dict] | 获取持仓 |

### 9.3 代理配置

通过 `.env` 文件中的 `HTTP_PROXY` / `HTTPS_PROXY` 配置代理，`BaseExchange` 内部延迟读取环境变量，确保 `dotenv` 已加载。

---

## 10. 配置说明

### 10.1 环境变量 (`.env`)

| 变量名 | 必填 | 默认值 | 说明 |
|--------|------|--------|------|
| `OKX_API_KEY` | 是 | - | OKX API Key |
| `OKX_API_SECRET` | 是 | - | OKX API Secret |
| `OKX_PASSPHRASE` | 是 | - | OKX API 口令 |
| `OKX_TESTNET` | 否 | `true` | 是否使用测试网 |
| `HTTP_PROXY` | 否 | - | HTTP 代理地址，如 `http://127.0.0.1:7890` |
| `HTTPS_PROXY` | 否 | - | HTTPS 代理地址 |
| `DB_PATH` | 否 | 系统默认 | SQLite 数据库文件路径 |
| `LOG_LEVEL` | 否 | `INFO` | 日志级别: `DEBUG`/`INFO`/`WARNING`/`ERROR` |
| `QWEN_API_KEY` | 否 | - | 通义千问 API Key（AI 功能） |
| `REDIS_URL` | 否 | - | Redis 连接地址（可选缓存层） |

### 10.2 SQLite PRAGMA 优化

| PRAGMA | 值 | 说明 |
|--------|-----|------|
| `journal_mode` | WAL | Write-Ahead Logging，允许并发读写 |
| `synchronous` | NORMAL | 在 WAL 模式下兼顾性能与安全 |
| `cache_size` | -65536 | 64MB 查询缓存 |
| `foreign_keys` | ON | 启用外键约束 |
| `busy_timeout` | 5000 | 锁等待超时 5 秒 |

### 10.3 数据同步默认配置

| 配置项 | 值 | 说明 |
|--------|-----|------|
| 默认交易对 | BTC/USDT, ETH/USDT, SOL/USDT, BNB/USDT, XRP/USDT, DOGE/USDT, ADA/USDT, AVAX/USDT, LINK/USDT, DOT/USDT, ZAMA/USDT | 11 个交易对 |
| 默认周期 | 5m, 15m, 1h, 4h, 1d | 5 个时间粒度 |
| 回溯天数 | 365 天 | 首次同步拉取最近 1 年数据 |
| 单次请求上限 | 1000 根K线 | CCXT `fetch_ohlcv` 的 limit 参数 |
| 请求间隔 | 0.3 秒 | 避免触发交易所 API 限流 |

---

## 11. WebSocket 协议

### 11.1 连接地址

```
ws://{host}/api/v1/ws
```

前端通过 Vite 代理访问 (`vite.config.ts` 中配置 `ws: true`)。

### 11.2 消息格式

**订阅**:
```json
{
  "action": "subscribe",
  "channel": "ticker",
  "exchange": "okx",
  "symbol": "BTC/USDT"
}
```

**取消订阅**:
```json
{
  "action": "unsubscribe",
  "channel": "ticker",
  "exchange": "okx",
  "symbol": "BTC/USDT"
}
```

**心跳**:
```json
{"action": "ping"}
```

### 11.3 支持的频道

| channel | 说明 | 推送频率 |
|---------|------|----------|
| `ticker` | 实时行情 | ~10s |
| `kline` | K线数据更新 | ~5min (取决于周期) |
| `orderbook` | 订单簿深度 | ~5s |
| `trades` | 最新成交 | 实时 |
| `funding` | 资金费率 | ~60s |

---

## 12. 数据同步机制

### 12.1 同步流程

```
1. 确定起始时间
   ├── 有 start_date 参数 → 使用参数
   ├── sync_metadata 中有 last_timestamp → 从 last_timestamp + interval 开始
   └── 首次同步 → 从 now - history_days 开始

2. 分批拉取数据
   ├── 每批最多 1000 根K线
   ├── 每批间隔 0.3 秒（防限流）
   ├── 每 10 批更新一次 sync_metadata（断点续传）
   └── 直到 current_ms >= end_ms

3. 双写入库
   ├── 写入分表 (kline_5m / kline_1h 等)
   └── 写入统一表 (kline_history)

4. 更新元数据
   ├── first_timestamp / last_timestamp
   ├── total_records
   ├── status = 'completed'
   └── last_sync_at
```

### 12.2 读取策略

```
get_klines(exchange, symbol, timeframe, ...)
  │
  ├── timeframe ∈ {5m, 15m, 1h, 4h, 1d}
  │     ├── 优先查分表 kline_{tf}
  │     └── 分表为空 → 回退查 kline_history
  │
  └── 其他周期 (1m, 30m, 1w ...)
        └── 直接查 kline_history
```

---

> **文档结束** | 如有疑问请联系开发团队
