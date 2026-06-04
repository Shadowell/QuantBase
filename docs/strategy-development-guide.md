# QuantBase 策略开发与模板指南

> **版本**: 2.0 | **更新日期**: 2026-04-28 | **适用对象**: 策略开发者

---

## 目录

1. [架构概览](#1-架构概览)
2. [核心数据结构](#2-核心数据结构)
3. [策略生命周期与模板方法](#3-策略生命周期与模板方法)
4. [交易 API 与持仓管理](#4-交易-api-与持仓管理)
5. [实战教学：从零写一个策略](#5-实战教学从零写一个策略)
6. [避坑指南与最佳实践](#6-避坑指南与最佳实践)
7. [附录：已有策略注册表](#7-附录已有策略注册表)

---

## 1. 架构概览

### 1.1 异步事件驱动架构

QuantBase 的策略引擎基于 Python `asyncio` 事件循环构建。整个执行流程如下：

```
┌─────────────────────────────────────────────────────────┐
│                   asyncio Event Loop                     │
│                                                          │
│   ┌──────────┐    ┌──────────────┐    ┌──────────────┐  │
│   │ Exchange  │───▶│  K线/Tick    │───▶│  策略引擎     │  │
│   │ (OKX)    │    │  数据拉取     │    │  on_bar()    │  │
│   └──────────┘    └──────────────┘    └──────┬───────┘  │
│                                              │           │
│                                     ┌────────▼────────┐  │
│                                     │   Broker 接口    │  │
│                                     │  buy/sell/close  │  │
│                                     └────────┬────────┘  │
│                              ┌───────────────┼──────────┐│
│                              ▼               ▼          ▼│
│                        PaperBroker     LiveBroker   BT Broker│
│                        (模拟盘)        (实盘)      (回测) │
└─────────────────────────────────────────────────────────┘
```

**关键点**：

- 策略引擎运行在一个 `asyncio.Task` 中，每根新 K 线到来时调用 `await strategy.on_bar(bar)`。
- 所有策略方法都是 `async def`，与 FastAPI 后端共享同一个事件循环。
- **绝对不能**在策略代码中使用阻塞调用（如 `time.sleep()`），否则会冻结整个事件循环。

### 1.2 投研分离与回测实盘同构

QuantBase 的核心设计理念是**"一套代码，三处运行"**：

| 运行环境 | Broker 实现 | 数据来源 | 说明 |
|---------|------------|---------|------|
| **模拟盘** | `PaperBroker` | OKX 实时 K 线 | 内存撮合，虚拟资金 |
| **实盘** | `LiveBroker` | OKX 实时 K 线 | 通过 CCXT 发送真实订单 |
| **回测** | `BacktraderBroker` | 本地历史数据 | Backtrader 引擎撮合 |

> 社区版默认关闭实盘执行。HTTP 实盘变更入口需要服务端显式设置 `QUANTBASE_LIVE_TRADING_ENABLED=1`，MCP 实盘变更工具还需要单独设置 `QUANTBASE_MCP_ENABLE_LIVE_TRADING=1`。仅配置交易所 API key 不会自动启用真实下单路径。

策略代码**完全不需要关心**当前运行在哪种环境中。你只需要调用 `await self.buy(...)`，底层 Broker 会自动处理撮合逻辑。

**这意味着：**
- ❌ 禁止在策略中直接调用 CCXT、交易所 API、数据库
- ❌ 禁止使用 Backtrader 内置指标（`bt.indicators.EMA` 等）
- ✅ 只依赖 `BaseStrategy` 提供的 `buy()` / `sell()` / `close_position()`
- ✅ 策略自行维护指标状态（用 `deque` / 纯 Python 计算）

---

## 2. 核心数据结构

所有数据结构定义在 `backend/app/core/execution/base_strategy.py` 中。

### 2.1 BarData — K 线数据

```python
@dataclass(frozen=True)
class BarData:
    exchange: str       # 交易所名称，如 "okx"
    symbol: str         # 交易对，如 "BTC/USDT"
    timeframe: str      # K线周期，如 "1m", "5m", "1h", "4h", "1d"
    timestamp: int      # Bar 起始时间（毫秒级 Unix 时间戳）
    open: float         # 开盘价
    high: float         # 最高价
    low: float          # 最低价
    close: float        # 收盘价
    volume: float       # 成交量
```

**注意事项**：
- `BarData` 是 `frozen=True` 的不可变对象，你**不能**修改它的字段。
- `timestamp` 是**毫秒级**时间戳（不是秒级）。转换为可读时间：`datetime.fromtimestamp(bar.timestamp / 1000)`。
- 策略引擎传入的总是**已收盘**的完整 K 线（不是正在形成中的未完成 bar）。

### 2.2 TickData — Tick 数据

```python
@dataclass(frozen=True)
class TickData:
    exchange: str           # 交易所
    symbol: str             # 交易对
    timestamp: int          # 毫秒时间戳
    last: float             # 最新成交价
    bid: Optional[float]    # 买一价
    ask: Optional[float]    # 卖一价
    volume: Optional[float] # 成交量
```

**使用场景**：`on_tick()` 是可选的高频钩子，大多数策略只需要 `on_bar()`。

### 2.3 StrategyState — 策略运行状态

```python
@dataclass
class StrategyState:
    strategy_id: int                    # 策略 ID（数据库主键）
    name: str                           # 策略名称
    exchange: str                       # 交易所
    symbols: List[str]                  # 交易对列表
    created_at: datetime                # 创建时间
    status: str                         # "running" / "paused" / "stopped" / "error"
    error_message: Optional[str]        # 错误信息
    positions: Dict[str, float]         # 持仓快照（含 _capital 键表示可用资金）
```

**在策略中访问**：

```python
# 获取可用资金（PaperBroker 会写入此字段）
capital = self.state.positions.get("_capital", 10000.0)

# 获取交易对列表
symbols = self.symbols()  # 返回 tuple，如 ("BTC/USDT",)
```

---

## 3. 策略生命周期与模板方法

一个策略从创建到销毁，会依次经历以下生命周期：

```
实例化 → on_init() → on_start() → [预热 warmup] → on_bar() 循环 → on_stop()
```

### 3.1 `async def on_init(self)` — 初始化

**调用时机**：策略实例化后、进入主循环前，只调用一次。

**职责**：
- 从 `self.config` 读取参数
- 初始化指标容器（`deque`）
- 声明内部状态变量

```python
async def on_init(self) -> None:
    # 从配置读取参数（不硬编码）
    self.period = self.config.get("period", 14)

    # 初始化定长历史窗口（防止内存泄漏）
    self._closes = deque(maxlen=self.period + 1)

    # 初始化持仓状态
    self._in_position = False
```

**注意**：`on_init` 中**不要**访问交易所、不要发请求、不要做 I/O。

### 3.2 `async def on_start(self)` — 策略启动

**调用时机**：`on_init()` 之后，主循环之前。

**职责**：可选。一般用于打印日志或做一些需要网络的初始化。

```python
async def on_start(self) -> None:
    logger.info("策略启动: symbols=%s", self.symbols())
```

### 3.3 `async def on_bar(self, bar: BarData)` — 核心逻辑（必须实现）

**调用时机**：每根新的已收盘 K 线到来时触发（模拟盘/实盘/回测都走这个入口）。

**这是策略的灵魂方法**，你的交易逻辑全部写在这里。

```python
async def on_bar(self, bar: BarData) -> None:
    # 1. 更新历史数据
    self._closes.append(bar.close)

    # 2. 数据不够时跳过
    if len(self._closes) < self.period:
        return

    # 3. 计算指标
    rsi = self._calc_rsi(list(self._closes))

    # 4. 生成信号 & 下单
    if rsi < 30 and not self._in_position:
        await self.buy(bar.symbol, amount)
        self._in_position = True
```

**关键规则**：
- `on_bar` 是 `async` 方法，内部可以 `await` 异步操作
- 不要在 `on_bar` 中使用 `time.sleep()` — 用 `await asyncio.sleep()`
- 不要让异常逃逸 — 用 `try/except` 包裹可能出错的代码

### 3.4 `async def on_tick(self, tick: TickData)` — Tick 驱动（可选）

大多数策略不需要实现此方法。只有需要高频行情（如剥头皮）时才考虑。

### 3.5 `async def on_stop(self)` — 策略停止

**调用时机**：策略被手动停止或引擎关闭时。

**职责**：清理资源、打印统计信息。

```python
async def on_stop(self) -> None:
    logger.info("策略停止，共交易 %d 次", self._trade_count)
```

### 3.6 K 线预热（Warmup）

引擎在进入主循环前会自动拉取最近 100 根（可配置）历史 K 线，依次喂给 `on_bar()`，让你的 `deque` 填满、指标预热完毕。

在预热阶段：
- `PaperBroker` 会自动进入 `warmup_mode`，**拒绝所有下单请求**
- 价格会正常更新（`update_mark_price`），指标可以正常计算
- 预热结束后自动恢复正常交易

你可以通过配置 `warmup_bars` 控制预热根数：

```python
config = {
    "warmup_bars": 100,  # 预热 100 根 K 线（默认值）
}
```

---

## 4. 交易 API 与持仓管理

### 4.1 买入 — `await self.buy()`

```python
result = await self.buy(
    symbol="BTC/USDT",    # 交易对
    amount=0.001,          # 下单数量
    price=None,            # 价格（None=市价单）
    order_type="market",   # "market" 或 "limit"
)
```

**返回值** `OrderResult`：一个类 dict 对象，包含订单详情（成交价、数量、手续费等）。

### 4.2 卖出 — `await self.sell()`

```python
result = await self.sell(
    symbol="BTC/USDT",
    amount=0.001,
    price=None,
    order_type="market",
)
```

### 4.3 平仓 — `await self.close_position()`

```python
result = await self.close_position(symbol="BTC/USDT")
```

一键清空指定交易对的全部持仓，常用于止损/止盈/策略退出。

### 4.4 计算下单数量

推荐根据可用资金和风险比例动态计算：

```python
def _calc_order_size(self, price: float) -> float:
    if price <= 0:
        return 0.0

    # _capital 由 PaperBroker / 引擎写入，表示当前可用余额
    capital = self.state.positions.get("_capital", 10000.0)
    risk_fraction = self.config.get("risk_fraction", 0.9)
    return capital * risk_fraction / price
```

### 4.5 并发安全

`BaseStrategy` 内部的 `buy()` / `sell()` / `close_position()` 方法都已通过 `asyncio.Lock` 保护，**同一时刻只会执行一笔下单**，你不需要自己加锁。

---

## 5. 实战教学：从零写一个策略

下面我们从零编写一个 **RSI 超卖反弹策略**，展示完整的策略开发流程。

### 5.1 创建文件

在 `backend/app/strategies/` 下新建 `my_rsi_strategy.py`：

```python
"""
RSI 超卖反弹策略 — 完整开发教程示例
=======================================

信号逻辑：
    - RSI(14) 跌破 30 → 超卖 → 买入
    - RSI(14) 升破 70 → 超买 → 卖出
    - 止损：入场价下跌 3% 强制平仓
"""

from __future__ import annotations

import logging
from collections import deque
from typing import Optional

from app.core.execution.base_strategy import BaseStrategy, BarData

logger = logging.getLogger(__name__)


class MyRSIStrategy(BaseStrategy):
    """RSI 超卖反弹策略。"""

    # ==========================================================
    # 生命周期方法
    # ==========================================================

    async def on_init(self) -> None:
        """
        初始化：读取配置 → 创建容器 → 声明状态。
        """
        # ---- 从 self.config 读取参数（不硬编码） ----
        self.rsi_period: int = self.config.get("rsi_period", 14)
        self.oversold: float = self.config.get("oversold", 30.0)
        self.overbought: float = self.config.get("overbought", 70.0)
        self.stop_loss_pct: float = self.config.get("stop_loss_pct", 0.03)
        self.risk_fraction: float = self.config.get("risk_fraction", 0.9)

        # ---- 历史收盘价窗口（定长 deque，自动淘汰旧数据） ----
        #
        # 为什么用 deque 而不是 list？
        # - deque(maxlen=N) 超过 N 个元素时自动丢弃最早的
        # - list 会无限增长，运行数小时后可能占用数 GB 内存
        #
        # maxlen 为什么是 rsi_period + 2？
        # - RSI 需要 rsi_period+1 个收盘价来计算 rsi_period 个涨跌幅
        # - 多留 1 根余量，确保任何边界情况都有足够数据
        self._closes: deque[float] = deque(maxlen=self.rsi_period + 2)

        # ---- 持仓状态 ----
        self._in_position: bool = False
        self._entry_price: float = 0.0

        logger.info(
            "[MyRSI] on_init | RSI周期=%d 超卖=%.0f 超买=%.0f 止损=%.1f%%",
            self.rsi_period, self.oversold, self.overbought,
            self.stop_loss_pct * 100,
        )

    async def on_bar(self, bar: BarData) -> None:
        """
        每根 K 线触发的核心逻辑。
        """
        symbol = bar.symbol

        # ---- 1. 更新历史窗口 ----
        self._closes.append(bar.close)

        # ---- 2. 止损检查（有持仓时优先执行） ----
        if self._in_position and self._entry_price > 0:
            pnl_pct = (bar.close - self._entry_price) / self._entry_price
            if pnl_pct <= -self.stop_loss_pct:
                logger.info(
                    "[MyRSI] 止损触发 | 入场=%.2f 当前=%.2f 浮亏=%.2f%%",
                    self._entry_price, bar.close, pnl_pct * 100,
                )
                await self.close_position(symbol)
                self._in_position = False
                self._entry_price = 0.0
                return

        # ---- 3. 数据不够时跳过 ----
        if len(self._closes) < self.rsi_period + 1:
            return

        # ---- 4. 计算 RSI ----
        rsi = self._calc_rsi(list(self._closes), self.rsi_period)
        if rsi is None:
            return

        # ---- 5. 交易信号 ----
        if rsi < self.oversold and not self._in_position:
            amount = self._calc_order_size(bar.close)
            if amount > 0:
                logger.info(
                    "[MyRSI] RSI=%.1f 超卖 → 买入 | 价格=%.2f 数量=%.6f",
                    rsi, bar.close, amount,
                )
                await self.buy(symbol, amount)
                self._in_position = True
                self._entry_price = bar.close

        elif rsi > self.overbought and self._in_position:
            logger.info(
                "[MyRSI] RSI=%.1f 超买 → 卖出 | 价格=%.2f",
                rsi, bar.close,
            )
            await self.close_position(symbol)
            self._in_position = False
            self._entry_price = 0.0

    async def on_stop(self) -> None:
        logger.info("[MyRSI] on_stop")

    # ==========================================================
    # 指标计算（纯 Python，零外部依赖）
    # ==========================================================

    @staticmethod
    def _calc_rsi(closes: list[float], period: int) -> Optional[float]:
        """
        计算 RSI（相对强弱指标）。

        公式：
            RSI = 100 - 100 / (1 + RS)
            RS  = 平均涨幅 / 平均跌幅

        需要至少 period+1 个收盘价。
        """
        if len(closes) < period + 1:
            return None

        gains = []
        losses = []
        for i in range(len(closes) - period, len(closes)):
            diff = closes[i] - closes[i - 1]
            if diff > 0:
                gains.append(diff)
                losses.append(0.0)
            else:
                gains.append(0.0)
                losses.append(abs(diff))

        avg_gain = sum(gains) / period
        avg_loss = sum(losses) / period

        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    # ==========================================================
    # 辅助工具
    # ==========================================================

    def _calc_order_size(self, price: float) -> float:
        """根据可用资金动态计算下单量。"""
        if price <= 0:
            return 0.0
        capital = self.state.positions.get("_capital", 10000.0)
        return capital * self.risk_fraction / price
```

### 5.2 注册策略

在 `backend/app/strategies/__init__.py` 中添加导入：

```python
from app.strategies.my_rsi_strategy import MyRSIStrategy

STRATEGY_CLASSES = {
    # ... 已有策略 ...
    "my_rsi": MyRSIStrategy,
}
```

### 5.3 运行策略

在数据库 `strategies` 表中插入记录时，`config` 字段设为：

```json
{
    "module_path": "app.strategies.my_rsi_strategy",
    "class_name": "MyRSIStrategy",
    "timeframe": "1m",
    "rsi_period": 14,
    "oversold": 30,
    "overbought": 70,
    "stop_loss_pct": 0.03,
    "risk_fraction": 0.9,
    "is_paper_trading": true,
    "initial_capital": 10000.0,
    "warmup_bars": 100
}
```

### 5.4 回测验证

```python
from app.services.backtrader_engine import backtrader_engine
from app.strategies.my_rsi_strategy import MyRSIStrategy

report = backtrader_engine.run_strategy(
    strategy_class=MyRSIStrategy,
    symbol="BTC/USDT",
    timeframe="1h",
    start_date="2026-01-01",
    end_date="2026-04-01",
    strategy_config={"rsi_period": 14, "oversold": 30, "overbought": 70},
)
print(f"总收益率: {report.total_return_pct:.2f}%")
print(f"最大回撤: {report.max_drawdown_pct:.2f}%")
print(f"Sharpe: {report.sharpe_ratio:.4f}")
```

---

## 6. 避坑指南与最佳实践

### 🚫 陷阱 1：在 `on_bar` 中阻塞事件循环

```python
# ❌ 错误！会冻结整个系统
import time
import requests

async def on_bar(self, bar):
    time.sleep(1)                                  # 阻塞 1 秒
    resp = requests.get("https://api.example.com") # 同步 HTTP
```

```python
# ✅ 正确：使用异步等价物
import asyncio
import aiohttp

async def on_bar(self, bar):
    await asyncio.sleep(1)                          # 非阻塞等待

    async with aiohttp.ClientSession() as session:  # 异步 HTTP
        async with session.get("https://api.example.com") as resp:
            data = await resp.json()
```

如果必须调用同步库（如 ONNX Runtime），用 `run_in_executor`：

```python
import asyncio

async def on_bar(self, bar):
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, self._sync_heavy_compute, bar)
```

### 🚫 陷阱 2：用 list 无限累积 K 线数据

```python
# ❌ 错误！运行 24 小时后 _history 可能有 86400 条数据
self._history = []

async def on_bar(self, bar):
    self._history.append(bar.close)  # 永远在增长
```

```python
# ✅ 正确：用 deque 限制长度
from collections import deque

self._history = deque(maxlen=100)  # 最多保存 100 根

async def on_bar(self, bar):
    self._history.append(bar.close)  # 超过 100 自动丢弃最早的
```

### 🚫 陷阱 3：未捕获的异常导致引擎崩溃

```python
# ❌ 危险！除零异常会杀死整个策略任务
async def on_bar(self, bar):
    ratio = bar.close / (bar.open - bar.close)  # bar.open == bar.close 时除零
```

```python
# ✅ 安全：对可能出错的计算做保护
async def on_bar(self, bar):
    try:
        denominator = bar.open - bar.close
        if abs(denominator) < 1e-10:
            return
        ratio = bar.close / denominator
    except Exception as e:
        logger.warning("计算异常: %s", e)
```

### 🚫 陷阱 4：使用 Backtrader 内置指标

```python
# ❌ 错误！策略被焊死在 Backtrader 上，实盘无法运行
import backtrader as bt

class MyStrategy(BaseStrategy):
    async def on_init(self):
        self.ema = bt.indicators.EMA(period=20)  # 这在实盘中会报错
```

```python
# ✅ 正确：自行计算 EMA
class MyStrategy(BaseStrategy):
    async def on_init(self):
        self._closes = deque(maxlen=21)

    async def on_bar(self, bar):
        self._closes.append(bar.close)
        ema = self._calc_ema(list(self._closes), 20)
```

### 🚫 陷阱 5：硬编码参数

```python
# ❌ 不利于调优和复用
async def on_init(self):
    self.period = 14
    self.threshold = 30
```

```python
# ✅ 从 self.config 读取，通过数据库/前端动态调整
async def on_init(self):
    self.period = self.config.get("period", 14)
    self.threshold = self.config.get("threshold", 30)
```

### ✅ 最佳实践清单

| 实践 | 说明 |
|------|------|
| 参数全部走 `self.config` | 方便前端调参、回测扫参 |
| 历史数据用 `deque(maxlen=N)` | 防止内存无限增长 |
| 指标自行计算（纯 Python） | 保证回测/实盘同构 |
| 关键日志用 `logger.info` | 方便实盘调试追踪 |
| `on_bar` 内做异常保护 | 防止单次异常杀死策略 |
| 下单量动态计算 | 基于 `_capital` 和风险比例 |
| 加入止损/止盈/超时 | 模型/信号不可能永远对 |

---

## 7. 附录：已有策略注册表

所有已注册的策略位于 `backend/app/strategies/__init__.py` 的 `STRATEGY_CLASSES` 字典中：

| Key | 类名 | 文件 | 说明 |
|-----|------|------|------|
| `ema_cross` | `EMACrossStrategy` | `demo_ema_cross.py` | EMA 均线交叉（标准范例） |
| `dual_ma` | `DualMAStrategy` | `dual_ma.py` | 双均线金叉死叉 |
| `rsi_oversold` | `RSIOversoldStrategy` | `rsi_oversold.py` | RSI 超卖反弹 |
| `bollinger_reversion` | `BollingerReversionStrategy` | `bollinger_reversion.py` | 布林带均值回归 |
| `macd_rsi` | `MACDRsiStrategy` | `macd_rsi.py` | MACD+RSI 多因子 |
| `scalping` | `ScalpingStrategy` | `scalping.py` | 高频剥头皮 |
| `momentum_breakout` | `MomentumBreakoutStrategy` | `momentum_breakout.py` | 动量突破 |
| `trend_following` | `TrendFollowingStrategy` | `trend_following.py` | 趋势跟踪 |
| `ai_predict` | `AIPredictStrategy` | `ai_predict_strategy.py` | AI 时序预测 |

---

## 快速参考卡片

```
继承 BaseStrategy
      │
      ├── on_init()          读配置 → 建 deque → 声明状态
      ├── on_start()         打印日志（可选）
      ├── on_bar(bar)        更新窗口 → 算指标 → 风控 → 信号 → 下单
      ├── on_tick(tick)      高频场景（可选）
      └── on_stop()          打印统计（可选）

交易接口:
      await self.buy(symbol, amount)
      await self.sell(symbol, amount)
      await self.close_position(symbol)

资金查询:
      self.state.positions.get("_capital", 10000.0)

配置读取:
      self.config.get("param_name", default_value)
```
