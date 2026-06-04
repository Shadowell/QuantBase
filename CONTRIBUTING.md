# Contributing to QuantBase

感谢你帮助改进 QuantBase。

## 基本原则

- 保持 paper/simulation first。
- 不提交真实 API key、交易所密钥、webhook URL、服务器地址、账户截图、生产数据库、日志或私有策略研究记录。
- 不在代码、文档、注释、截图或示例中写收益承诺、投资建议或跟单暗示。
- 公开示例优先使用 demo 策略。

## 本地开发

安装依赖后运行项目检查：

```bash
./scripts/check.sh
```

行为风险较高的改动应增加聚焦测试，并直接运行相关检查：

```bash
pytest -q tests/path_to_test.py
npm --prefix frontend run build
```

## Pull Request

请说明：

- 改了什么；
- 为什么要改；
- 如何验证；
- 有哪些已知限制或跳过的检查。

页面工作流变化应同步更新 `docs/pages/*.md`。长期产品行为变化应同步更新 `docs/spec.md`。
