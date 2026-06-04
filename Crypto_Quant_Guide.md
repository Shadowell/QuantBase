# QuantBase - 加密货币量化交易完整指南

> 从股票量化到 Crypto 量化：一站式学习与开发手册
> 版本：1.0
> 更新日期：2026-01-24

---

## 目录

1. [项目概述](#1-项目概述)
2. [核心差异：股市 vs B圈](#2-核心差异股市-vs-b圈)
3. [B圈核心术语详解](#3-b圈核心术语详解)
4. [交易所选择与账户准备](#4-交易所选择与账户准备)
5. [工具链与数据源](#5-工具链与数据源)
6. [策略开发路径](#6-策略开发路径)
7. [QuantBase 系统架构设计](#7-quantbase-系统架构设计)
8. [数据库设计](#8-数据库设计)
9. [API 接口设计](#9-api-接口设计)
10. [开发计划与里程碑](#10-开发计划与里程碑)
11. [避坑指南与风险控制](#11-避坑指南与风险控制)
12. [附录](#附录)

---

## 1. 项目概述

### 1.1 项目背景

QuantBase 是一个面向加密货币市场的量化交易工具平台，参考 StockPro（A股量化工具）的设计理念，提供：

- **实时行情监控**：主流交易对的价格、深度、资金费率
- **策略开发与回测**：支持 Python 策略编写、历史回测
- **多交易所支持**：Binance、OKX、Bybit 统一接口
- **智能盯盘**：异动监控、资金费率套利机会、巨鲸追踪
- **AI 辅助分析**：利用大模型分析市场情绪、链上数据

### 1.2 目标用户

- 具有股票量化经验，想进入 Crypto 领域的开发者
- 希望建立自己量化系统的加密货币交易者
- 对区块链和金融科技感兴趣的技术人员

### 1.3 与 StockPro 的对比

| 维度 | StockPro (股票) | QuantBase (Crypto) |
|------|-----------------|-----------------|
| 数据源 | AkShare (东财/同花顺) | CCXT (Binance/OKX/Bybit) |
| 交易时间 | 9:30-15:00 周一至周五 | 7×24 小时全年无休 |
| 数据库 | SQLite (本地缓存) | SQLite + Redis (实时缓存) |
| 核心指标 | 涨停板、连板、封板率 | 资金费率、持仓量、爆仓数据 |
| 策略类型 | 打板、低吸、板块轮动 | 期现套利、网格、趋势跟踪 |

---

## 2. 核心差异：股市 vs B圈

### 2.1 交易机制对比

| 特性 | 股票市场 (A股/美股) | Crypto 市场 (B圈) | 策略影响 |
|------|---------------------|-------------------|----------|
| **交易时间** | 每天4-6小时，周末休市 | **7×24小时，全年无休** | 策略需全天候运行，服务器运维要求高 |
| **涨跌幅限制** | 10%/20% (A股有限制) | **无限制** | 极端行情可瞬间±50%，风控要求极高 |
| **交易机制** | T+1 (A股) / T+0 (美股) | **T+0** | 高频交易可行，日内反复交易 |
| **最小交易单位** | 100股 (1手) | **0.0001** (可拆分) | 资金门槛极低，10U 也能跑策略 |
| **做空机制** | 融券 (门槛高、成本高) | **合约做空 (便捷)** | 双向交易机会多 |
| **手续费** | 万1-万3 + 印花税 | **Maker 0.02% / Taker 0.05%** | 高频成本可控，Maker 有返佣 |
| **结算周期** | T+1 资金到账 | **实时结算** | 资金利用率极高 |

### 2.2 衍生品对比

| 特性 | 股票期货 (IF/IC) | Crypto 永续合约 |
|------|------------------|-----------------|
| **到期日** | 有交割日 (当月/次月/季) | **无到期日** |
| **锚定机制** | 基差收敛 | **资金费率** |
| **杠杆倍数** | 10-20倍 | **1-125倍** |
| **保证金** | 10%-15% | **0.8%-100%** |
| **强平机制** | 追加保证金 | **自动减仓 (ADL)** |

### 2.3 数据获取对比

```
股票数据流:
交易所 → Wind/万得 → 券商 → 你的系统
(延迟大、成本高、限制多)

Crypto 数据流:
交易所 → API → 你的系统
(直连、实时、免费)
```

---

## 3. B圈核心术语详解

### 3.1 交易品种

#### 现货 (Spot)
- **定义**：币币交易，如 BTC/USDT
- **特点**：持有实际的币，可提现到钱包
- **适用策略**：长线持有、现货网格、跨所搬砖

#### 合约 (Futures)

| 类型 | 英文 | 特点 | 适用场景 |
|------|------|------|----------|
| **交割合约** | Delivery | 有到期日，到期自动结算 | 对冲、套期保值 |
| **永续合约** | Perpetual | 无到期日，资金费率锚定 | **主流交易品种** |
| **U本位** | USDT-M | 用 USDT 作保证金，盈亏以 USDT 计 | 新手推荐 |
| **币本位** | Coin-M | 用 BTC/ETH 作保证金 | 矿工、囤币党 |

### 3.2 资金费率 (Funding Rate) - 核心机制

#### 什么是资金费率？
永续合约没有到期日，通过资金费率让合约价格锚定现货价格。

```
每8小时结算一次（UTC 0:00, 8:00, 16:00）

如果 资金费率 > 0（正费率）:
  → 多头付钱给空头
  → 说明做多的人多，市场偏多

如果 资金费率 < 0（负费率）:
  → 空头付钱给多头
  → 说明做空的人多，市场偏空
```

#### 费率计算示例
```python
# 假设你持有 1 BTC 的多头仓位
# BTC 价格 = 50000 USDT
# 资金费率 = 0.01% (正费率)

仓位价值 = 1 * 50000 = 50000 USDT
费率支出 = 50000 * 0.01% = 5 USDT

# 如果你是多头，需支付 5 USDT 给空头
# 如果你是空头，会收到 5 USDT
```

#### 费率套利策略 (期现套利)
```python
# 经典套利：买现货 + 空永续
# 赚取资金费率，几乎无风险

# 示例：
buy_spot(symbol="BTC", amount=1)        # 买入 1 BTC 现货
open_short(symbol="BTCUSDT", amount=1)  # 做空 1 BTC 永续

# 年化收益 = 费率 * 3次/天 * 365天
# 假设平均费率 0.01%
# 年化 = 0.01% * 3 * 365 = 10.95%
```

### 3.3 订单类型

| 订单类型 | 说明 | Maker/Taker |
|----------|------|-------------|
| **限价单 (Limit)** | 指定价格挂单，等待成交 | Maker (手续费低) |
| **市价单 (Market)** | 立即以当前价成交 | Taker (手续费高) |
| **止损单 (Stop Loss)** | 价格触发后自动下单 | 取决于触发后订单类型 |
| **止盈单 (Take Profit)** | 达到目标价自动平仓 | 取决于触发后订单类型 |
| **跟踪止损 (Trailing Stop)** | 随价格移动的动态止损 | Taker |
| **冰山单 (Iceberg)** | 大单拆分隐藏 | Maker |
| **时间加权 (TWAP)** | 按时间均匀下单 | 混合 |

### 3.4 保证金与杠杆

#### 全仓 vs 逐仓

| 模式 | 英文 | 特点 | 风险 |
|------|------|------|------|
| **全仓** | Cross | 所有仓位共享保证金 | 单个仓位亏损可能连累其他仓位 |
| **逐仓** | Isolated | 每个仓位独立保证金 | 单仓爆仓不影响其他，但资金利用率低 |

#### 杠杆计算

```python
# 开仓保证金 = 仓位价值 / 杠杆倍数
# 示例：50000 USDT 仓位，20倍杠杆

initial_margin = 50000 / 20 = 2500 USDT

# 强平价格计算（简化版，实际更复杂）
# 逐仓模式下，亏损 = 保证金时强平
# 20倍杠杆，约 5% 波动即强平
```

### 3.5 关键指标

| 指标 | 英文 | 说明 | 监控意义 |
|------|------|------|----------|
| **持仓量** | Open Interest | 未平仓合约总量 | 市场参与度、趋势强度 |
| **多空比** | Long/Short Ratio | 多头/空头账户比例 | 市场情绪、反向指标 |
| **爆仓数据** | Liquidation | 被强平的仓位 | 市场波动程度 |
| **资金流向** | Fund Flow | 大资金进出 | 主力动向 |
| **恐惧贪婪指数** | Fear & Greed Index | 市场情绪综合指标 | 逆向操作参考 |

### 3.6 稳定币

| 稳定币 | 发行方 | 锚定 | 特点 |
|--------|--------|------|------|
| **USDT** | Tether | 美元 | 流动性最好，但透明度争议 |
| **USDC** | Circle | 美元 | 合规性好，机构首选 |
| **BUSD** | Binance | 美元 | 币安生态内使用 |
| **DAI** | MakerDAO | 美元 | 去中心化，链上抵押 |

---

## 4. 交易所选择与账户准备

### 4.1 主流交易所对比

| 交易所 | 优势 | 劣势 | 适合人群 | API 特点 |
|--------|------|------|----------|----------|
| **Binance 币安** | 流动性最好、深度最大、交易对最多 | 部分地区受限 | 所有人首选 | 文档完善、限频清晰 |
| **OKX 欧易** | Web3 钱包好、统一账户资金利用率高 | 深度略逊币安 | 华人用户、DeFi 玩家 | v5 API 设计现代 |
| **Bybit** | 衍生品强、撮合引擎快 | 现货交易对少 | 合约交易者 | 延迟低 |
| **Bitget** | 跟单功能强 | 流动性一般 | 跟单用户 | - |
| **Gate.io** | 小币种多 | 深度一般 | 山寨币玩家 | - |

### 4.2 账户注册与安全设置

#### 注册流程
1. 准备：海外邮箱、护照/身份证
2. 注册：使用邀请链接获得手续费折扣
3. KYC：完成身份验证（大部分功能需要）
4. 安全设置：
   - 开启 Google 2FA
   - 设置防钓鱼码
   - 绑定提现白名单

#### API Key 安全配置

```bash
# API Key 权限设置原则
✅ 读取权限 (Read)        - 获取行情、账户信息
✅ 交易权限 (Trade)       - 下单、撤单
❌ 提现权限 (Withdraw)    - 绝对不要开启！

# 必须设置 IP 白名单
# 只允许你的服务器 IP 访问
```

### 4.3 测试环境 (Testnet)

| 交易所 | 测试网地址 | 说明 |
|--------|------------|------|
| Binance | testnet.binancefuture.com | 模拟资金，API 相同 |
| OKX | www.okx.com (Demo Trading) | 需在 APP 中切换 |
| Bybit | testnet.bybit.com | 完整测试环境 |

**强烈建议**：所有策略先在测试网跑通，再用小资金实盘验证！

---

## 5. 工具链与数据源

### 5.1 核心库：CCXT

CCXT 是 Crypto 量化的瑞士军刀，封装了 100+ 交易所的 API。

#### 安装
```bash
pip install ccxt
```

#### 基础用法
```python
import ccxt

# 创建交易所实例（公开接口，无需 API Key）
exchange = ccxt.binance()

# 获取 BTC/USDT 行情
ticker = exchange.fetch_ticker('BTC/USDT')
print(f"价格: {ticker['last']}")
print(f"24h成交量: {ticker['baseVolume']}")
print(f"24h涨跌幅: {ticker['percentage']}%")

# 获取 K 线数据
ohlcv = exchange.fetch_ohlcv('BTC/USDT', timeframe='1h', limit=100)
# 返回: [[timestamp, open, high, low, close, volume], ...]

# 获取深度数据
orderbook = exchange.fetch_order_book('BTC/USDT', limit=20)
print(f"买一: {orderbook['bids'][0]}")  # [price, amount]
print(f"卖一: {orderbook['asks'][0]}")
```

#### 私有接口（需要 API Key）
```python
import ccxt

exchange = ccxt.binance({
    'apiKey': 'YOUR_API_KEY',
    'secret': 'YOUR_SECRET',
    'options': {
        'defaultType': 'future'  # 永续合约
    }
})

# 获取账户余额
balance = exchange.fetch_balance()
print(f"USDT 余额: {balance['USDT']['free']}")

# 下单
order = exchange.create_order(
    symbol='BTC/USDT',
    type='limit',
    side='buy',
    amount=0.001,
    price=40000
)

# 获取持仓
positions = exchange.fetch_positions(['BTC/USDT'])
```

### 5.2 数据源汇总

| 数据类型 | 免费数据源 | 付费数据源 | 说明 |
|----------|------------|------------|------|
| **实时行情** | CCXT + 交易所 API | - | 足够用 |
| **历史 K 线** | CCXT / Binance Public Data | Tardis.dev | 币安历史数据免费下载 |
| **Tick 数据** | 交易所 WebSocket | Tardis.dev, Kaiko | 高频策略需要 |
| **深度快照** | 交易所 WebSocket | Tardis.dev | 做市策略需要 |
| **链上数据** | Dune Analytics, Glassnode Free | Glassnode Pro, Nansen | 巨鲸追踪、DeFi 分析 |
| **情绪数据** | Fear & Greed Index | Santiment | 市场情绪 |
| **资金费率** | 交易所 API | Coinglass | 套利机会 |

### 5.3 Binance Public Data

币安官方提供的历史数据下载中心，包含：
- 所有交易对的 K 线数据（1m 到 1M）
- 逐笔成交数据（Trades）
- 聚合交易数据（AggTrades）
- 深度快照

```bash
# 下载示例
# https://data.binance.vision/
# 目录结构: /data/spot/daily/klines/BTCUSDT/1h/

wget https://data.binance.vision/data/spot/daily/klines/BTCUSDT/1h/BTCUSDT-1h-2025-01-01.zip
```

### 5.4 实时数据 WebSocket

```python
import ccxt.pro as ccxtpro
import asyncio

async def watch_ticker():
    exchange = ccxtpro.binance()
    while True:
        ticker = await exchange.watch_ticker('BTC/USDT')
        print(f"{ticker['datetime']} | Price: {ticker['last']}")

asyncio.run(watch_ticker())
```

### 5.5 量化框架对比

| 框架 | 语言 | 特点 | 适用场景 |
|------|------|------|----------|
| **CCXT** | Python/JS/PHP | 交易所 API 封装 | 所有场景 |
| **VeighNa (vn.py)** | Python | 国产老牌、事件驱动 | CTA、套利 |
| **Hummingbot** | Python | 做市机器人 | 高频做市 |
| **Freqtrade** | Python | 开源、社区活跃 | 入门回测 |
| **Backtrader** | Python | 通用回测框架 | 策略回测 |
| **FMZ (发明者)** | Python/JS | 网页版、托管运行 | 快速验证 |
| **QuantConnect** | Python/C# | 云端回测 | 专业回测 |

---

## 6. 策略开发路径

### 6.1 第一阶段：无风险/低风险套利 (入门推荐)

#### 6.1.1 资金费率套利 (Funding Rate Arbitrage)

**原理**：
- 买入 1 BTC 现货 + 做空 1 BTC 永续合约
- Delta 中性，价格涨跌对你没影响
- 赚取资金费率（正费率时）

**代码框架**：
```python
class FundingArbitrage:
    """资金费率套利策略"""

    def __init__(self, exchange, symbol='BTC/USDT'):
        self.exchange = exchange
        self.symbol = symbol
        self.position_size = 0

    def get_funding_rate(self):
        """获取当前资金费率"""
        funding = self.exchange.fetch_funding_rate(self.symbol)
        return funding['fundingRate']

    def open_arbitrage(self, size):
        """开仓套利"""
        # 1. 买入现货
        self.exchange.create_market_buy_order(
            self.symbol, size, params={'type': 'spot'}
        )

        # 2. 做空永续
        self.exchange.create_market_sell_order(
            self.symbol, size, params={'type': 'future'}
        )

        self.position_size = size

    def close_arbitrage(self):
        """平仓"""
        if self.position_size > 0:
            # 卖出现货
            self.exchange.create_market_sell_order(
                self.symbol, self.position_size, params={'type': 'spot'}
            )
            # 平空永续
            self.exchange.create_market_buy_order(
                self.symbol, self.position_size, params={'type': 'future'}
            )
            self.position_size = 0

    def run(self):
        """运行策略"""
        funding_rate = self.get_funding_rate()

        # 费率 > 0.01% 时开仓
        if funding_rate > 0.0001 and self.position_size == 0:
            self.open_arbitrage(size=0.01)

        # 费率转负时平仓
        elif funding_rate < 0 and self.position_size > 0:
            self.close_arbitrage()
```

**收益预期**：
- 年化 10%-30%（牛市费率高时更多）
- 几乎无风险（需注意交易所风险）

#### 6.1.2 跨交易所搬砖 (Cross-Exchange Arbitrage)

**原理**：
- A 交易所 BTC = 50000 USDT
- B 交易所 BTC = 50050 USDT
- 在 A 买入，在 B 卖出，赚取 50 USDT 价差

**现状**：
- 大币种价差很小（0.01%-0.05%）
- 需要极快的执行速度
- 需要两边都有资金

### 6.2 第二阶段：CTA 趋势策略 (中等风险)

#### 6.2.1 双均线策略

```python
import pandas as pd

class DualMA:
    """双均线策略"""

    def __init__(self, fast_period=5, slow_period=20):
        self.fast_period = fast_period
        self.slow_period = slow_period

    def generate_signal(self, df: pd.DataFrame) -> str:
        """
        生成交易信号
        df 需要包含 'close' 列
        """
        df['ma_fast'] = df['close'].rolling(self.fast_period).mean()
        df['ma_slow'] = df['close'].rolling(self.slow_period).mean()

        # 金叉做多
        if df['ma_fast'].iloc[-1] > df['ma_slow'].iloc[-1] and \
           df['ma_fast'].iloc[-2] <= df['ma_slow'].iloc[-2]:
            return 'BUY'

        # 死叉做空
        if df['ma_fast'].iloc[-1] < df['ma_slow'].iloc[-1] and \
           df['ma_fast'].iloc[-2] >= df['ma_slow'].iloc[-2]:
            return 'SELL'

        return 'HOLD'
```

#### 6.2.2 网格交易 (Grid Trading)

**原理**：在价格区间内设置多个买卖点，跌买涨卖

```python
class GridTrading:
    """网格交易策略"""

    def __init__(self, price_low, price_high, grid_num=10, total_amount=1000):
        self.price_low = price_low
        self.price_high = price_high
        self.grid_num = grid_num
        self.total_amount = total_amount

        # 计算网格
        self.grid_prices = self._calculate_grids()
        self.order_amount = total_amount / grid_num
        self.orders = {}  # 记录订单

    def _calculate_grids(self):
        """计算网格价格"""
        step = (self.price_high - self.price_low) / self.grid_num
        return [self.price_low + i * step for i in range(self.grid_num + 1)]

    def place_orders(self, exchange, symbol):
        """挂网格订单"""
        current_price = exchange.fetch_ticker(symbol)['last']

        for price in self.grid_prices:
            if price < current_price:
                # 低于当前价的挂买单
                order = exchange.create_limit_buy_order(
                    symbol, self.order_amount / price, price
                )
            else:
                # 高于当前价的挂卖单
                order = exchange.create_limit_sell_order(
                    symbol, self.order_amount / price, price
                )
            self.orders[price] = order
```

### 6.3 第三阶段：高频与做市 (高难度)

#### 做市策略 (Market Making)

**原理**：
- 在买一和卖一之间挂单
- 赚取 Bid-Ask Spread + 交易所 Maker 返佣

**要求**：
- 延迟 < 10ms（服务器部署在交易所同机房）
- 高性能代码（C++/Rust）
- 风控极其严格

---

## 7. QuantBase 系统架构设计

### 7.1 整体架构图

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              用户浏览器 / Electron                            │
│  ┌───────────────────────────────────────────────────────────────────────┐  │
│  │                    React 前端应用 (Vite + TypeScript)                   │  │
│  │  ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌─────────────┐  │  │
│  │  │  Home   │  │ Market  │  │Strategy │  │Backtest │  │   Monitor   │  │  │
│  │  │ 首页看板│  │ 行情页  │  │ 策略开发│  │  回测   │  │   盯盘监控  │  │  │
│  │  └────┬────┘  └────┬────┘  └────┬────┘  └────┬────┘  └──────┬──────┘  │  │
│  └───────┼───────────┼───────────┼───────────┼───────────────┼──────────┘  │
│          │           │           │           │               │             │
└──────────┼───────────┼───────────┼───────────┼───────────────┼─────────────┘
           │           │           │           │               │
           ▼           ▼           ▼           ▼               ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         FastAPI 后端服务 (Python 3.11)                        │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │                           API 路由层 (/api/v1)                         │   │
│  │  ┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐   │   │
│  │  │market  │ │trading │ │strategy│ │backtest│ │monitor │ │  ai    │   │   │
│  │  └───┬────┘ └───┬────┘ └───┬────┘ └───┬────┘ └───┬────┘ └───┬────┘   │   │
│  └──────┼──────────┼──────────┼──────────┼──────────┼──────────┼────────┘   │
│         │          │          │          │          │          │            │
│  ┌──────▼──────────▼──────────▼──────────▼──────────▼──────────▼────────┐   │
│  │                          业务服务层 (Services)                         │   │
│  │  ┌─────────────┐ ┌─────────────┐ ┌─────────────┐ ┌─────────────────┐  │   │
│  │  │MarketService│ │TradingServic│ │StrategyServ │ │ BacktestService │  │   │
│  │  └──────┬──────┘ └──────┬──────┘ └──────┬──────┘ └────────┬────────┘  │   │
│  │  ┌──────┴──────┐ ┌──────┴──────┐ ┌──────┴──────┐ ┌────────┴────────┐  │   │
│  │  │MonitorServic│ │FundingServic│ │DataSyncServi│ │  AIService      │  │   │
│  │  └─────────────┘ └─────────────┘ └─────────────┘ └─────────────────┘  │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│         │                    │                    │                         │
└─────────┼────────────────────┼────────────────────┼─────────────────────────┘
          │                    │                    │
          ▼                    ▼                    ▼
┌─────────────────┐  ┌─────────────────┐  ┌─────────────────────────────────┐
│   SQLite 数据库  │  │   Redis 缓存    │  │         外部数据源               │
│   (历史数据)     │  │   (实时数据)    │  │  ┌─────────────────────────────┐ │
└─────────────────┘  └─────────────────┘  │  │ CCXT (Binance/OKX/Bybit)    │ │
                                          │  │ Coinglass (费率/持仓)        │ │
                                          │  │ Dune (链上数据)              │ │
                                          │  │ 千问大模型 (AI分析)          │ │
                                          │  └─────────────────────────────┘ │
                                          └─────────────────────────────────┘
```

### 7.2 技术栈

#### 后端技术

| 技术 | 版本 | 用途 |
|------|------|------|
| Python | 3.11+ | 运行时 |
| FastAPI | 0.104+ | Web 框架 |
| CCXT | latest | 交易所 API |
| SQLite | 3.x | 本地历史数据 |
| Redis | 7.x | 实时数据缓存 |
| APScheduler | 3.x | 定时任务 |
| Pandas | 2.x | 数据处理 |
| NumPy | 1.x | 数值计算 |

#### 前端技术

| 技术 | 版本 | 用途 |
|------|------|------|
| React | 18.x | UI 框架 |
| TypeScript | 5.x | 类型安全 |
| Vite | 5.x | 构建工具 |
| Tailwind CSS | 3.x | 样式框架 |
| Zustand | 4.x | 状态管理 |
| Apache ECharts | 5.x | 图表 (K线、深度图) |
| TradingView | - | 专业图表 (可选) |

### 7.3 目录结构

```
QuantBase/
├── backend/                    # 后端服务
│   ├── app/
│   │   ├── api/endpoints/      # API 端点
│   │   │   ├── market.py       # 行情数据 API
│   │   │   ├── trading.py      # 交易相关 API
│   │   │   ├── strategy.py     # 策略管理 API
│   │   │   ├── backtest.py     # 回测 API
│   │   │   ├── monitor.py      # 监控告警 API
│   │   │   └── ai.py           # AI 分析 API
│   │   ├── services/           # 业务服务
│   │   │   ├── market_service.py      # 行情服务
│   │   │   ├── trading_service.py     # 交易服务
│   │   │   ├── strategy_service.py    # 策略服务
│   │   │   ├── backtest_service.py    # 回测服务
│   │   │   ├── monitor_service.py     # 监控服务
│   │   │   ├── funding_service.py     # 资金费率服务
│   │   │   ├── data_sync_service.py   # 数据同步服务
│   │   │   └── ai_service.py          # AI 服务
│   │   ├── db/
│   │   │   └── local_db.py     # 本地数据库
│   │   ├── exchange/
│   │   │   ├── base.py         # 交易所基类
│   │   │   ├── binance.py      # Binance 封装
│   │   │   ├── okx.py          # OKX 封装
│   │   │   └── bybit.py        # Bybit 封装
│   │   ├── strategies/         # 策略模板
│   │   │   ├── contract_common.py
│   │   │   ├── cta_trend_following_strategy.py
│   │   │   ├── grid_trading_strategy.py
│   │   │   └── okx_funding_arbitrage_strategy.py
│   │   ├── core/
│   │   │   └── config.py       # 配置管理
│   │   ├── models/
│   │   │   └── schemas.py      # Pydantic 模型
│   │   └── main.py             # 应用入口
│   └── requirements.txt
│
├── frontend/                   # 前端应用
│   ├── src/
│   │   ├── pages/              # 页面组件
│   │   │   ├── Home.tsx        # 首页看板
│   │   │   ├── Market.tsx      # 行情页面
│   │   │   ├── Strategy.tsx    # 策略开发
│   │   │   ├── Backtest.tsx    # 回测页面
│   │   │   └── Monitor.tsx     # 监控页面
│   │   ├── components/         # 通用组件
│   │   │   ├── KlineChart.tsx  # K线图
│   │   │   ├── DepthChart.tsx  # 深度图
│   │   │   ├── OrderBook.tsx   # 订单簿
│   │   │   ├── FundingRate.tsx # 资金费率
│   │   │   └── ...
│   │   ├── api/                # API 客户端
│   │   ├── stores/             # 状态管理
│   │   └── types/              # 类型定义
│   └── package.json
│
├── docs/                       # 文档
│   ├── api.md                  # API 文档
│   ├── strategy_guide.md       # 策略开发指南
│   └── deployment.md           # 部署文档
│
├── scripts/                    # 脚本工具
│   ├── download_history.py     # 下载历史数据
│   ├── init_db.py              # 初始化数据库
│   └── sync_funding.py         # 同步资金费率
│
├── strategies/                 # 用户策略目录
│   └── my_strategy.py          # 用户自定义策略
│
├── data/                       # 数据目录
│   ├── klines/                 # K线数据
│   └── trades/                 # 成交数据
│
├── Crypto_Quant_Guide.md       # 本文档
└── README.md
```

### 7.4 页面功能设计

#### 首页看板 (Home)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              QuantBase - 首页看板                               │
├─────────────────────────────────────────────────────────────────────────────┤
│  BTC: $50,123 +2.5%  |  ETH: $3,456 +3.2%  |  BTC.D: 52.3%  |  恐惧贪婪: 65 │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌─────────────────────────────┐  ┌─────────────────────────────┐          │
│  │      资金费率套利机会        │  │        24H 爆仓数据          │          │
│  │  ┌─────────────────────┐   │  │                             │          │
│  │  │ Symbol  |  Rate     │   │  │  多头爆仓: $125M            │          │
│  │  │ BTC     |  0.015%   │   │  │  空头爆仓: $89M             │          │
│  │  │ ETH     |  0.012%   │   │  │  最大单笔: $5.2M (BTC)      │          │
│  │  │ SOL     |  0.025%   │   │  │                             │          │
│  │  └─────────────────────┘   │  └─────────────────────────────┘          │
│  └─────────────────────────────┘                                           │
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                         BTC/USDT K线图                               │   │
│  │  ┌───────────────────────────────────────────────────────────────┐  │   │
│  │  │                                                               │  │   │
│  │  │                      [TradingView Chart]                      │  │   │
│  │  │                                                               │  │   │
│  │  └───────────────────────────────────────────────────────────────┘  │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
│  ┌─────────────────────────────┐  ┌─────────────────────────────────────┐  │
│  │      持仓量变化              │  │          多空比                     │  │
│  │  [Open Interest Chart]      │  │  [Long/Short Ratio Chart]           │  │
│  └─────────────────────────────┘  └─────────────────────────────────────┘  │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

#### 行情页面 (Market)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  搜索: [BTC/USDT ▼]    交易所: [Binance ▼]    类型: [永续合约 ▼]            │
├─────────────────────────────────────────────────────────────────────────────┤
│  ┌───────────────────────────────────────────────────────────────────────┐ │
│  │                            K线图 + 技术指标                           │ │
│  │  ┌─────────────────────────────────────────────────────────────────┐ │ │
│  │  │                                                                 │ │ │
│  │  │                     [TradingView/ECharts]                       │ │ │
│  │  │                                                                 │ │ │
│  │  └─────────────────────────────────────────────────────────────────┘ │ │
│  │  指标: [MA] [MACD] [RSI] [BOLL] [Volume]                             │ │
│  └───────────────────────────────────────────────────────────────────────┘ │
├───────────────────────────────────┬─────────────────────────────────────────┤
│         订单簿 (Order Book)       │              成交记录                   │
│  ┌─────────────────────────────┐ │ ┌─────────────────────────────────────┐ │
│  │ Price     | Amount | Total  │ │ │ Time  | Price | Amount | Side      │ │
│  │ 50,125    | 1.23   | ...    │ │ │ 12:01 | 50120 | 0.5    | Buy       │ │
│  │ 50,120    | 2.56   | ...    │ │ │ 12:01 | 50118 | 0.3    | Sell      │ │
│  │ ────────────────────────── │ │ │ ...                                 │ │
│  │ 50,115    | 3.45   | ...    │ │ └─────────────────────────────────────┘ │
│  │ 50,110    | 1.89   | ...    │ │                                         │
│  └─────────────────────────────┘ │         深度图 (Depth Chart)            │
│                                   │ ┌─────────────────────────────────────┐ │
│                                   │ │     [Bid/Ask Depth Chart]           │ │
│                                   │ └─────────────────────────────────────┘ │
└───────────────────────────────────┴─────────────────────────────────────────┘
```

#### 策略开发页面 (Strategy)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  策略列表                                           [+ 新建策略]             │
├─────────────────────────────────────────────────────────────────────────────┤
│  ┌───────────────────────────────────────────────────────────────────────┐ │
│  │  📁 我的策略                                                          │ │
│  │  ├── 资金费率套利 (运行中 🟢)                                         │ │
│  │  ├── BTC网格交易 (已停止 🔴)                                          │ │
│  │  └── 双均线策略 (回测中 🟡)                                           │ │
│  │                                                                       │ │
│  │  📁 策略模板                                                          │ │
│  │  ├── okx_funding_arbitrage_strategy.py                               │ │
│  │  ├── grid_trading_strategy.py                                        │ │
│  │  └── cta_trend_following_strategy.py                                 │ │
│  └───────────────────────────────────────────────────────────────────────┘ │
├─────────────────────────────────────────────────────────────────────────────┤
│  策略编辑器                                                                 │
│  ┌───────────────────────────────────────────────────────────────────────┐ │
│  │  # 资金费率套利策略                                                   │ │
│  │  class FundingArbitrage(BaseStrategy):                                │ │
│  │      def __init__(self, config):                                      │ │
│  │          self.min_rate = config.get('min_rate', 0.0001)               │ │
│  │          self.position_size = config.get('size', 0.01)                │ │
│  │                                                                       │ │
│  │      def on_funding_rate(self, rate):                                 │ │
│  │          if rate > self.min_rate:                                     │ │
│  │              self.open_arbitrage()                                    │ │
│  │          ...                                                          │ │
│  └───────────────────────────────────────────────────────────────────────┘ │
│  [保存] [运行] [回测] [停止]                                                │
└─────────────────────────────────────────────────────────────────────────────┘
```

#### 回测页面 (Backtest)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  回测配置                                                                   │
├─────────────────────────────────────────────────────────────────────────────┤
│  策略: [双均线策略 ▼]    交易对: [BTC/USDT ▼]    交易所: [Binance ▼]        │
│  开始日期: [2025-01-01]  结束日期: [2025-12-31]  初始资金: [10000 USDT]     │
│  手续费: [0.04%]  滑点: [0.01%]                  [开始回测]                 │
├─────────────────────────────────────────────────────────────────────────────┤
│  回测结果                                                                   │
│  ┌───────────────────────────────────────────────────────────────────────┐ │
│  │  总收益: +32.5%  |  年化: +35.2%  |  最大回撤: -12.3%  |  夏普: 1.85  │ │
│  │  交易次数: 156   |  胜率: 58.3%   |  盈亏比: 1.42                      │ │
│  └───────────────────────────────────────────────────────────────────────┘ │
│  ┌───────────────────────────────────────────────────────────────────────┐ │
│  │                          净值曲线                                     │ │
│  │  ┌─────────────────────────────────────────────────────────────────┐ │ │
│  │  │                     [Equity Curve Chart]                        │ │ │
│  │  └─────────────────────────────────────────────────────────────────┘ │ │
│  └───────────────────────────────────────────────────────────────────────┘ │
│  ┌───────────────────────────────────────────────────────────────────────┐ │
│  │                         交易记录                                      │ │
│  │  Time       | Side | Price  | Amount | PnL      | 累计收益            │ │
│  │  01-15 10:00| Buy  | 45000  | 0.1    | -        | -                   │ │
│  │  01-20 14:30| Sell | 46500  | 0.1    | +150     | +150                │ │
│  │  ...                                                                  │ │
│  └───────────────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────────────┘
```

#### 监控盯盘页面 (Monitor)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  监控面板                                               [+ 添加监控]         │
├─────────────────────────────────────────────────────────────────────────────┤
│  ┌────────────────────────────────────┬────────────────────────────────────┐│
│  │        运行中的策略                 │           账户概览                 ││
│  │ ┌────────────────────────────────┐ │  总资产: $25,680.50               ││
│  │ │ 🟢 资金费率套利                 │ │  可用: $15,230.20                 ││
│  │ │    收益: +$230.50 (+0.92%)     │ │  持仓: $10,450.30                 ││
│  │ │    持仓: BTC +1 / -1           │ │                                    ││
│  │ │    运行时间: 3天 12小时         │ │  今日 PnL: +$156.80 (+0.61%)      ││
│  │ └────────────────────────────────┘ │                                    ││
│  │ ┌────────────────────────────────┐ │                                    ││
│  │ │ 🟢 BTC 网格交易                │ │                                    ││
│  │ │    收益: +$89.20 (+0.35%)      │ │                                    ││
│  │ │    成交: 23笔                   │ │                                    ││
│  │ └────────────────────────────────┘ │                                    ││
│  └────────────────────────────────────┴────────────────────────────────────┘│
├─────────────────────────────────────────────────────────────────────────────┤
│  告警设置                                                                   │
│  ┌───────────────────────────────────────────────────────────────────────┐ │
│  │  ⚠️ 价格告警: BTC > $55,000 或 < $45,000                              │ │
│  │  ⚠️ 资金费率告警: BTC 费率 > 0.03%                                    │ │
│  │  ⚠️ 持仓告警: 单仓位亏损 > 5%                                         │ │
│  │  ⚠️ 爆仓预警: 保证金率 < 50%                                          │ │
│  └───────────────────────────────────────────────────────────────────────┘ │
├─────────────────────────────────────────────────────────────────────────────┤
│  实时日志                                                                   │
│  ┌───────────────────────────────────────────────────────────────────────┐ │
│  │  [12:30:05] ✅ 资金费率结算: BTC 收取 +$15.50                         │ │
│  │  [12:28:33] 📊 网格成交: BTC 买入 0.01 @ $50,100                      │ │
│  │  [12:25:10] ⚠️ 告警触发: BTC 资金费率达到 0.025%                      │ │
│  │  [12:20:00] 📈 行情更新: BTC $50,150 (+1.2%)                          │ │
│  └───────────────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 8. 数据库设计

### 8.1 数据库概览

**数据库类型**：SQLite (本地) + Redis (实时缓存)

**文件位置**：
- macOS: `~/Library/Application Support/QuantBase/crypto_data.db`
- Windows: `~/AppData/Roaming/QuantBase/crypto_data.db`
- Linux: `~/.local/share/QuantBase/crypto_data.db`

### 8.2 表结构详解

#### 8.2.1 kline_history - K线历史数据表

```sql
CREATE TABLE IF NOT EXISTS kline_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    exchange TEXT NOT NULL,          -- 交易所 (binance/okx/bybit)
    symbol TEXT NOT NULL,            -- 交易对 (BTC/USDT)
    timeframe TEXT NOT NULL,         -- 周期 (1m/5m/15m/1h/4h/1d)
    timestamp INTEGER NOT NULL,      -- Unix 时间戳 (毫秒)
    open REAL NOT NULL,              -- 开盘价
    high REAL NOT NULL,              -- 最高价
    low REAL NOT NULL,               -- 最低价
    close REAL NOT NULL,             -- 收盘价
    volume REAL NOT NULL,            -- 成交量 (Base)
    quote_volume REAL,               -- 成交额 (Quote)
    trades_count INTEGER,            -- 成交笔数
    UNIQUE(exchange, symbol, timeframe, timestamp)
);

-- 索引
CREATE INDEX idx_kline_symbol_time ON kline_history(exchange, symbol, timeframe, timestamp);
```

#### 8.2.2 funding_rate_history - 资金费率历史表

```sql
CREATE TABLE IF NOT EXISTS funding_rate_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    exchange TEXT NOT NULL,          -- 交易所
    symbol TEXT NOT NULL,            -- 交易对 (BTCUSDT)
    timestamp INTEGER NOT NULL,      -- 结算时间
    funding_rate REAL NOT NULL,      -- 资金费率
    mark_price REAL,                 -- 标记价格
    UNIQUE(exchange, symbol, timestamp)
);

CREATE INDEX idx_funding_symbol_time ON funding_rate_history(exchange, symbol, timestamp);
```

#### 8.2.3 funding_rate_realtime - 资金费率实时表

```sql
CREATE TABLE IF NOT EXISTS funding_rate_realtime (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    exchange TEXT NOT NULL,
    symbol TEXT NOT NULL UNIQUE,
    current_rate REAL,               -- 当前费率
    predicted_rate REAL,             -- 预测费率
    next_funding_time INTEGER,       -- 下次结算时间
    mark_price REAL,
    index_price REAL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

#### 8.2.4 open_interest_history - 持仓量历史表

```sql
CREATE TABLE IF NOT EXISTS open_interest_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    exchange TEXT NOT NULL,
    symbol TEXT NOT NULL,
    timestamp INTEGER NOT NULL,
    open_interest REAL NOT NULL,     -- 持仓量 (张/币)
    open_interest_value REAL,        -- 持仓价值 (USDT)
    UNIQUE(exchange, symbol, timestamp)
);
```

#### 8.2.5 liquidation_history - 爆仓历史表

```sql
CREATE TABLE IF NOT EXISTS liquidation_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    exchange TEXT NOT NULL,
    symbol TEXT NOT NULL,
    timestamp INTEGER NOT NULL,
    side TEXT NOT NULL,              -- LONG/SHORT
    price REAL NOT NULL,
    quantity REAL NOT NULL,
    value REAL NOT NULL              -- 爆仓价值 (USDT)
);

CREATE INDEX idx_liq_time ON liquidation_history(timestamp);
```

#### 8.2.6 trades_history - 成交历史表

```sql
CREATE TABLE IF NOT EXISTS trades_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    exchange TEXT NOT NULL,
    symbol TEXT NOT NULL,
    trade_id TEXT NOT NULL,
    timestamp INTEGER NOT NULL,
    side TEXT NOT NULL,              -- BUY/SELL
    price REAL NOT NULL,
    quantity REAL NOT NULL,
    quote_quantity REAL,
    is_maker BOOLEAN,
    UNIQUE(exchange, symbol, trade_id)
);
```

#### 8.2.7 strategies - 策略表

```sql
CREATE TABLE IF NOT EXISTS strategies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    description TEXT,
    script_content TEXT NOT NULL,    -- Python 策略代码
    config TEXT,                     -- JSON 配置
    status TEXT DEFAULT 'stopped',   -- running/stopped/error
    exchange TEXT,
    symbols TEXT,                    -- JSON 数组
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

#### 8.2.8 strategy_trades - 策略交易记录表

```sql
CREATE TABLE IF NOT EXISTS strategy_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_id INTEGER NOT NULL,
    exchange TEXT NOT NULL,
    symbol TEXT NOT NULL,
    order_id TEXT,
    timestamp INTEGER NOT NULL,
    side TEXT NOT NULL,              -- BUY/SELL
    type TEXT NOT NULL,              -- MARKET/LIMIT
    price REAL NOT NULL,
    quantity REAL NOT NULL,
    fee REAL,
    fee_asset TEXT,
    pnl REAL,                        -- 平仓盈亏
    FOREIGN KEY (strategy_id) REFERENCES strategies(id)
);
```

#### 8.2.9 backtest_results - 回测结果表

```sql
CREATE TABLE IF NOT EXISTS backtest_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_id INTEGER NOT NULL,
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    initial_capital REAL NOT NULL,
    final_capital REAL NOT NULL,
    total_return REAL,               -- 总收益率
    annual_return REAL,              -- 年化收益率
    max_drawdown REAL,               -- 最大回撤
    sharpe_ratio REAL,               -- 夏普比率
    win_rate REAL,                   -- 胜率
    profit_factor REAL,              -- 盈亏比
    total_trades INTEGER,
    trades_detail TEXT,              -- JSON 交易明细
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (strategy_id) REFERENCES strategies(id)
);
```

#### 8.2.10 alerts - 告警配置表

```sql
CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    type TEXT NOT NULL,              -- price/funding/position/liquidation
    symbol TEXT,
    condition TEXT NOT NULL,         -- JSON 条件
    notification TEXT,               -- JSON 通知方式 (telegram/email/webhook)
    enabled BOOLEAN DEFAULT 1,
    last_triggered_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

#### 8.2.11 exchange_configs - 交易所配置表

```sql
CREATE TABLE IF NOT EXISTS exchange_configs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    exchange TEXT NOT NULL UNIQUE,
    api_key TEXT,                    -- 加密存储
    api_secret TEXT,                 -- 加密存储
    passphrase TEXT,                 -- OKX 需要
    testnet BOOLEAN DEFAULT 0,
    enabled BOOLEAN DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### 8.3 Redis 缓存设计

```python
# Redis Key 设计

# 实时行情 (Hash)
"ticker:{exchange}:{symbol}"
# 例: "ticker:binance:BTCUSDT"
# 字段: last, bid, ask, volume, change_percent, ...

# 订单簿 (Sorted Set)
"orderbook:{exchange}:{symbol}:bids"
"orderbook:{exchange}:{symbol}:asks"
# Score: 价格, Member: 数量

# 资金费率 (Hash)
"funding:{exchange}:{symbol}"
# 字段: current_rate, predicted_rate, next_time, mark_price

# 持仓量 (String)
"oi:{exchange}:{symbol}"

# 策略状态 (Hash)
"strategy:{id}:status"
# 字段: status, pnl, positions, last_update

# 告警状态 (String)
"alert:{id}:triggered"
```

### 8.4 表关系图

```
┌─────────────────────┐
│   exchange_configs  │
│   (交易所配置)       │
└─────────────────────┘
          │
          ▼
┌─────────────────────┐     ┌─────────────────────┐
│   kline_history     │     │ funding_rate_history│
│   (K线历史)         │     │ (资金费率历史)       │
└─────────────────────┘     └─────────────────────┘
                                      │
┌─────────────────────┐               ▼
│ open_interest_hist  │     ┌─────────────────────┐
│ (持仓量历史)         │     │funding_rate_realtime│
└─────────────────────┘     │ (资金费率实时)       │
                            └─────────────────────┘
┌─────────────────────┐
│ liquidation_history │
│ (爆仓历史)          │
└─────────────────────┘

┌─────────────────────┐
│     strategies      │
│     (策略表)        │
└──────────┬──────────┘
           │
     ┌─────┴─────┐
     ▼           ▼
┌─────────────┐ ┌─────────────────┐
│strategy_trad│ │ backtest_results│
│(策略交易记录)│ │  (回测结果)      │
└─────────────┘ └─────────────────┘

┌─────────────────────┐
│      alerts         │
│    (告警配置)        │
└─────────────────────┘
```

---

## 9. API 接口设计

### 9.1 Market 模块 - 行情数据

#### GET /api/v1/market/ticker
**功能**：获取单个交易对行情

**请求参数**：
| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| exchange | string | 是 | 交易所 (binance/okx/bybit) |
| symbol | string | 是 | 交易对 (BTC/USDT) |

**响应示例**：
```json
{
  "exchange": "binance",
  "symbol": "BTC/USDT",
  "last": 50123.45,
  "bid": 50122.00,
  "ask": 50124.00,
  "volume": 12345.67,
  "quoteVolume": 618765432.10,
  "change": 1250.45,
  "changePercent": 2.56,
  "high": 51000.00,
  "low": 48500.00,
  "timestamp": 1706140800000
}
```

#### GET /api/v1/market/tickers
**功能**：获取多个交易对行情

**请求参数**：
| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| exchange | string | 是 | 交易所 |
| symbols | string | 否 | 交易对列表，逗号分隔 |

#### GET /api/v1/market/klines
**功能**：获取 K 线数据

**请求参数**：
| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| exchange | string | 是 | - | 交易所 |
| symbol | string | 是 | - | 交易对 |
| timeframe | string | 否 | 1h | 周期 (1m/5m/15m/1h/4h/1d) |
| limit | int | 否 | 100 | 数量限制 (1-1000) |
| start | int | 否 | - | 开始时间戳 (毫秒) |
| end | int | 否 | - | 结束时间戳 (毫秒) |

**响应示例**：
```json
[
  {
    "timestamp": 1706140800000,
    "open": 50000.00,
    "high": 50500.00,
    "low": 49800.00,
    "close": 50123.45,
    "volume": 1234.56
  }
]
```

#### GET /api/v1/market/orderbook
**功能**：获取订单簿深度

**请求参数**：
| 参数 | 类型 | 必填 | 默认值 |
|------|------|------|--------|
| exchange | string | 是 | - |
| symbol | string | 是 | - |
| limit | int | 否 | 20 |

**响应示例**：
```json
{
  "bids": [[50100.00, 1.5], [50095.00, 2.3], ...],
  "asks": [[50105.00, 1.2], [50110.00, 3.1], ...],
  "timestamp": 1706140800000
}
```

#### GET /api/v1/market/trades
**功能**：获取最近成交

### 9.2 Funding 模块 - 资金费率

#### GET /api/v1/funding/rates
**功能**：获取所有交易对当前资金费率

**响应示例**：
```json
[
  {
    "exchange": "binance",
    "symbol": "BTCUSDT",
    "currentRate": 0.0001,
    "predictedRate": 0.00012,
    "nextFundingTime": 1706140800000,
    "markPrice": 50123.45,
    "indexPrice": 50120.00
  }
]
```

#### GET /api/v1/funding/rate/{symbol}
**功能**：获取单个交易对资金费率详情

#### GET /api/v1/funding/history
**功能**：获取资金费率历史

**请求参数**：
| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| exchange | string | 是 | 交易所 |
| symbol | string | 是 | 交易对 |
| limit | int | 否 | 数量 (默认100) |

#### GET /api/v1/funding/opportunities
**功能**：获取套利机会（费率排行）

**响应示例**：
```json
[
  {
    "symbol": "SOLUSDT",
    "rate": 0.00025,
    "annualized": 27.375,
    "nextFundingTime": 1706140800000
  },
  {
    "symbol": "BTCUSDT",
    "rate": 0.0001,
    "annualized": 10.95,
    "nextFundingTime": 1706140800000
  }
]
```

### 9.3 Trading 模块 - 交易

#### POST /api/v1/trading/order
**功能**：下单

**请求体**：
```json
{
  "exchange": "binance",
  "symbol": "BTC/USDT",
  "side": "buy",
  "type": "limit",
  "amount": 0.01,
  "price": 50000,
  "params": {
    "leverage": 10,
    "marginMode": "isolated"
  }
}
```

#### DELETE /api/v1/trading/order/{orderId}
**功能**：撤单

#### GET /api/v1/trading/orders
**功能**：获取订单列表

#### GET /api/v1/trading/positions
**功能**：获取持仓

**响应示例**：
```json
[
  {
    "symbol": "BTC/USDT",
    "side": "long",
    "amount": 0.1,
    "entryPrice": 50000,
    "markPrice": 50500,
    "liquidationPrice": 45000,
    "unrealizedPnl": 50.00,
    "leverage": 10,
    "marginMode": "isolated"
  }
]
```

#### GET /api/v1/trading/balance
**功能**：获取账户余额

### 9.4 Strategy 模块 - 策略

#### GET /api/v1/strategy/list
**功能**：获取策略列表

#### POST /api/v1/strategy/create
**功能**：创建策略

**请求体**：
```json
{
  "name": "BTC 资金费率套利",
  "description": "当费率 > 0.01% 时开仓",
  "scriptContent": "class MyStrategy(BaseStrategy): ...",
  "config": {
    "minRate": 0.0001,
    "positionSize": 0.1
  },
  "exchange": "binance",
  "symbols": ["BTC/USDT"]
}
```

#### POST /api/v1/strategy/{id}/start
**功能**：启动策略

#### POST /api/v1/strategy/{id}/stop
**功能**：停止策略

#### GET /api/v1/strategy/{id}/trades
**功能**：获取策略交易记录

### 9.5 Backtest 模块 - 回测

#### POST /api/v1/backtest/run
**功能**：运行回测

**请求体**：
```json
{
  "strategyId": 1,
  "exchange": "binance",
  "symbol": "BTC/USDT",
  "timeframe": "1h",
  "startDate": "2025-01-01",
  "endDate": "2025-12-31",
  "initialCapital": 10000,
  "commission": 0.0004,
  "slippage": 0.0001
}
```

**响应示例**：
```json
{
  "id": 1,
  "status": "completed",
  "result": {
    "totalReturn": 0.325,
    "annualReturn": 0.352,
    "maxDrawdown": -0.123,
    "sharpeRatio": 1.85,
    "winRate": 0.583,
    "profitFactor": 1.42,
    "totalTrades": 156,
    "trades": [...]
  }
}
```

#### GET /api/v1/backtest/results
**功能**：获取回测结果列表

### 9.6 Monitor 模块 - 监控告警

#### GET /api/v1/monitor/alerts
**功能**：获取告警列表

#### POST /api/v1/monitor/alert/create
**功能**：创建告警

**请求体**：
```json
{
  "name": "BTC 价格告警",
  "type": "price",
  "symbol": "BTC/USDT",
  "condition": {
    "operator": ">",
    "value": 55000
  },
  "notification": {
    "telegram": true,
    "webhook": "https://your-webhook.com"
  }
}
```

#### GET /api/v1/monitor/liquidations
**功能**：获取爆仓数据

#### GET /api/v1/monitor/long-short-ratio
**功能**：获取多空比

---

## 10. 开发计划与里程碑

### 10.1 Phase 1: 基础架构 (Week 1-2)

| 任务 | 优先级 | 状态 | 说明 |
|------|--------|------|------|
| 项目初始化 | P0 | 待开始 | 创建目录结构、配置文件 |
| 数据库设计实现 | P0 | 待开始 | SQLite 表创建、初始化 |
| CCXT 交易所封装 | P0 | 待开始 | Binance/OKX/Bybit 统一接口 |
| 后端 FastAPI 框架 | P0 | 待开始 | API 路由、中间件 |
| 前端 React 框架 | P0 | 待开始 | 页面路由、布局 |

### 10.2 Phase 2: 行情模块 (Week 3-4)

| 任务 | 优先级 | 状态 | 说明 |
|------|--------|------|------|
| 实时行情 API | P0 | 待开始 | ticker、orderbook、trades |
| K线数据 API | P0 | 待开始 | 历史 K线获取与存储 |
| WebSocket 实时推送 | P1 | 待开始 | 行情实时更新 |
| K线图组件 | P0 | 待开始 | ECharts/TradingView |
| 深度图组件 | P1 | 待开始 | 买卖盘可视化 |
| 资金费率模块 | P0 | 待开始 | 费率获取、套利机会 |

### 10.3 Phase 3: 策略模块 (Week 5-6)

| 任务 | 优先级 | 状态 | 说明 |
|------|--------|------|------|
| 策略基类设计 | P0 | 待开始 | BaseStrategy 抽象类 |
| 策略管理 API | P0 | 待开始 | CRUD、启停 |
| 策略编辑器 | P1 | 待开始 | Monaco Editor |
| 内置策略模板 | P1 | 待开始 | 费率套利、网格、均线 |
| 回测引擎 | P0 | 待开始 | 历史数据回测 |
| 回测结果可视化 | P1 | 待开始 | 净值曲线、交易记录 |

### 10.4 Phase 4: 交易与监控 (Week 7-8)

| 任务 | 优先级 | 状态 | 说明 |
|------|--------|------|------|
| 交易 API | P0 | 待开始 | 下单、撤单、查询 |
| 持仓管理 | P0 | 待开始 | 持仓展示、风控 |
| 告警系统 | P1 | 待开始 | 价格/费率/持仓告警 |
| Telegram 通知 | P2 | 待开始 | 消息推送 |
| 监控面板 | P1 | 待开始 | 策略运行状态 |

### 10.5 Phase 5: 优化与扩展 (Week 9+)

| 任务 | 优先级 | 状态 | 说明 |
|------|--------|------|------|
| AI 分析模块 | P2 | 待开始 | 大模型市场分析 |
| 链上数据集成 | P2 | 待开始 | 巨鲸追踪、DeFi 数据 |
| 多账户管理 | P2 | 待开始 | 支持多个 API Key |
| 移动端适配 | P3 | 待开始 | 响应式设计 |
| Electron 打包 | P3 | 待开始 | 桌面应用 |

---

## 11. 避坑指南与风险控制

### 11.1 技术风险

#### API 限频 (Rate Limit)

```python
# 各交易所限频规则（大致）
RATE_LIMITS = {
    'binance': {
        'requests_per_minute': 1200,
        'orders_per_second': 10,
        'orders_per_day': 200000
    },
    'okx': {
        'requests_per_second': 20,
        'orders_per_second': 60
    },
    'bybit': {
        'requests_per_minute': 120,
        'orders_per_minute': 100
    }
}

# 解决方案：
# 1. 使用 WebSocket 代替轮询
# 2. 实现请求队列和限速器
# 3. 缓存不频繁变化的数据
```

#### 网络延迟

```python
# 延迟优化
# 1. 服务器部署在交易所附近
#    - Binance: AWS 东京 (ap-northeast-1)
#    - OKX: AWS 东京
#    - Bybit: AWS 新加坡

# 2. 使用 WebSocket 保持长连接
# 3. 预热连接，避免冷启动
```

#### 代码安全

```python
# ❌ 错误做法：API Key 硬编码
api_key = "sk-xxxx"  # 千万不要这样！

# ✅ 正确做法：环境变量 + 加密存储
import os
from cryptography.fernet import Fernet

api_key = os.environ.get('BINANCE_API_KEY')

# 或者从加密配置文件读取
cipher = Fernet(encryption_key)
api_key = cipher.decrypt(encrypted_api_key)
```

### 11.2 交易风险

#### 杠杆风险

```python
# 杠杆使用建议
LEVERAGE_GUIDELINES = {
    '新手': '1-3倍，或不使用杠杆',
    '有经验': '5-10倍，严格止损',
    '高频/套利': '可以更高，但必须有对冲'
}

# 强平价格计算（简化）
def calculate_liquidation_price(entry_price, leverage, side):
    """
    逐仓模式下的强平价格估算
    """
    maintenance_margin_rate = 0.005  # 维持保证金率 0.5%

    if side == 'long':
        # 多头强平价 = 入场价 * (1 - 1/杠杆 + 维持保证金率)
        return entry_price * (1 - 1/leverage + maintenance_margin_rate)
    else:
        # 空头强平价 = 入场价 * (1 + 1/杠杆 - 维持保证金率)
        return entry_price * (1 + 1/leverage - maintenance_margin_rate)

# 示例
entry = 50000
leverage = 20
liq_price = calculate_liquidation_price(entry, leverage, 'long')
print(f"20倍多头，50000入场，强平价约: {liq_price}")  # 约 47750
```

#### 滑点控制

```python
# 滑点来源
# 1. 市价单与当前价格的差异
# 2. 大单冲击成本

# 解决方案
# 1. 使用限价单
# 2. 大单拆分（TWAP/冰山单）
# 3. 选择流动性好的交易对

def estimate_slippage(orderbook, amount, side):
    """估算滑点"""
    if side == 'buy':
        orders = orderbook['asks']
    else:
        orders = orderbook['bids']

    total_amount = 0
    total_cost = 0

    for price, qty in orders:
        fill = min(qty, amount - total_amount)
        total_amount += fill
        total_cost += fill * price

        if total_amount >= amount:
            break

    avg_price = total_cost / total_amount
    best_price = orders[0][0]
    slippage = abs(avg_price - best_price) / best_price

    return slippage
```

### 11.3 交易所风险

#### 历史事件

| 事件 | 时间 | 影响 | 教训 |
|------|------|------|------|
| Mt.Gox 破产 | 2014 | 85万 BTC 被盗 | 不要把币放交易所 |
| FTX 暴雷 | 2022 | 数十亿美元损失 | 分散存放、定期提现 |
| Binance 被黑 | 2019 | 7000 BTC 被盗 | 交易所也有风险 |

#### 风险控制

```python
# 资金分散原则
FUND_ALLOCATION = {
    'binance': 0.4,    # 40% - 流动性最好
    'okx': 0.3,        # 30% - 备用
    'cold_wallet': 0.3  # 30% - 冷钱包保管
}

# 单交易所最大仓位
MAX_SINGLE_EXCHANGE_POSITION = 0.5  # 不超过总资金的 50%
```

### 11.4 回测陷阱

#### 常见问题

```python
# 1. 未来函数
# ❌ 错误：使用未来数据
signal = df['close'].shift(-1) > df['close']  # 使用了明天的数据

# ✅ 正确：只使用历史数据
signal = df['close'] > df['close'].shift(1)

# 2. 幸存者偏差
# ❌ 错误：只回测目前还存在的币种
# ✅ 正确：包含已退市的币种

# 3. 忽略滑点和手续费
# ❌ 错误：理想化的回测
backtest_return = (exit_price - entry_price) / entry_price

# ✅ 正确：考虑真实成本
commission = 0.0004  # 0.04% 手续费
slippage = 0.0001    # 0.01% 滑点
real_return = (exit_price * (1 - commission - slippage) -
               entry_price * (1 + commission + slippage)) / entry_price
```

---

## 附录

### A. CCXT 常用接口速查

```python
import ccxt

# 创建交易所实例
exchange = ccxt.binance({
    'apiKey': 'YOUR_API_KEY',
    'secret': 'YOUR_SECRET',
    'options': {'defaultType': 'future'}  # 永续合约
})

# 公共接口（无需 API Key）
exchange.fetch_ticker('BTC/USDT')         # 行情
exchange.fetch_order_book('BTC/USDT')     # 深度
exchange.fetch_trades('BTC/USDT')         # 成交
exchange.fetch_ohlcv('BTC/USDT', '1h')    # K线
exchange.fetch_funding_rate('BTC/USDT')   # 资金费率

# 私有接口（需要 API Key）
exchange.fetch_balance()                   # 余额
exchange.fetch_positions()                 # 持仓
exchange.fetch_open_orders('BTC/USDT')    # 未成交订单
exchange.fetch_my_trades('BTC/USDT')      # 成交记录

# 下单
exchange.create_order(
    symbol='BTC/USDT',
    type='limit',       # limit/market
    side='buy',         # buy/sell
    amount=0.01,
    price=50000,
    params={
        'leverage': 10,
        'marginMode': 'isolated'
    }
)

# 撤单
exchange.cancel_order(order_id, 'BTC/USDT')
```

### B. 环境配置模板

#### backend/.env

```bash
# 交易所 API 配置
BINANCE_API_KEY=your_binance_api_key
BINANCE_API_SECRET=your_binance_secret
BINANCE_TESTNET=false

OKX_API_KEY=your_okx_api_key
OKX_API_SECRET=your_okx_secret
OKX_PASSPHRASE=your_okx_passphrase
OKX_TESTNET=false

BYBIT_API_KEY=your_bybit_api_key
BYBIT_API_SECRET=your_bybit_secret
BYBIT_TESTNET=false

# AI 服务配置
QWEN_API_KEY=your_qwen_api_key

# Redis 配置
REDIS_URL=redis://localhost:6379/0

# 应用配置
BACKEND_CORS_ORIGINS=["http://localhost:5173"]
LOG_LEVEL=INFO
```

#### frontend/.env

```bash
VITE_API_URL=/api/v1
VITE_WS_URL=ws://localhost:8000/ws
```

### C. 快速启动脚本

#### start.sh

```bash
#!/bin/bash

# 启动后端
echo "Starting backend..."
cd backend
source venv/bin/activate
uvicorn app.main:app --host 0.0.0.0 --port 8000 &
BACKEND_PID=$!

# 启动前端
echo "Starting frontend..."
cd ../frontend
npm run dev &
FRONTEND_PID=$!

echo "Backend PID: $BACKEND_PID"
echo "Frontend PID: $FRONTEND_PID"

# 等待退出信号
trap "kill $BACKEND_PID $FRONTEND_PID" EXIT
wait
```

### D. 常用数据源链接

| 数据源 | 链接 | 说明 |
|--------|------|------|
| Binance Public Data | https://data.binance.vision/ | 免费历史数据 |
| Coinglass | https://www.coinglass.com/ | 资金费率、持仓、爆仓 |
| Glassnode | https://glassnode.com/ | 链上数据 |
| Dune Analytics | https://dune.com/ | 链上数据分析 |
| Fear & Greed Index | https://alternative.me/crypto/fear-and-greed-index/ | 市场情绪 |
| CoinMarketCap | https://coinmarketcap.com/ | 市值排名 |
| TradingView | https://www.tradingview.com/ | 图表工具 |

### E. 学习资源

| 资源 | 链接 | 说明 |
|------|------|------|
| CCXT 文档 | https://docs.ccxt.com/ | 交易所 API 封装库 |
| Binance API 文档 | https://binance-docs.github.io/apidocs/ | 币安官方 API |
| OKX API 文档 | https://www.okx.com/docs-v5/ | OKX v5 API |
| Bybit API 文档 | https://bybit-exchange.github.io/docs/ | Bybit API |
| VeighNa 文档 | https://www.vnpy.com/ | 量化交易框架 |
| Freqtrade 文档 | https://www.freqtrade.io/ | 开源交易机器人 |

---

## 更新日志

### v1.0 (2026-01-24)
- 初始版本
- 完整的 B 圈量化入门指南
- QuantBase 系统架构设计
- 数据库设计
- API 接口设计
- 开发计划

---

> 文档维护：个人项目
> 最后更新：2026-01-24
>
> **免责声明**：本文档仅供学习交流，不构成任何投资建议。加密货币市场风险极高，请谨慎投资。
