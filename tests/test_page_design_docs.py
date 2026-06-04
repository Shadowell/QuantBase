from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


PAGES = [
    ("首页", "/", "docs/pages/home.md"),
    ("行情", "/market", "docs/pages/market.md"),
    ("策略", "/strategy", "docs/pages/strategy.md"),
    ("回测", "/backtest", "docs/pages/backtest.md"),
    ("链上", "/onchain", "docs/pages/onchain.md"),
    ("模拟盘", "/live", "docs/pages/simulation.md"),
    ("实盘", "/live-real", "docs/pages/live-real.md"),
    ("盯盘", "/watch", "docs/pages/watch.md"),
    ("信号中心", "/signals", "docs/pages/signals.md"),
    ("监控", "/monitor", "docs/pages/monitor.md"),
    ("数据中心", "/data", "docs/pages/data.md"),
    ("AI研发", "/ai-lab", "docs/pages/ai-lab.md"),
]


def test_every_first_level_page_has_design_doc():
    page_index = (ROOT / "docs/pages/README.md").read_text()

    for label, route, doc_path in PAGES:
        doc = ROOT / doc_path

        assert doc.exists(), f"missing page design doc for {label}: {doc_path}"
        assert doc.name in page_index
        assert route in page_index


def test_page_docs_define_required_contract_sections():
    required_sections = [
        "## Route",
        "## Purpose",
        "## First-Screen Layout",
        "## Data Sources",
        "## Interactions",
        "## Empty/Error States",
        "## Screenshot Contract",
    ]

    for _, _, doc_path in PAGES:
        text = (ROOT / doc_path).read_text()
        for section in required_sections:
            assert section in text, f"{doc_path} missing {section}"
