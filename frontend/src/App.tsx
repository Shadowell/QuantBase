import { lazy } from 'react'
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import MainLayout from './components/MainLayout'

const Home = lazy(() => import('./pages/Home'))
const Market = lazy(() => import('./pages/Market'))
const Strategy = lazy(() => import('./pages/Strategy'))
const Backtest = lazy(() => import('./pages/Backtest'))
const ArbitrageCenter = lazy(() => import('./pages/ArbitrageCenter'))
const OnchainResearch = lazy(() => import('./pages/OnchainResearch'))
const Monitor = lazy(() => import('./pages/Monitor'))
const LiveTrading = lazy(() => import('./pages/liveTrading'))
const SignalCenter = lazy(() => import('./pages/SignalCenter'))
const WatchMarket = lazy(() => import('./pages/WatchMarket'))
const DataManager = lazy(() => import('./pages/DataManager'))
const AILab = lazy(() => import('./pages/AILab'))

function AppRoutes() {
  return (
    <Routes>
      <Route path="/" element={<MainLayout />}>
        <Route index element={<Home />} />
        <Route path="market" element={<Market />} />
        <Route path="trading" element={<Navigate to="/" replace />} />
        <Route path="strategy" element={<Strategy />} />
        <Route path="backtest" element={<Backtest />} />
        <Route path="arbitrage" element={<ArbitrageCenter />} />
        <Route path="onchain" element={<OnchainResearch />} />
        <Route path="live" element={<LiveTrading modeScope="paper" />} />
        <Route path="live-real" element={<LiveTrading modeScope="live" />} />
        <Route path="signals" element={<SignalCenter />} />
        <Route path="watch" element={<WatchMarket />} />
        <Route path="monitor" element={<Monitor />} />
        <Route path="data" element={<DataManager />} />
        <Route path="ai-lab" element={<AILab />} />
      </Route>
    </Routes>
  )
}

function App() {
  return (
    <BrowserRouter>
      {/* Suspense 放在 MainLayout 内包裹 Outlet，避免懒加载时整页（含侧栏）被 fallback 顶替 */}
      <AppRoutes />
    </BrowserRouter>
  )
}

export default App
