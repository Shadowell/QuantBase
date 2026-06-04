from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_live_real_route_is_kept_but_sidebar_entry_is_hidden():
    main_layout = (ROOT / "frontend" / "src" / "components" / "MainLayout.tsx").read_text(encoding="utf-8")
    app = (ROOT / "frontend" / "src" / "App.tsx").read_text(encoding="utf-8")

    assert "{ path: '/live-real', icon: Rocket, label: '实盘' }" not in main_layout
    assert "{ path: '/watch', icon: ScanLine, label: '盯盘' }" not in main_layout
    assert "{ path: '/signals', icon: Send, label: '信号' }" not in main_layout
    assert main_layout.index("{ path: '/live', icon: Activity, label: '模拟' }") < main_layout.index(
        "{ path: '/monitor', icon: Eye, label: '监控' }"
    )
    assert 'Route path="live-real"' in app
    assert 'Route path="watch"' in app
    assert 'modeScope="live"' in app


def test_paper_sidebar_icon_differs_from_backtest_icon():
    main_layout = (ROOT / "frontend" / "src" / "components" / "MainLayout.tsx").read_text(encoding="utf-8")

    assert "{ path: '/backtest', icon: FlaskConical, label: '回测' }" in main_layout
    assert "{ path: '/live', icon: Activity, label: '模拟' }" in main_layout
    assert "{ path: '/live', icon: FlaskConical, label: '模拟' }" not in main_layout
