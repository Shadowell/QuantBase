# OKX Signal Bot 自定义 JSON 格式

参考来源：[OKX 信号策略警报规范 2.0](https://www.okx.com/zh-hans/help/signal-bot-alert-message-specifications)，页面显示更新时间为 2025-04-01。

本文只归纳 QuantBase v1 需要使用的 **B 节通用兼容**格式：QuantBase 不走 TradingView PineScript 占位符，而是把已确定的 OKX 合约工具、动作、时间戳和通道令牌组装为 JSON，再按策略设置自动推送或人工确认后推送到 OKX Signal Bot webhook。

## QuantBase v1 最终 JSON

### 开多 / 开空

开仓使用 `percentage_balance`，表示按可用保证金比例下单。QuantBase 默认用 `amount=100`，即按该 Signal Bot 可用保证金的 100% 计算；实际风险仍由通道的 `最大保证金`、Bot 自身资金和 OKX 侧风控共同限制。

```json
{
  "action": "ENTER_LONG",
  "instrument": "BTC-USDT-SWAP",
  "signalToken": "<OKX_SIGNAL_TOKEN>",
  "timestamp": "2026-05-08T06:20:00Z",
  "maxLag": "30",
  "orderType": "market",
  "orderPriceOffset": "",
  "investmentType": "percentage_balance",
  "amount": "100"
}
```

开空只改 `action`：

```json
{
  "action": "ENTER_SHORT",
  "instrument": "BTC-USDT-SWAP",
  "signalToken": "<OKX_SIGNAL_TOKEN>",
  "timestamp": "2026-05-08T06:20:00Z",
  "maxLag": "30",
  "orderType": "market",
  "orderPriceOffset": "",
  "investmentType": "percentage_balance",
  "amount": "100"
}
```

### 平多 / 平空

平仓使用 `percentage_position`，表示按当前持仓比例平仓。全平时 `amount=100`；半仓平仓时 `amount=50`。

```json
{
  "action": "EXIT_LONG",
  "instrument": "BTC-USDT-SWAP",
  "signalToken": "<OKX_SIGNAL_TOKEN>",
  "timestamp": "2026-05-08T06:20:00Z",
  "maxLag": "30",
  "orderType": "market",
  "orderPriceOffset": "",
  "investmentType": "percentage_position",
  "amount": "100"
}
```

平空只改 `action`：

```json
{
  "action": "EXIT_SHORT",
  "instrument": "BTC-USDT-SWAP",
  "signalToken": "<OKX_SIGNAL_TOKEN>",
  "timestamp": "2026-05-08T06:20:00Z",
  "maxLag": "30",
  "orderType": "market",
  "orderPriceOffset": "",
  "investmentType": "percentage_position",
  "amount": "100"
}
```

## 字段说明

| 字段 | 是否必填 | QuantBase v1 取值 | 说明 |
| --- | --- | --- | --- |
| `action` | 必填 | `ENTER_LONG` / `ENTER_SHORT` / `EXIT_LONG` / `EXIT_SHORT` | 分别对应开多、开空、平多、平空。 |
| `instrument` | 必填 | `BTC-USDT-SWAP` 这类 OKX `instId` | QuantBase 会把 `BTC/USDT:USDT` 转成 `BTC-USDT-SWAP`。 |
| `signalToken` | 必填 | 通道配置中的 OKX token | 从 OKX Signal Bot 自定义 JSON 示例里复制。接口返回和页面展示必须脱敏。 |
| `timestamp` | 必填 | UTC ISO 时间，例如 `2026-05-08T06:20:00Z` | QuantBase 发送时生成，不使用 `{{timenow}}` 占位符。 |
| `maxLag` | 可选 | 默认 `"30"` | 最大可接受延迟秒数。OKX 文档说明用于避免处理过期信号；QuantBase 通道默认 30 秒。 |
| `orderType` | 可选 | `"market"` | v1 固定使用市价单。 |
| `orderPriceOffset` | 条件可选 | `""` | 市价单不需要价格偏移；限价单才需要偏移比例。v1 不用限价单。 |
| `investmentType` | 条件可选 | 开仓 `percentage_balance`；平仓 `percentage_position` | 开仓按可用保证金比例，平仓按当前持仓比例。 |
| `amount` | 条件可选 | `"100"` 或平仓比例字符串 | 对 `percentage_balance` 是可用保证金百分比；对 `percentage_position` 是持仓百分比。 |

## investmentType 和 amount 的组合

OKX Signal Bot 的开仓信号（`ENTER_LONG` / `ENTER_SHORT`）用 `investmentType` 解释 `amount` 的单位。两者必须一起看，不能只看 `amount` 数字大小。

| investmentType | amount 含义 | 示例 | 适用说明 |
| --- | --- | --- | --- |
| `margin` | 本次开仓投入的计价货币保证金金额，例如 USDT。 | `amount="100"` 且 Bot 杠杆为 10 倍时，约使用 100 USDT 保证金开价值约 1000 USDT 的仓位。 | 推荐用于明确控制每笔投入保证金金额的场景。 |
| `base` | 基础货币数量，例如 DOGE。 | `amount="1000"` 表示按 Bot 规则买入或卖出 1000 个 DOGE 对应的仓位。 | 适合按币数量控制仓位，但低价币和合约面值需要特别注意。 |
| `percentage_balance` | Bot 当前可用余额百分比。 | `amount="10"` 表示使用 Bot 当前可用资金的 10% 作为保证金。 | QuantBase v1 策略自动信号当前默认用这个模式，`amount="100"` 表示 100% 可用保证金，再由 QuantBase 通道保证金上限、Bot 资金和 OKX 侧风控共同约束。 |
| `percentage_investment` | 创建 Bot 时设定的总投资额百分比。 | Bot 总投资额为 1000 USDT 且 `amount="10"` 时，每次约使用 100 USDT 作为保证金。 | 适合希望按 Bot 初始/总投资额度固定比例下单的场景。 |
| `contract` | 合约张数。 | `amount="5"` 表示固定买入或卖出 5 张合约。 | 适合熟悉 OKX 合约面值、最小张数和杠杆规则的场景。 |

平仓信号（`EXIT_LONG` / `EXIT_SHORT`）在 QuantBase v1 中使用 `percentage_position`，`amount` 表示要平掉的当前持仓比例，例如 `amount="100"` 表示全平。

## QuantBase 映射规则

| QuantBase 纸面成交 | OKX action | investmentType | amount |
| --- | --- | --- | --- |
| `open_contract(long)` | `ENTER_LONG` | `percentage_balance` | `100` |
| `open_contract(short)` | `ENTER_SHORT` | `percentage_balance` | `100` |
| `close_contract(long)` | `EXIT_LONG` | `percentage_position` | `close_ratio * 100` |
| `close_contract(short)` | `EXIT_SHORT` | `percentage_position` | `close_ratio * 100` |

## QuantBase 通道默认值

- `最大保证金`：默认 `10` USDT。这是 QuantBase 审批前的内部风控门槛，不是 OKX JSON 字段。
- `消息最大延迟`：默认 `30` 秒，会写入 JSON 的 `maxLag`。
- `orderType`：固定 `market`。
- `orderPriceOffset`：固定空字符串。
- `止损 / 止盈`：v1 只作为 QuantBase 内部审计信息，不写入 OKX Signal Bot JSON。

## 通道测试 payload

通道卡片的“测试”按钮会先打开确认弹框。默认的真实发送测试不是策略信号，而是一个极小的 OKX Signal Bot 连通性测试：

```json
{
  "action": "ENTER_LONG",
  "instrument": "DOGE-USDT-SWAP",
  "signalToken": "<OKX_SIGNAL_TOKEN>",
  "timestamp": "2026-05-08T06:20:00Z",
  "maxLag": "30",
  "orderType": "market",
  "orderPriceOffset": "",
  "investmentType": "margin",
  "amount": "0.1"
}
```

`investmentType=margin` 表示按报价币种保证金金额下单，`amount=0.1` 表示 0.1 USDT 保证金。弹框里的“生成测试 payload”只返回脱敏 payload，不发送；“真实发送测试”会把真实 token 推到已启用通道的 OKX Signal Bot webhook。OKX 仍可能因为最小下单额、Bot 配置、账户资金或交易权限拒绝成交。

## 不使用 TradingView 占位符

OKX 文档示例里常见：

```json
{
  "instrument": "{{ticker}}",
  "timestamp": "{{timenow}}"
}
```

QuantBase 不是从 TradingView 警报触发，所以最终发送时必须替换为真实值：

- `instrument` 用 OKX `instId`，例如 `BTC-USDT-SWAP`。
- `timestamp` 用 QuantBase 发送时的 UTC 时间。
- `signalToken` 用通道配置里的真实 token。
