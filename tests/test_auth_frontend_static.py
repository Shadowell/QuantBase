from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_frontend_uses_auth_gate_before_rendering_main_layout() -> None:
    app = read("frontend/src/App.tsx")
    auth_provider = ROOT / "frontend/src/auth/AuthProvider.tsx"
    login_page = ROOT / "frontend/src/pages/Login.tsx"

    assert auth_provider.exists()
    assert login_page.exists()
    assert "AuthProvider" in app
    assert "Login" in app
    assert "MainLayout" in app
    assert "authEnabled" in auth_provider.read_text(encoding="utf-8")
    assert "访客邀请码" in login_page.read_text(encoding="utf-8")
    assert "管理员登录" in login_page.read_text(encoding="utf-8")


def test_admin_login_prefills_default_username() -> None:
    login_page = read("frontend/src/pages/Login.tsx")

    assert "DEFAULT_ADMIN_USERNAME = 'Shadowell'" in login_page
    assert "useState(DEFAULT_ADMIN_USERNAME)" in login_page


def test_guest_invite_hash_link_auto_logs_in_and_cleans_url() -> None:
    login_page = read("frontend/src/pages/Login.tsx")

    assert "AUTO_GUEST_INVITE_PARAM_NAMES = ['invite', 'guest_code']" in login_page
    assert "window.location.hash" in login_page
    assert "new URLSearchParams(hash.slice(1))" in login_page
    assert "await loginGuest(inviteCode)" in login_page
    assert "window.history.replaceState" in login_page
    assert "window.location.search" not in login_page


def test_guest_role_can_see_all_primary_navigation_and_settings_code_manager_exists() -> None:
    app = read("frontend/src/App.tsx")
    layout = read("frontend/src/components/MainLayout.tsx")
    client = read("frontend/src/api/client.ts")

    assert "useAuth" in layout
    assert "allowedRoles" in layout
    assert "RoleRoute" not in app
    assert "allowedRoles={['admin']}" not in app
    for path in ("/live-real", "/watch", "/monitor", "/data", "/ai-lab"):
        assert f"path: '{path}'," in layout
    assert layout.count("allowedRoles: ['admin', 'guest']") >= 10
    assert "访客邀请码管理" in layout
    assert "createGuestCode" in client
    assert "revokeGuestCode" in client
    assert "/auth/guest-codes" in client


def test_guest_mode_shows_limited_feature_notice() -> None:
    layout = read("frontend/src/components/MainLayout.tsx")

    assert "isGuest &&" in layout
    assert "访客模式：" in layout
    assert "部分页面功能不可用" in layout
    assert "仅支持查看和受限回测" in layout
    assert "实盘控制" in layout


def test_guest_live_real_page_is_read_only() -> None:
    live_center = read("frontend/src/pages/liveTrading/LiveExecutionCenter.tsx")

    assert "useAuth" in live_center
    assert "const readOnly = isGuest" in live_center
    assert "if (readOnly) return;" in live_center
    assert "!readOnly &&" in live_center
    assert "readOnly ? null : renderDeployPipeline()" in live_center


def test_guest_code_manager_does_not_render_revoked_rows_after_delete() -> None:
    layout = read("frontend/src/components/MainLayout.tsx")

    assert "setCodes((current) => current.filter((code) => code.id !== codeId));" in layout
    assert "已撤销" not in layout
    assert "disabled={revoked}" not in layout


def test_guest_code_form_labels_are_paired_with_inputs() -> None:
    layout = read("frontend/src/components/MainLayout.tsx")

    assert "grid grid-cols-1 items-start gap-3 lg:grid-cols-[minmax(180px,1.2fr)_repeat(4,minmax(90px,0.7fr))_auto]" in layout
    assert layout.count('className="flex min-w-0 flex-col gap-2"') >= 5
    assert layout.count('className="text-[10px] font-medium text-gray-600"') >= 5
    assert "mt-2 grid grid-cols-2 gap-2 text-[10px] text-gray-600 sm:grid-cols-5" not in layout
