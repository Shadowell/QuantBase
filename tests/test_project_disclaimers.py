from pathlib import Path
import os
import re


ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_docs_markdown_filenames_use_consistent_kebab_case():
    allowed = {"README.md"}
    pattern = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*\.md$")
    invalid = [
        str(path.relative_to(ROOT))
        for path in (ROOT / "docs").rglob("*.md")
        if path.name not in allowed and not pattern.fullmatch(path.name)
    ]

    assert invalid == []


def test_readme_links_project_disclaimer_and_highlights_core_risks():
    text = _read("README.md")

    assert "[English](README_EN.md) | 简体中文" in text
    assert "## 项目定位" in text
    assert "## 核心能力" in text
    assert "## 示例策略" in text
    assert "docs/disclaimer.md" in text
    assert "docs/open-source-scope.md" in text
    assert "不构成投资建议" in text
    assert "收益承诺" in text
    assert "paper/simulation" in text
    assert "10 个社区版 demo seed" in text
    assert "/trading" not in text


def test_english_readme_exists_and_preserves_research_boundary():
    text = _read("README_EN.md")

    assert "English | [简体中文](README.md)" in text
    assert "# QuantBase" in text
    assert "## What It Is" in text
    assert "## Core Capabilities" in text
    assert "10 demo seeds" in text
    assert "not investment advice" in text.lower()
    assert "docs/disclaimer.md" in text
    assert "docs/open-source-scope.md" in text
    assert "/trading" not in text


def test_open_source_scope_records_public_boundary():
    text = _read("docs/open-source-scope.md")

    assert "公开社区版" in text
    assert "10 个 demo seed" in text
    assert "私有 `.env` 文件和生产密钥" in text
    assert "生产部署 workflow" in text
    assert "docs/local-deployment.md" in text
    assert "QUANTBASE_LIVE_TRADING_ENABLED=1" in text
    assert "不把 demo 策略描述为可盈利系统" in text


def test_spec_records_open_source_boundaries():
    text = _read("docs/spec.md")

    assert "## 默认边界" in text
    assert "paper/simulation" in text
    assert "Demo seed" in text
    assert "不提供投资建议、跟单、托管账户或收益承诺" in text
    assert "不内置生产服务器部署配置" in text


def test_strategy_development_guide_keeps_live_execution_opt_in():
    text = _read("docs/strategy-development-guide.md")

    assert "社区版默认关闭" in text
    assert "QUANTBASE_LIVE_TRADING_ENABLED=1" in text
    assert "QUANTBASE_MCP_ENABLE_LIVE_TRADING=1" in text


def test_central_disclaimer_document_covers_required_topics():
    text = _read("docs/disclaimer.md")

    required = [
        "主要用于策略研究",
        "不构成投资建议",
        "不构成法律、税务、会计或合规建议",
        "模拟盘和回测结果不代表未来结果",
        "杠杆、衍生工具、做空、强制平仓和流动性不足可能导致快速亏损",
        "AI、模型预测、回测和历史表现不保证未来收益",
        "外部平台、API、网络、数据源、Webhook、OKX Signal Bot 和第三方服务可能中断、延迟、重复处理或返回错误数据",
        "使用者需自行确认当地法律法规、平台规则、税务申报、数据授权、凭证安全和资金来源合规",
    ]
    for phrase in required:
        assert phrase in text


def test_historical_and_investment_guides_are_removed_from_public_docs():
    current_docs_index = _read("docs/README.md")

    removed_paths = [
        "docs/archive/Crypto_Quant_Guide.md",
        "docs/archive/Quant_Strategies_Guide.md",
        "docs/archive/Strategy_Research_Notes.md",
        "docs/archive/Technical_Reference.md",
        "docs/archive/美股投资完全指南.md",
        "docs/archive/期权入门指南.md",
        "docs/archive/全球期权市场全景指南.md",
        "docs/archive/Crypto_Beginner_Guide.md",
        "docs/archive/Top50_Cryptocurrencies.md",
        "docs/archive/全球交割日完全指南.md",
        "docs/archive/Exchange_Fees_API_Analysis.md",
        "docs/archive/README.md",
        "docs/美股投资完全指南.md",
    ]
    for path in removed_paths:
        assert not (ROOT / path).exists(), f"unrelated public document should be removed: {path}"

    assert "docs/archive/" not in current_docs_index
    assert "docs/Strategy_Research_Notes.md" not in current_docs_index
    assert "docs/Technical_Reference.md" not in current_docs_index
    assert "docs/Quant_Strategies_Guide.md" not in current_docs_index


def test_ci_installs_dev_requirements_and_check_runs_public_pytest_subset():
    workflow = _read(".github/workflows/check.yml")
    check_script = _read("scripts/check.sh")
    dev_requirements = _read("backend/requirements-dev.txt")

    assert "backend/requirements-dev.txt" in workflow
    assert "-m pytest" in check_script
    assert "tests/test_project_disclaimers.py" in check_script
    assert "tests/test_auth_frontend_static.py" in check_script
    assert "tests/test_ai_lab_orbit_auto_post_static.py" in check_script
    assert "pytest" in dev_requirements
    assert "pytest-asyncio" in dev_requirements


def test_restart_script_exists_when_local_scripts_reference_it():
    init_script = _read("init.sh")
    start_script = _read("start.sh")
    restart_script = ROOT / "restart.sh"

    assert "./restart.sh" in init_script
    assert "./restart.sh" in start_script
    assert restart_script.exists()
    assert os.access(restart_script, os.X_OK)


def test_env_example_documents_optional_integrations_and_safe_defaults():
    env_example = _read("backend/.env.example")
    spec = _read("docs/spec.md")
    local_deployment = _read("docs/local-deployment.md")

    required_env_vars = [
        "OKX_API_KEY=",
        "OKX_API_SECRET=",
        "OKX_PASSPHRASE=",
        "OKX_TESTNET=true",
        "BINANCE_API_KEY=",
        "BINANCE_API_SECRET=",
        "BINANCE_TESTNET=false",
        "HERMES_AGENT_ENABLED=false",
        "HERMES_AGENT_COMMAND=hermes",
        "HERMES_AGENT_TIMEOUT=240",
        "QUANTBASE_AUTH_ENABLED=false",
        "QUANTBASE_ADMIN_USERNAME=",
        "QUANTBASE_ADMIN_PASSWORD_HASH=",
        "QUANTBASE_AUTH_COOKIE_SECURE=false",
        "QUANTBASE_LIVE_TRADING_ENABLED=false",
        "QUANTBASE_MCP_ENABLE_LIVE_TRADING=0",
    ]
    for env_var in required_env_vars:
        assert env_var in env_example

    assert "实盘执行默认关闭" in spec
    assert "真实账户读取和实盘执行默认关闭" in spec
    assert "QUANTBASE_LIVE_TRADING_ENABLED=1" in spec
    assert "QUANTBASE_MCP_ENABLE_LIVE_TRADING=1" in spec
    assert "Frontend community build has no login page" in local_deployment
    assert "Real-account reads and live execution are disabled" in local_deployment


def test_generated_test_report_is_not_committed():
    gitignore = _read(".gitignore")

    assert not (ROOT / "tests/test_report.txt").exists()
    assert "tests/test_report.txt" in gitignore


def test_open_source_community_files_exist_and_preserve_boundaries():
    required_paths = [
        "CONTRIBUTING.md",
        "CODE_OF_CONDUCT.md",
        "SECURITY.md",
        ".github/pull_request_template.md",
        ".github/ISSUE_TEMPLATE/bug_report.md",
        ".github/ISSUE_TEMPLATE/config.yml",
        "docs/local-deployment.md",
        "docs/open-source-release-checklist.md",
    ]
    for path in required_paths:
        assert (ROOT / path).exists(), f"missing community file: {path}"

    combined = "\n".join(_read(path) for path in required_paths if path.endswith(".md"))
    assert "paper/simulation" in combined
    assert "not investment advice" in combined.lower()
    assert "API keys" in combined
    assert "production" in combined.lower()
