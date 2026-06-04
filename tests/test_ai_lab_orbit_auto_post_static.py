from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AI_LAB = ROOT / "frontend" / "src" / "pages" / "AILab.tsx"
PUBLISHER = ROOT / "scripts" / "okx_orbit_publisher.js"
SERVICE = ROOT / "backend" / "app" / "services" / "orbit_auto_post_service.py"


def test_ai_lab_exposes_orbit_auto_post_tab_and_api_contract():
    page = AI_LAB.read_text(encoding="utf-8")

    assert "orbit-post" in page
    assert "星球发帖" in page
    assert "研究复盘" in page
    assert "分享阈值" in page
    assert "单账号自动发帖" not in page
    assert "真实合约单星球发布台" not in page
    assert "收益超过" not in page
    assert "吸引跟单" not in page
    assert "minMarginRoiPct" in page
    assert "unwrapApiData" in page
    assert "orbit-auto-post/config" in page
    assert "orbit-auto-post/candidates" in page
    assert "orbit-auto-post/run-now" in page
    assert "orbit-auto-post/publish" in page


def test_orbit_publisher_forces_direct_connection_by_default():
    script = PUBLISHER.read_text(encoding="utf-8")

    assert "clearProxyEnvironment" in script
    assert "QUANTBASE_ORBIT_USE_SYSTEM_PROXY" in script
    assert "--no-proxy-server" in script
    assert "--proxy-server=direct://" in script


def test_orbit_auto_post_copy_is_research_review_not_follow_trading():
    service = SERVICE.read_text(encoding="utf-8")

    assert "跟单" not in service
    assert "研究复盘" in service
    assert "不是投资建议" in service
