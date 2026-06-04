from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_frontend_renders_main_layout_without_login_gate() -> None:
    app = read("frontend/src/App.tsx")
    auth_provider = ROOT / "frontend/src/auth/AuthProvider.tsx"
    login_page = ROOT / "frontend/src/pages/Login.tsx"

    assert not auth_provider.exists()
    assert not login_page.exists()
    assert "MainLayout" in app
    assert "AuthProvider" not in app
    assert "import Login" not in app
    assert "return <Login />" not in app
    assert "authEnabled && !authenticated" not in app
    assert "正在检查登录态" not in app


def test_frontend_navigation_and_settings_are_not_role_gated() -> None:
    app = read("frontend/src/App.tsx")
    layout = read("frontend/src/components/MainLayout.tsx")
    client = read("frontend/src/api/client.ts")

    assert "useAuth" not in layout
    assert "allowedRoles" not in layout
    assert "authApi" not in client
    assert "/auth/" not in client
    assert "RoleRoute" not in app
    assert "allowedRoles={['admin']}" not in app
    for path in ("/monitor", "/data", "/ai-lab"):
        assert f"path: '{path}'," in layout
    for hidden_path in ("/live-real", "/watch"):
        assert f"path: '{hidden_path}'," not in layout
    assert "访客邀请码管理" not in layout
    assert "createGuestCode" not in client
    assert "revokeGuestCode" not in client
    assert "/auth/guest-codes" not in client


def test_frontend_no_longer_renders_guest_mode_notice() -> None:
    layout = read("frontend/src/components/MainLayout.tsx")

    assert "isGuest &&" not in layout
    assert "访客模式：" not in layout
    assert "部分页面功能不可用" not in layout
    assert "仅支持查看和受限回测" not in layout


def test_live_pages_are_not_guest_read_only_in_frontend() -> None:
    live_center = read("frontend/src/pages/liveTrading/LiveExecutionCenter.tsx")
    live_workspace = read("frontend/src/pages/liveTrading/index.tsx")
    strategy_page = read("frontend/src/pages/Strategy.tsx")

    for source in (live_center, live_workspace, strategy_page):
        assert "useAuth" not in source
        assert "isGuest" not in source
        assert "const readOnly = isGuest" not in source
        assert "const readOnly = false" not in source
    assert "readOnly ? null : renderDeployPipeline()" not in live_center


def test_frontend_guest_code_manager_removed() -> None:
    layout = read("frontend/src/components/MainLayout.tsx")

    assert "GuestCodeManager" not in layout
    assert "访客邀请码" not in layout
    assert "setCodes((current) => current.filter((code) => code.id !== codeId));" not in layout
