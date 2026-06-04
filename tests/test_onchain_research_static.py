from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read_text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_onchain_research_route_navigation_api_and_sections() -> None:
    app = read_text("frontend/src/App.tsx")
    layout = read_text("frontend/src/components/MainLayout.tsx")
    page = read_text("frontend/src/pages/OnchainResearch.tsx")
    client = read_text("frontend/src/api/client.ts")
    api = read_text("backend/app/api/v2/api.py")

    assert "const OnchainResearch = lazy(() => import('./pages/OnchainResearch'))" in app
    assert '<Route path="onchain" element={<OnchainResearch />} />' in app
    assert "{ path: '/onchain', icon: Network, label: '链上'" in layout
    assert "api_router_v2.include_router(onchain.router, prefix=\"/onchain\"" in api
    assert "export const onchainApi" in client
    assert "getSummary:" in client
    assert "getReq('/onchain/summary')" in client

    required_labels = [
        "链上数据",
        "综合总览",
        "协议研究",
        "收益机会",
        "DeFiLlama",
        "总锁仓量",
        "稳定币供给",
        "24H 协议费用",
        "稳定币收益池",
        "最大公链",
        "最大协议",
        "链锁仓量",
        "协议锁仓量",
        "协议费用排行",
        "DeFiLlama 数据源状态",
        "刷新",
    ]
    for label in required_labels:
        assert label in page

    old_english_labels = [
        "as of ",
        "Top Chain",
        "Top Protocol",
        "Fees 24H",
        "Yield Pools",
        "Chains TVL",
        "Stablecoin Yield",
        "DeFiLlama Source Status",
    ]
    for label in old_english_labels:
        assert label not in page


def test_onchain_nav_item_is_below_data_center() -> None:
    layout = read_text("frontend/src/components/MainLayout.tsx")

    data_index = layout.index("{ path: '/data', icon: Database, label: '数据'")
    onchain_index = layout.index("{ path: '/onchain', icon: Network, label: '链上'")
    ai_lab_index = layout.index("{ path: '/ai-lab', icon: Sparkles, label: 'AI研发'")

    assert data_index < onchain_index < ai_lab_index


def test_onchain_research_page_renders_real_summary_tables() -> None:
    page = read_text("frontend/src/pages/OnchainResearch.tsx")

    required_renderers = [
        "summary.chains.map",
        "summary.protocols.map",
        "summary.fees.map",
        "summary.stablecoins.map",
        "summary.yieldPools.map",
    ]
    for renderer in required_renderers:
        assert renderer in page

    assert "summary?.chains?.length ? <div />" not in page
    assert "summary?.protocols?.length ? <div />" not in page
    assert "summary?.yieldPools?.length ? <div />" not in page


def test_onchain_endpoint_uses_shared_response_contract() -> None:
    endpoint = read_text("backend/app/api/v2/endpoints/onchain.py")

    assert "from app.core.contracts import ok" in endpoint
    assert "from app.api.response import ok" not in endpoint


def test_onchain_research_has_no_mock_or_synthetic_data() -> None:
    page = read_text("frontend/src/pages/OnchainResearch.tsx")
    service = read_text("backend/app/domain/onchain/service.py")

    combined = page + "\n" + service
    forbidden = ["mock", "fake", "sample", "synthetic", "random"]
    for token in forbidden:
        assert token not in combined.lower()

    assert '"chains": []' in service
    assert '"protocols": []' in service
    assert '"fees": []' in service
    assert '"stablecoins": []' in service
    assert '"yield_pools": []' in service
