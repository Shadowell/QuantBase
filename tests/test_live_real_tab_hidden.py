from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_live_real_sidebar_entry_is_visible_after_paper_and_signal_nav_is_hidden():
    main_layout = (ROOT / "frontend" / "src" / "components" / "MainLayout.tsx").read_text(encoding="utf-8")
    app = (ROOT / "frontend" / "src" / "App.tsx").read_text(encoding="utf-8")

    assert "{ path: '/live-real', icon: Rocket, label: '实盘' }" in main_layout
    assert "{ path: '/signals', icon: Send, label: '信号' }" not in main_layout
    assert main_layout.index("{ path: '/live', icon: Activity, label: '模拟' }") < main_layout.index(
        "{ path: '/live-real', icon: Rocket, label: '实盘' }"
    ) < main_layout.index("{ path: '/data', icon: Database, label: '数据' }")
    assert 'Route path="live-real"' in app
    assert 'modeScope="live"' in app


def test_paper_sidebar_icon_differs_from_backtest_icon():
    main_layout = (ROOT / "frontend" / "src" / "components" / "MainLayout.tsx").read_text(encoding="utf-8")

    assert "{ path: '/backtest', icon: FlaskConical, label: '回测' }" in main_layout
    assert "{ path: '/live', icon: Activity, label: '模拟' }" in main_layout
    assert "{ path: '/live', icon: FlaskConical, label: '模拟' }" not in main_layout
