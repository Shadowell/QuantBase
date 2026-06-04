from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_readme_links_project_disclaimer_and_highlights_core_risks():
    text = _read("README.md")

    assert "[English](README_EN.md) | 简体中文" in text
    assert "## 项目定位" in text
    assert "## 核心能力" in text
    assert "## 示例策略" in text
    assert "docs/DISCLAIMER.md" in text
    assert "docs/OPEN_SOURCE_SCOPE.md" in text
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
    assert "docs/DISCLAIMER.md" in text
    assert "docs/OPEN_SOURCE_SCOPE.md" in text
    assert "/trading" not in text


def test_open_source_scope_records_public_boundary():
    text = _read("docs/OPEN_SOURCE_SCOPE.md")

    assert "公开社区版" in text
    assert "10 个 demo seed" in text
    assert "私有 `.env` 文件和生产密钥" in text
    assert "生产部署 workflow" in text
    assert "不把 demo 策略描述为可盈利系统" in text


def test_spec_records_open_source_boundaries():
    text = _read("docs/spec.md")

    assert "## 默认边界" in text
    assert "paper/simulation" in text
    assert "Demo seed" in text
    assert "不提供投资建议、跟单、托管账户或收益承诺" in text
    assert "不内置生产服务器部署配置" in text


def test_central_disclaimer_document_covers_required_topics():
    text = _read("docs/DISCLAIMER.md")

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
