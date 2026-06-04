# QuantBase 代码框架导览

## 推荐阅读顺序

如果目标是熟悉整个项目，而不是只修一个局部 bug，建议按下面顺序看：

1. `README.md`：了解产品定位、主要页面和运行边界。
2. `docs/README.md`：按产品、架构、页面、合同、研究、部署、QA 和截图找到文档入口。
3. `docs/spec.md`：了解当前产品合同、行为约束和长期规格。
4. `docs/architecture.md`：了解分层架构、核心数据流和部署边界。
5. `docs/open-source-scope.md`：了解社区版保留和剥离范围。
6. `docs/progress.md`：了解整理时间线、验证记录和遗留事项。

本仓库把文档当作系统状态的一部分。只要行为、接口、页面或运行方式发生变化，就应该同步更新对应规格、页面文档和进度记录。

## 系统整体形状

QuantBase 是一个全栈量化策略研究系统，核心闭环是：

```text
真实行情数据 -> 策略定义 -> Backtrader 回测 -> 模拟盘运行 -> 监控/信号审计 -> 实盘预检
```

最重要的边界是：研究和 paper 模拟盘是默认路径。外部 Signal Bot 投递和 OKX 直接实盘下单都必须经过显式启用、预检和操作者确认。

## 仓库主要入口

- 后端应用入口：`backend/app/main.py`
- API 路由：`backend/app/api/`
- API v2 路由：`backend/app/api/v2/`
- SQLite 持久化：`backend/app/db/local_db.py`
- 策略运行时：`backend/app/services/strategy_engine.py`
- 回测运行时：`backend/app/services/backtrader_engine.py`
- 策略注册表：`backend/app/services/strategy_registry.py`
- 策略实现：`backend/app/strategies/`
- 前端路由入口：`frontend/src/App.tsx`
- 前端 API 客户端：`frontend/src/api/client.ts`
- 前端页面：`frontend/src/pages/`
- 内置策略 seed：`data/seed/strategies.json`
- 本地验证入口：`scripts/check.sh`
- 通用 CI workflow：`.github/workflows/check.yml`

## 后端启动流程

`backend/app/main.py` 负责应用启动和关闭。`lifespan` 函数按固定顺序完成持久化启动：

1. 先加载 `.env`，确保后续模块能读到配置。
2. 通过 `db.init_db()` 初始化 SQLite 表和轻量迁移。
3. 当 `QUANTBASE_AUTH_ENABLED=1` 时校验登录配置。
4. 把上次进程异常退出后无法继续的内存任务标记为 interrupted。
5. 初始化交易所连接。
6. 恢复可持久化的数据同步任务。
7. 启动 WebSocket 实时服务、策略引擎、告警服务和调度器。
8. 关闭时依次停止调度器、告警服务、策略引擎和实时服务。

这个顺序很重要，因为很多服务都依赖数据库表和交易所客户端已经就绪。

## API 层

后端同时保留 legacy API 和 v2 API。当前产品开发通常优先使用 v2：

- v2 路由组合在 `backend/app/api/v2/` 下。
- endpoint 模块负责请求解析和响应形状。
- 共享业务规则应放到 domain 或 service 模块，避免散落在页面专属 endpoint 里。
- 大多数 v2 响应使用 QuantBase envelope，前端在 `frontend/src/api/client.ts` 中统一 unwrap。

新增或修改 API 行为时，要同步更新相关合同和依赖该接口的页面文档。

## 持久化模型

`backend/app/db/local_db.py` 是 SQLite 操作总入口，目前承担这些职责：

- 通过 `init_db()` 和 `_ensure_*` helper 创建 schema、补齐旧库缺失字段。
- 兼容读写旧 SQLite K 线表。
- 持久化策略元数据、paper 成交、权益采样、回测任务/结果、登录会话、信号、实盘设置、AI 任务、优化器运行和应用配置。

K 线数据有两个真实数据来源：

- 文件 K 线 store：`backend/app/services/kline_file_store.py`，是 Data Manager 持久同步的主路径。
- 旧 SQLite 分表：例如 `kline_1h`，作为历史兼容 fallback 保留。

回测可以读取任一真实数据源，但不能伪造 K 线。覆盖不足时应该明确失败，或提示先同步历史数据。

## 策略框架

策略统一继承 `backend/app/core/execution/base_strategy.py` 里的 `BaseStrategy`。这个合同刻意保持较小：

- 策略 state 保存身份、交易所、交易对、持仓和运行期元数据。
- 策略通过 `on_init`、`on_start`、`on_bar`、`on_stop` 响应生命周期。
- 策略通过 broker 协议下单，而不是直接调用 Backtrader、paper broker 或 live order 代码。

`backend/app/services/strategy_registry.py` 负责把保存的策略配置和 `strategy_key` 映射到具体 Python 策略类。`data/seed/strategies.json` 则让内置策略以数据库行的形式导入。

## 回测流程

当前回测主路径是 `backend/app/services/backtrader_engine.py`。

它的关键设计是适配器桥接：

- `BacktraderBroker` 实现 `BaseStrategy` 期望的 broker 协议。
- `BTStrategyAdapter` 继承 Backtrader 的 `bt.Strategy`，把每根 Backtrader bar 转成 QuantBase 的 `BarData`。
- `BacktestEngine` 负责加载真实 K 线、配置 `Cerebro`、运行策略、叠加可选资金费现金流，并生成标准化 `BacktestReport`。

因此同一份策略代码可以同时复用于回测和模拟盘运行。策略作者不需要关心 bar 来自 Backtrader 还是异步策略运行时。

## 风控流程

`backend/app/services/risk_manager.py` 是通用账户级风控工具：

- `RiskConfig` 保存仓位上限、亏损上限、止损止盈、交易频率、市场过滤和杠杆上限。
- `RiskManager.check_order()` 是下单前入口，会依次检查熔断、最小/最大订单金额、单仓占比、总敞口、日亏损、总回撤、交易频率、冷却期、波动率、止损止盈和单笔风险金额。
- `RiskManager.update_position()` 维护追踪止损、保本止损、未实现盈亏和退出判断。
- 熔断状态可以通过 `get_circuit_breaker_snapshot()` 明确查看。

可以把这个模块理解为通用安全层。策略自己的限制可以更保守，但不应该绕过账户级硬限制。

## 前端结构

`frontend/src/App.tsx` 声明一级页面路由：

- `/`
- `/market`
- `/strategy`
- `/backtest`
- `/arbitrage`
- `/onchain`
- `/live`
- `/live-real`
- `/signals`
- `/watch`
- `/monitor`
- `/data`
- `/ai-lab`

页面模块使用 lazy loading。主布局保留在页面路由外层，避免懒加载或 Vite chunk 替换时整页布局被 fallback 顶掉。社区版前端不提供登录页面或访客只读 UI；如需访问控制，请在二次开发或部署层接入。

`frontend/src/api/client.ts` 统一管理 REST 行为：

- Axios base URL 是 `/api/v2`。
- 请求携带 credentials，以兼容二次开发中的服务端 session cookie。
- API envelope 在客户端边界统一 unwrap。
- snake_case 和 camelCase 在客户端边界互转。
- 长时间的数据同步和同步式回测使用比普通 REST 更长的 timeout。

## 文档维护

长期产品边界写在 `docs/spec.md`，开源社区版范围写在 `docs/open-source-scope.md`。当一级页面发生变化时，应同步更新 `docs/pages/`。页面文档需要说明页面目的、布局、数据来源、交互、空态/错误态和截图要求。

## 部署与运行

本地验证优先从下面入口开始：

```bash
./scripts/check.sh
```

`./init.sh` 会安装依赖、初始化 SQLite，并导入 `data/seed/strategies.json` 中的 10 个公开 demo 策略。`./start.sh`、`./stop.sh`、`./status.sh` 面向本地演示和开发；详细运行手册见 `docs/local-deployment.md`。生产部署、远程数据库同步、域名、反向代理和密钥注入由使用者自行配置。

## 安全扩展方式

- 新增策略行为时，先明确 paper/simulation 边界，再更新 seed、registry、测试和规格文档。
- 修改页面时，同步更新 `docs/pages/` 对应页面文档，以及 README/spec 中相关说明。
- 修改持久化字段时，在 `local_db.py` 增加 `_ensure_*` 迁移，保证旧生产库可读。
- 修改回测逻辑时，保持真实数据原则：不生成合成 K 线，不做静默覆盖 fallback。
- 修改 live 或 signal 行为时，把它视为高风险路径，保留显式操作者确认。
