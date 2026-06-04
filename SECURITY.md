# Security Policy

## 漏洞报告

请不要用公开 issue 披露密钥泄露、账户访问、可利用的真实下单路径或其他安全漏洞。

私下联系仓库维护者时，请尽量提供：

- 受影响的文件或接口；
- 复现步骤；
- 预期影响；
- 是否涉及凭证、webhook、账户或真实订单路径。

## 项目边界

QuantBase 默认面向 paper/simulation。任何使用交易所私有 API、真实下单、账户余额、账户持仓或通知 webhook 的路径，都必须显式配置，并默认关闭。

## 敏感数据

请勿提交：

- 带真实值的 `.env` 文件；
- API key、passphrase、private key、webhook URL 或 token；
- 生产 SQLite 数据库或 K 线缓存；
- 服务器主机名、SSH key、部署路径或 runner 标签；
- 包含真实账户余额、订单、持仓或策略 PnL 的截图。
