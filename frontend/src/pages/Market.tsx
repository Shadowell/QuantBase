import { useEffect, useState, useRef, useCallback, useMemo, lazy, Suspense } from 'react';
import { RefreshCw, Sparkles, TrendingUp } from 'lucide-react';
import clsx from 'clsx';
import { useStore } from '../stores/useStore';
import { marketApi } from '../api/client';
import {
  useTickerWebSocket,
  useKlineWebSocket,
  useOrderbookWebSocket,
  type RealtimeTicker,
} from '../hooks/useWebSocket';
import OrderBookChart from '../components/OrderBookChart';
import SymbolSearch from '../components/SymbolSearch';
import type { Kline, OrderBook } from '../types';

const KlineChart = lazy(() => import('../components/KlineChart'));

const TIMEFRAMES = ['1m', '5m', '15m', '1h', '4h', '1d'];

const REFRESH_INTERVALS: Record<string, number> = {
  '1m': 10_000,
  '5m': 15_000,
  '15m': 30_000,
  '1h': 60_000,
  '4h': 120_000,
  '1d': 300_000,
};

/** 对比 API 时间窗：按周期估算每根 K 线毫秒宽度 */
const TF_MS: Record<string, number> = {
  '1m': 60_000,
  '5m': 300_000,
  '15m': 900_000,
  '1h': 3_600_000,
  '4h': 14_400_000,
  '1d': 86_400_000,
};

/**
 * 在历史时间戳重合点上，比较真实收盘与「当时预测」的误差与涨跌方向。
 */
function computePredictionGapStats(klines: Kline[], historicalPred: Kline[]) {
  const predMap = new Map(historicalPred.map((p) => [p.timestamp, p]));
  let sumAbs = 0;
  let n = 0;
  let dirTotal = 0;
  let dirMatch = 0;

  for (let i = 1; i < klines.length; i++) {
    const bar = klines[i];
    const pred = predMap.get(bar.timestamp);
    if (!pred) continue;

    const prevClose = klines[i - 1].close;
    sumAbs += Math.abs(bar.close - pred.close);
    n += 1;

    const realDir = bar.close - prevClose;
    const predDir = pred.close - prevClose;
    if (realDir !== 0 && predDir !== 0) {
      dirTotal += 1;
      if (Math.sign(realDir) === Math.sign(predDir)) dirMatch += 1;
    }
  }

  return {
    mae: n ? sumAbs / n : 0,
    directionAccuracyPct: dirTotal ? (dirMatch / dirTotal) * 100 : 0,
    sampleCount: n,
  };
}

/** 1m：拉取最近 6h（360 根）；默认视口约 2h 实盘（120 根）+ 右侧预测，可左滑看更早 */
const KLINE_LIMIT_1M = 360;
const VISIBLE_1M_REAL_BARS = 120;
const MIN_KLINES_TO_RENDER = 20;
const MARKET_EMA_PERIODS = [5, 10, 20, 30];

type MarketType = 'swap' | 'spot';

type MarketDataCacheEntry = {
  klines?: Kline[];
  historicalPred?: Kline[];
  futurePred?: Kline[];
  marketIndicators?: Record<string, Array<number | null>>;
  marketIndicatorTimestamps?: number[];
  orderbook?: OrderBook | null;
  lastUpdateMs?: number;
};

function symbolBase(symbol: string): string {
  return String(symbol || '').split('/')[0].replace(/-USDT-SWAP$/i, '').toUpperCase();
}

function symbolForMarketType(symbol: string, marketType: MarketType): string {
  const base = symbolBase(symbol) || 'BTC';
  return marketType === 'swap' ? `${base}/USDT:USDT` : `${base}/USDT`;
}

function marketDataCacheKey(exchange: string, symbol: string, timeframe: string, predictionMode: boolean): string {
  return [exchange, symbol, timeframe, predictionMode ? 'prediction' : 'standard'].join('|');
}

export default function Market() {
  const { selectedExchange, selectedSymbol, setSelectedSymbol } = useStore();
  const [klines, setKlines] = useState<Kline[]>([]);
  const [historicalPred, setHistoricalPred] = useState<Kline[]>([]);
  const [futurePred, setFuturePred] = useState<Kline[]>([]);
  const [marketIndicators, setMarketIndicators] = useState<Record<string, Array<number | null>>>({});
  const [marketIndicatorTimestamps, setMarketIndicatorTimestamps] = useState<number[]>([]);
  const [orderbook, setOrderbook] = useState<OrderBook | null>(null);
  const [loading, setLoading] = useState(false);
  const [allSymbols, setAllSymbols] = useState<string[]>([]);
  const [marketType, setMarketType] = useState<MarketType>('swap');
  const [timeframe, setTimeframe] = useState('1m');
  const [showPrediction, setShowPrediction] = useState(false);
  const [predictLoading, setPredictLoading] = useState(false);

  const { ticker, isConnected } = useTickerWebSocket(selectedExchange, selectedSymbol);
  const { kline: wsKline } = useKlineWebSocket(selectedExchange, selectedSymbol, timeframe);
  const { orderbook: wsOrderbook } = useOrderbookWebSocket(selectedExchange, selectedSymbol);

  // 价格闪烁动画状态
  const prevPriceRef = useRef<number>(0);
  const [priceFlash, setPriceFlash] = useState<'up' | 'down' | null>(null);
  const flashTimerRef = useRef<ReturnType<typeof setTimeout>>();

  // 刷新按钮旋转动画（1s）
  const [refreshSpin, setRefreshSpin] = useState(false);
  const refreshTimerRef = useRef<ReturnType<typeof setTimeout>>();

  // 自动刷新相关
  const intervalRef = useRef<ReturnType<typeof setInterval>>();
  const [lastUpdate, setLastUpdate] = useState<Date | null>(null);
  const predictPollDebounceRef = useRef<ReturnType<typeof setTimeout>>();
  const marketDataRequestSeqRef = useRef(0);
  const marketLoadingRequestSeqRef = useRef(0);
  const marketDataCacheRef = useRef<Map<string, MarketDataCacheEntry>>(new Map());

  useEffect(() => {
    let cancelled = false;
    marketApi.getSymbols(selectedExchange, 'USDT', marketType)
      .then((res) => {
        if (cancelled) return;
        const symbols = res.symbols || [];
        const normalizedSymbol = symbolForMarketType(selectedSymbol, marketType);
        const fallbackSymbol = symbols.find((symbol) => symbolBase(symbol) === symbolBase(selectedSymbol)) || symbols[0];
        const nextSymbol = symbols.includes(normalizedSymbol)
          ? normalizedSymbol
          : (fallbackSymbol || normalizedSymbol);

        setAllSymbols(symbols);
        if (nextSymbol && nextSymbol !== selectedSymbol) {
          setSelectedSymbol(nextSymbol);
        }
      })
      .catch(console.error);
    return () => { cancelled = true; };
  }, [selectedExchange, selectedSymbol, setSelectedSymbol, marketType]);

  const applyMarketDataCacheEntry = useCallback(function applyMarketDataCacheEntry(entry?: MarketDataCacheEntry | null): boolean {
    if (!entry?.klines?.length) return false;
    setKlines(entry.klines);
    setHistoricalPred(entry.historicalPred || []);
    setFuturePred(entry.futurePred || []);
    setMarketIndicators(entry.marketIndicators || {});
    setMarketIndicatorTimestamps(entry.marketIndicatorTimestamps || []);
    setOrderbook(entry.orderbook || null);
    if (entry.lastUpdateMs) setLastUpdate(new Date(entry.lastUpdateMs));
    return true;
  }, []);

  const updateMarketDataCache = useCallback((cacheKey: string, patch: MarketDataCacheEntry) => {
    const next = {
      ...(marketDataCacheRef.current.get(cacheKey) || {}),
      ...patch,
    };
    marketDataCacheRef.current.set(cacheKey, next);
  }, []);

  const fetchData = useCallback((quiet = false) => {
    if (!selectedSymbol) return;

    const requestSeq = ++marketDataRequestSeqRef.current;
    const isStaleMarketDataRequest = () => requestSeq !== marketDataRequestSeqRef.current;
    const cacheKey = marketDataCacheKey(selectedExchange, selectedSymbol, timeframe, showPrediction);
    if (!quiet) {
      setLoading(true);
      marketLoadingRequestSeqRef.current = requestSeq;
    }

    const klineLimit = timeframe === '1m' ? KLINE_LIMIT_1M : 200;
    const compareBarWindow = timeframe === '1m' ? KLINE_LIMIT_1M : 200;

    if (showPrediction) {
      const step = TF_MS[timeframe] ?? 3_600_000;
      const endTime = Date.now();
      const startTime = endTime - compareBarWindow * step;
      const predictionRequest = marketApi.getPredictionsCompare(
        selectedExchange,
        selectedSymbol,
        timeframe,
        startTime,
        endTime,
        30
      );
      const orderbookRequest = marketApi.getOrderbook(selectedExchange, selectedSymbol, 20);

      predictionRequest
        .then((cmp) => {
          if (isStaleMarketDataRequest()) return;
          const compareKlines = (cmp.klines || []).slice(-klineLimit);
          const nextHistoricalPred = cmp.historicalPredictedBars || [];
          const nextFuturePred = cmp.futurePredictedBars || [];
          const lastUpdateMs = Date.now();
          setKlines(compareKlines);
          setHistoricalPred(nextHistoricalPred);
          setFuturePred(nextFuturePred);
          setLastUpdate(new Date(lastUpdateMs));
          updateMarketDataCache(cacheKey, {
            klines: compareKlines,
            historicalPred: nextHistoricalPred,
            futurePred: nextFuturePred,
            lastUpdateMs,
          });

          const indicatorStart = compareKlines[0]?.timestamp ?? startTime;
          const indicatorEnd = compareKlines[compareKlines.length - 1]?.timestamp ?? endTime;
          marketApi
            .getTechnicalIndicators(
              selectedExchange,
              selectedSymbol,
              timeframe,
              klineLimit,
              indicatorStart,
              indicatorEnd,
              MARKET_EMA_PERIODS
            )
            .then((indicatorsData) => {
              if (isStaleMarketDataRequest()) return;
              setMarketIndicators(indicatorsData.series || {});
              setMarketIndicatorTimestamps(indicatorsData.timestamps || []);
              updateMarketDataCache(cacheKey, {
                marketIndicators: indicatorsData.series || {},
                marketIndicatorTimestamps: indicatorsData.timestamps || [],
              });
            })
            .catch((error) => {
              if (!isStaleMarketDataRequest()) console.error(error);
            });
        })
        .catch((error) => {
          if (!isStaleMarketDataRequest()) console.error(error);
        })
        .finally(() => {
          if (!quiet && marketLoadingRequestSeqRef.current === requestSeq) setLoading(false);
        });

      orderbookRequest
        .then((orderbookData) => {
          if (isStaleMarketDataRequest()) return;
          setOrderbook(orderbookData);
          updateMarketDataCache(cacheKey, { orderbook: orderbookData });
        })
        .catch((error) => {
          if (!isStaleMarketDataRequest()) console.error(error);
        });
    } else {
      const klineRequest = marketApi.getKlines(selectedExchange, selectedSymbol, timeframe, klineLimit);
      const indicatorsRequest = marketApi.getTechnicalIndicators(
        selectedExchange,
        selectedSymbol,
        timeframe,
        klineLimit,
        undefined,
        undefined,
        MARKET_EMA_PERIODS
      );
      const orderbookRequest = marketApi.getOrderbook(selectedExchange, selectedSymbol, 20);

      klineRequest.then((klinesData) => {
        if (isStaleMarketDataRequest()) return;
        const lastUpdateMs = Date.now();
        setKlines(klinesData);
        setHistoricalPred([]);
        setFuturePred([]);
        setLastUpdate(new Date(lastUpdateMs));
        updateMarketDataCache(cacheKey, {
          klines: klinesData,
          historicalPred: [],
          futurePred: [],
          lastUpdateMs,
        });
      })
        .catch((error) => {
          if (!isStaleMarketDataRequest()) console.error(error);
        })
        .finally(() => {
          if (!quiet && marketLoadingRequestSeqRef.current === requestSeq) setLoading(false);
        });

      indicatorsRequest
        .then((indicatorsData) => {
          if (isStaleMarketDataRequest()) return;
          setMarketIndicators(indicatorsData.series || {});
          setMarketIndicatorTimestamps(indicatorsData.timestamps || []);
          updateMarketDataCache(cacheKey, {
            marketIndicators: indicatorsData.series || {},
            marketIndicatorTimestamps: indicatorsData.timestamps || [],
          });
        })
        .catch((error) => {
          if (!isStaleMarketDataRequest()) console.error(error);
        });

      orderbookRequest
        .then((orderbookData) => {
          if (isStaleMarketDataRequest()) return;
          setOrderbook(orderbookData);
          updateMarketDataCache(cacheKey, { orderbook: orderbookData });
        })
        .catch((error) => {
          if (!isStaleMarketDataRequest()) console.error(error);
        });
    }
  }, [selectedExchange, selectedSymbol, timeframe, showPrediction, updateMarketDataCache]);

  // 初始加载 & 参数变化时重新拉取
  useEffect(() => {
    if (!selectedSymbol) return;
    const cacheKey = marketDataCacheKey(selectedExchange, selectedSymbol, timeframe, showPrediction);
    const cachedEntry = marketDataCacheRef.current.get(cacheKey);
    if (!applyMarketDataCacheEntry(cachedEntry)) {
      setKlines([]);
      setHistoricalPred([]);
      setFuturePred([]);
      setMarketIndicators({});
      setMarketIndicatorTimestamps([]);
      setOrderbook(null);
    }
    fetchData(Boolean(cachedEntry?.klines?.length));
  }, [applyMarketDataCacheEntry, fetchData, selectedExchange, selectedSymbol, timeframe, showPrediction]);

  // K 线 / 订单簿自动轮询
  useEffect(() => {
    if (intervalRef.current) clearInterval(intervalRef.current);
    const base = REFRESH_INTERVALS[timeframe] || 15_000;
    const pollMs = showPrediction
      ? (isConnected ? Math.min(base, 12_000) : base)
      : (isConnected ? Math.max(base, 30_000) : base);
    intervalRef.current = setInterval(() => fetchData(true), pollMs);
    return () => { if (intervalRef.current) clearInterval(intervalRef.current); };
  }, [fetchData, timeframe, isConnected, showPrediction]);

  // 开启 AI 预测时：K 线 WS 推送后防抖拉取 compare，避免实盘已走而虚线未来段仍卡在旧 last_ts
  useEffect(() => {
    if (!showPrediction || !wsKline || !selectedSymbol) return;
    if (predictPollDebounceRef.current) clearTimeout(predictPollDebounceRef.current);
    predictPollDebounceRef.current = setTimeout(() => {
      fetchData(true);
    }, 900);
    return () => {
      if (predictPollDebounceRef.current) clearTimeout(predictPollDebounceRef.current);
    };
  }, [wsKline, showPrediction, selectedSymbol, fetchData]);

  // WebSocket kline 增量更新（主驱动）
  useEffect(() => {
    if (!wsKline || !selectedSymbol) return;
    const bar = wsKline as any;
    if (!bar.timestamp) return;
    setKlines((prev) => {
      if (prev.length === 0) return prev;
      const next = [...prev];
      const last = next[next.length - 1];
      const sameTs = last && Number(last.timestamp) === Number(bar.timestamp);
      if (sameTs) {
        const vol = Number(bar.volume ?? 0);
        const cl = Number(bar.close);
        const qv =
          bar.quote_volume != null && Number.isFinite(Number(bar.quote_volume))
            ? Number(bar.quote_volume)
            : cl * vol;
        next[next.length - 1] = {
          ...last,
          open: Number(bar.open),
          high: Number(bar.high),
          low: Number(bar.low),
          close: cl,
          volume: vol,
          quote_volume: qv,
          timestamp: Number(bar.timestamp),
        } as any;
      } else {
        const vol = Number(bar.volume ?? 0);
        const cl = Number(bar.close);
        const qv =
          bar.quote_volume != null && Number.isFinite(Number(bar.quote_volume))
            ? Number(bar.quote_volume)
            : cl * vol;
        next.push({
          open: Number(bar.open),
          high: Number(bar.high),
          low: Number(bar.low),
          close: cl,
          volume: vol,
          quote_volume: qv,
          timestamp: Number(bar.timestamp),
        } as any);
      }
      return next.slice(-(timeframe === '1m' ? KLINE_LIMIT_1M : 200));
    });
    setLastUpdate(new Date());
  }, [wsKline, selectedSymbol, timeframe]);

  // WebSocket orderbook 增量更新（主驱动）
  useEffect(() => {
    if (!wsOrderbook) return;
    setOrderbook(wsOrderbook as any);
    setLastUpdate(new Date());
  }, [wsOrderbook]);

  const handleManualRefresh = () => {
    fetchData(false);
    setRefreshSpin(true);
    if (refreshTimerRef.current) clearTimeout(refreshTimerRef.current);
    refreshTimerRef.current = setTimeout(() => setRefreshSpin(false), 1000);
  };

  const togglePrediction = () => {
    setPredictLoading(true);
    setShowPrediction((prev) => !prev);
    setTimeout(() => setPredictLoading(false), 500);
  };

  const mergedPredicted = useMemo(
    () => [...historicalPred, ...futurePred],
    [historicalPred, futurePred]
  );

  /** 仅实盘时间戳 + 未来预测段：与关闭 AI 时主图 category 一致，避免插入孤儿历史预测时间导致均线/缩放观感变化 */
  const sharedTimestamps = useMemo(() => {
    const s = new Set<number>();
    klines.forEach((k) => s.add(k.timestamp));
    futurePred.forEach((k) => s.add(k.timestamp));
    return Array.from(s).sort((a, b) => a - b);
  }, [klines, futurePred]);

  const historicalPredCloseByTs = useMemo(() => {
    const m: Record<number, number> = {};
    historicalPred.forEach((p) => {
      m[p.timestamp] = p.close;
    });
    return m;
  }, [historicalPred]);

  const gapStats = useMemo(
    () => computePredictionGapStats(klines, historicalPred),
    [klines, historicalPred]
  );

  // 实时价格 & OKX App 口径涨跌幅：优先用后端按 OKX sodUtc0 计算的当日涨跌幅。
  const lastKline = klines[klines.length - 1];
  const liveTicker = ticker as RealtimeTicker | null;
  const currentPrice = liveTicker?.last || lastKline?.close || 0;
  const priceChange = liveTicker?.changePercentToday ?? liveTicker?.change_percent_today ?? liveTicker?.changePercent ?? liveTicker?.change_percent ?? 0;

  // 价格变化时触发闪烁
  useEffect(() => {
    if (currentPrice <= 0) return;
    const prev = prevPriceRef.current;
    if (prev > 0 && prev !== currentPrice) {
      const direction = currentPrice > prev ? 'up' : 'down';
      setPriceFlash(direction);
      if (flashTimerRef.current) clearTimeout(flashTimerRef.current);
      flashTimerRef.current = setTimeout(() => setPriceFlash(null), 600);
    }
    prevPriceRef.current = currentPrice;
    return () => { if (flashTimerRef.current) clearTimeout(flashTimerRef.current); };
  }, [currentPrice]);

  const priceFlashClass = priceFlash === 'up'
    ? 'bg-green-500/20 text-green-400'
    : priceFlash === 'down'
      ? 'bg-red-500/20 text-red-400'
      : 'text-white';

  return (
    <div className="h-full overflow-y-auto p-6">
      {/* 顶部工具栏 */}
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <TrendingUp className="w-6 h-6 text-blue-400" />
          <h1 className="text-2xl font-bold text-white">行情</h1>
        </div>

        {/* 右侧工具区 — AI 预测 · 刷新 · 连接状态 */}
        <div className="market-action-strip flex flex-wrap items-center gap-1 rounded-xl border border-crypto-border bg-crypto-card/80 p-1 shadow-sm shadow-black/10">

          {/* AI 预测 Toggle */}
          <button
            type="button"
            onClick={togglePrediction}
            disabled={predictLoading}
            className={clsx(
              'group flex h-9 items-center gap-2 rounded-lg px-3 text-sm font-medium transition-all duration-200',
              showPrediction
                ? 'bg-blue-600/90 text-white shadow-sm shadow-blue-900/30'
                : 'text-gray-400 hover:bg-white/[0.06] hover:text-white',
              predictLoading && 'cursor-wait opacity-80'
            )}
            title="AI 预测"
          >
            <Sparkles
              className={clsx(
                'h-4 w-4 transition-all duration-200',
                predictLoading && 'animate-pulse',
                showPrediction ? 'text-white' : 'text-gray-500 group-hover:text-blue-300'
              )}
            />
            <span>AI 预测</span>
          </button>

          {/* 刷新按钮 */}
          <button
            type="button"
            onClick={handleManualRefresh}
            disabled={loading}
            className={clsx(
              'flex h-9 items-center gap-2 rounded-lg px-3 text-sm font-medium transition-all duration-200',
              'text-gray-400 hover:bg-white/[0.06] hover:text-white',
              loading && 'opacity-50 cursor-not-allowed',
            )}
            title="刷新行情"
          >
            <RefreshCw className={clsx('h-4 w-4 shrink-0', refreshSpin && 'animate-spin')} />
            刷新
          </button>

          {/* 连接状态呼吸灯 */}
          <div
            className={clsx(
              'market-connection-pill flex h-9 items-center gap-2 rounded-lg px-3 text-xs font-medium',
              isConnected ? 'bg-emerald-500/10 text-emerald-300' : 'bg-white/[0.03] text-gray-500'
            )}
          >
            <span className="relative flex h-2 w-2">
              {isConnected && (
                <span className="absolute inset-0 rounded-full bg-[#2ebd85] animate-ping opacity-50" />
              )}
              <span
                className={clsx(
                  'relative inline-flex h-2 w-2 rounded-full',
                  isConnected ? 'bg-[#2ebd85]' : 'bg-gray-500'
                )}
              />
            </span>
            <span>{isConnected ? '实时' : '离线'}</span>
          </div>

        </div>
      </div>

      {loading && klines.length < MIN_KLINES_TO_RENDER ? (
        <div className="flex-1 flex items-center justify-center text-gray-400">
          加载中...
        </div>
      ) : (
        <div className="grid min-h-[560px] grid-cols-1 gap-4 lg:grid-cols-4">
          {/* K线图区域：flex 竖向占满格子高度，否则子元素 height:100% 失效，ECharts 主图会被压成一条 */}
          <div className="lg:col-span-3 bg-crypto-card border border-crypto-border rounded-lg p-4 min-h-0 h-full flex flex-col">
            <div className="mb-2 flex flex-wrap items-center gap-3">
              <div className="market-detail-controls flex min-w-0 flex-wrap items-center gap-3">
                {/* 市场类型 */}
                <div
                  className="market-type-toggle flex overflow-hidden rounded-lg border border-crypto-border bg-crypto-card"
                  data-active-market={marketType === 'swap' ? 'swap' : 'spot'}
                >
                  {([
                    { value: 'swap', label: '合约' },
                    { value: 'spot', label: '现货' },
                  ] as const).map((option) => (
                    <button
                      key={option.value}
                      type="button"
                      onClick={() => setMarketType(option.value)}
                      className={clsx(
                        'h-9 px-3 text-xs font-semibold transition-colors',
                        marketType === option.value
                          ? 'bg-blue-600 text-white shadow-sm shadow-blue-900/30'
                          : 'text-gray-500 hover:bg-gray-800/60 hover:text-gray-300'
                      )}
                    >
                      {option.label}
                    </button>
                  ))}
                </div>

                {/* 交易对搜索 */}
                <SymbolSearch
                  value={selectedSymbol}
                  onChange={setSelectedSymbol}
                  allSymbols={allSymbols}
                  marketType={marketType}
                />
              </div>
              <div className="flex min-w-[13rem] flex-1 flex-wrap items-baseline gap-x-3 gap-y-1">
                <div className={clsx(
                  'inline-flex rounded px-2 py-0.5 text-2xl font-bold leading-none tabular-nums transition-all duration-500',
                  priceFlashClass
                )}>
                  ${currentPrice.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                </div>
                <span className={clsx('text-sm font-medium tabular-nums', priceChange >= 0 ? 'text-up' : 'text-down')}>
                  {priceChange >= 0 ? '+' : ''}{priceChange.toFixed(2)}%
                </span>
                {lastUpdate && (
                  <span className="text-[10px] text-gray-600 tabular-nums">
                    {lastUpdate.toLocaleTimeString()}
                  </span>
                )}
              </div>
              <div className="market-detail-timeframe-controls ml-auto flex flex-wrap items-center justify-end gap-3">
                {/* 时间周期 */}
                <div className="flex bg-crypto-card border border-crypto-border rounded-lg overflow-hidden">
                  {TIMEFRAMES.map((tf) => (
                    <button
                      key={tf}
                      onClick={() => setTimeframe(tf)}
                      className={clsx(
                        'px-3 py-1.5 text-xs font-medium transition-colors',
                        timeframe === tf
                          ? 'bg-blue-600 text-white'
                          : 'text-gray-500 hover:text-gray-300 hover:bg-gray-800/60'
                      )}
                    >
                      {tf}
                    </button>
                  ))}
                </div>
              </div>
              <div className="flex basis-full items-center justify-end gap-2 text-xs text-gray-400">
                <span>共 {klines.length} 根K线</span>
                {showPrediction && mergedPredicted.length > 0 && (
                  <span className="flex items-center gap-1 text-blue-400">
                    <span className="w-1.5 h-1.5 rounded-full bg-blue-400 animate-pulse" />
                    + {mergedPredicted.length} 根预测（同图虚线为 AI 预测 K 线）
                  </span>
                )}
              </div>
            </div>
            {showPrediction && (
              <div className="mb-3 rounded-lg border border-crypto-border bg-black/20 px-4 py-3">
                <div className="text-xs font-medium text-gray-400">预测偏差分析（历史重合样本）</div>
                <div className="mt-2 flex flex-wrap gap-6 text-sm text-gray-200">
                  <div>
                    <span className="text-gray-500">MAE（收盘绝对误差均值）</span>
                    <span className="ml-2 font-mono tabular-nums">{gapStats.mae.toFixed(6)}</span>
                  </div>
                  <div>
                    <span className="text-gray-500">方向准确率</span>
                    <span className="ml-2 font-mono tabular-nums">
                      {gapStats.sampleCount ? `${gapStats.directionAccuracyPct.toFixed(1)}%` : '—'}
                    </span>
                  </div>
                  <div>
                    <span className="text-gray-500">重合样本数</span>
                    <span className="ml-2 font-mono tabular-nums">{gapStats.sampleCount}</span>
                  </div>
                </div>
                <p className="mt-2 text-[10px] text-gray-600 leading-relaxed">
                  历史虚线仅在有落库预测的时间点绘制，中间空缺为正常现象；右侧延伸段为当前模型对未来 K 线的推测，随盘口更新会重新计算。
                </p>
              </div>
            )}
            <div className="flex-1 min-h-0 flex flex-col gap-2">
              <div className="flex-1 min-h-[280px] min-w-0">
                {klines.length >= MIN_KLINES_TO_RENDER ? (
                  <Suspense
                    fallback={
                      <div className="flex h-full items-center justify-center text-gray-400">
                        图表加载中...
                      </div>
                    }
                  >
                    <KlineChart
                      data={klines}
                      predictedData={showPrediction ? mergedPredicted : undefined}
                      symbol={selectedSymbol}
                      height="100%"
                      showVolume
                      showEMA
                      emaPeriods={MARKET_EMA_PERIODS}
                      indicatorSeries={marketIndicators}
                      indicatorTimestamps={marketIndicatorTimestamps}
                      sharedTimestamps={showPrediction ? sharedTimestamps : undefined}
                      showRealCandles
                      showPredCandles={showPrediction}
                      historicalPredCloseByTs={
                        showPrediction ? historicalPredCloseByTs : undefined
                      }
                      defaultShowLastRealBars={
                        timeframe === '1m' ? VISIBLE_1M_REAL_BARS : undefined
                      }
                    />
                  </Suspense>
                ) : (
                  <div className="flex h-full items-center justify-center text-gray-400">
                    K线数据加载中...
                  </div>
                )}
              </div>
            </div>
          </div>

          {/* 订单簿 */}
          <div className="bg-crypto-card border border-crypto-border rounded-lg p-4 overflow-hidden min-h-0 h-full flex flex-col lg:min-h-0">
            <h2 className="text-lg font-semibold text-white mb-4">订单簿</h2>
            <OrderBookChart data={orderbook} maxRows={12} />
          </div>
        </div>
      )}

    </div>
  );
}
