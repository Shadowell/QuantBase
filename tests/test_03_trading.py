"""
模块3: 交易API测试（只读接口，不实际下单）
"""
import pytest


class TestBalance:
    """账户余额"""

    @pytest.mark.trading
    def test_get_balance(self, client):
        """获取OKX总余额"""
        r = client.get("/trading/balance", params={"exchange": "okx"})
        assert r.status_code == 200
        data = r.json()
        assert "balance" in data, "缺少 balance 字段"
        balances = data["balance"]
        assert isinstance(balances, list), "balance 应为数组"
        # 至少有一种资产
        assert len(balances) > 0, "余额为空，可能API Key未配置"

    @pytest.mark.trading
    def test_balance_structure(self, client):
        """余额数据结构正确"""
        r = client.get("/trading/balance", params={"exchange": "okx"})
        data = r.json()
        balances = data["balance"]
        if len(balances) > 0:
            b = balances[0]
            assert "currency" in b, "缺少 currency"
            assert "free" in b, "缺少 free"
            assert "total" in b, "缺少 total"

    @pytest.mark.trading
    def test_balance_detail(self, client):
        """获取分账户余额（trading/funding）"""
        r = client.get("/trading/balance/detail", params={"exchange": "okx"})
        assert r.status_code == 200
        data = r.json()
        assert "trading" in data, "缺少 trading 账户"
        assert "funding" in data, "缺少 funding 账户"
        assert isinstance(data["trading"], list)
        assert isinstance(data["funding"], list)


class TestPositions:
    """持仓查询"""

    @pytest.mark.trading
    def test_get_positions(self, client):
        """获取持仓列表"""
        r = client.get("/trading/positions", params={"exchange": "okx"})
        assert r.status_code == 200
        data = r.json()
        assert "positions" in data
        assert isinstance(data["positions"], list)


class TestOpenOrders:
    """挂单查询"""

    @pytest.mark.trading
    def test_get_open_orders(self, client):
        """获取当前挂单"""
        r = client.get("/trading/orders/open", params={"exchange": "okx"})
        assert r.status_code == 200
        data = r.json()
        assert "orders" in data
        assert isinstance(data["orders"], list)
