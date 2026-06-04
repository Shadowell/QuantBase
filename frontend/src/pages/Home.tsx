import { LayoutDashboard } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import MarketUniversePanel from '../components/MarketUniversePanel';
import { useStore } from '../stores/useStore';

export default function Home() {
  const { selectedExchange } = useStore();
  const navigate = useNavigate();

  const handleSelectSymbol = (symbol: string) => {
    useStore.getState().setSelectedSymbol(symbol);
    navigate('/market');
  };

  return (
    <div className="h-full overflow-y-auto">
      <div className="px-6 pt-5 pb-3">
        <div className="flex items-center gap-2">
          <LayoutDashboard className="h-5 w-5 text-blue-400" />
          <h1 className="text-xl font-bold text-white">市场大盘</h1>
        </div>
        <p className="mt-2 max-w-3xl text-sm text-gray-500">
          聚合 OKX 公开行情的大盘广度、成交活跃度和强弱排行；点击榜单标的后进入行情页查看 K 线详情。
        </p>
      </div>

      <div className="px-6 pb-6">
        <MarketUniversePanel
          variant="summary"
          selectedExchange={selectedExchange}
          onSelectSymbol={handleSelectSymbol}
        />
      </div>
    </div>
  );
}
