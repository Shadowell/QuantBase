import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Clock3,
  Crosshair,
  RefreshCcw,
  ScanLine,
} from 'lucide-react';
import clsx from 'clsx';
import WatchKlineChart from '../components/WatchKlineChart';
import {
  contractPositionSide,
  isSpotLivePosition,
  LiveContractPositionsPanel,
  LiveOrderDetailsPanel,
  LiveOrderFailureLogDialog,
  positionActionKey,
} from '../components/live/LiveAccountSummaryPanels';
import ThemeDialog from '../components/ThemeDialog';
import { useKlineWebSocket, useTickerWebSocket, useWebSocket } from '../hooks/useWebSocket';
import {
  liveExecutionApi,
  liveWatchApi,
  type LiveExecutionOrder,
  type LiveExecutionPosition,
  type WatchMarketPayload,
  type WatchTradeMarker,
  type WatchlistItem,
} from '../api/client';
import type { Kline, Ticker } from '../types';

const ACCOUNT_ID = 'default';
const DEFAULT_TIMEFRAME = '15m';
const TIMEFRAMES = [
  { value: '1m', label: '1M' },
  { value: '5m', label: '5M' },
  { value: '15m', label: '15M' },
  { value: '1h', label: '1H' },
  { value: '4h', label: '4H' },
  { value: '1d', label: '1D' },
];

function finite(value: unknown, fallback = 0): number {
  const next = Number(value);
  return Number.isFinite(next) ? next : fallback;
}

function compactNumber(value: unknown, digits = 2): string {
  const next = finite(value, Number.NaN);
  if (!Number.isFinite(next)) return '--';
  const abs = Math.abs(next);
  if (abs >= 1e8) return `${(next / 1e8).toFixed(digits)}亿`;
  if (abs >= 1e4) return `${(next / 1e4).toFixed(digits)}万`;
  return next.toLocaleString(undefined, { maximumFractionDigits: digits });
}

function money(value: unknown, digits = 2): string {
  const next = finite(value, Number.NaN);
  if (!Number.isFinite(next)) return '--';
  return `$${compactNumber(next, digits)}`;
}

function signedMoney(value: unknown, digits = 2): string {
  const next = finite(value, Number.NaN);
  if (!Number.isFinite(next)) return '--';
  const sign = next > 0 ? '+' : next < 0 ? '-' : '';
  return `${sign}$${compactNumber(Math.abs(next), digits)}`;
}

function pct(value: unknown): string {
  const next = finite(value, Number.NaN);
  if (!Number.isFinite(next)) return '--';
  return `${next >= 0 ? '+' : ''}${next.toFixed(2)}%`;
}

function fmtTime(value?: string | number | null): string {
  if (!value) return '--';
  const date = typeof value === 'number' ? new Date(value) : new Date(value);
  if (Number.isNaN(date.getTime())) return '--';
  return date.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
}

function symbolBase(symbol: string): string {
  return symbol.split('/', 1)[0] || symbol;
}

function normalizeWatchSymbolKey(symbol?: string | null): string {
  const raw = String(symbol || '').trim().toUpperCase();
  if (!raw) return '';
  if (raw.includes('/')) {
    const [base, rest = ''] = raw.split('/');
    const [quote = 'USDT', settle = quote || 'USDT'] = rest.split(':');
    return `${base}/${quote || 'USDT'}:${settle || quote || 'USDT'}`;
  }
  const okxSwap = raw.match(/^(.+)-([A-Z0-9]+)-SWAP$/);
  if (okxSwap) return `${okxSwap[1]}/${okxSwap[2]}:${okxSwap[2]}`;
  return raw.replace(/-/g, '/');
}

function normalizeKline(row: any): Kline | null {
  if (Array.isArray(row)) {
    const [timestamp, open, high, low, close, volume] = row;
    if (!Number.isFinite(Number(timestamp))) return null;
    return {
      timestamp: Number(timestamp),
      open: finite(open),
      high: finite(high),
      low: finite(low),
      close: finite(close),
      volume: finite(volume),
    };
  }
  if (!row || !Number.isFinite(Number(row.timestamp))) return null;
  return {
    timestamp: Number(row.timestamp),
    open: finite(row.open),
    high: finite(row.high),
    low: finite(row.low),
    close: finite(row.close),
    volume: finite(row.volume),
    quote_volume: row.quoteVolume ?? row.quote_volume,
  };
}

function tickerLast(ticker?: Ticker | null): number | null {
  if (!ticker) return null;
  const value = (ticker as any).last ?? (ticker as any).close;
  const next = finite(value, Number.NaN);
  return Number.isFinite(next) ? next : null;
}

function tickerMark(ticker?: Ticker | null): number | null {
  if (!ticker) return null;
  const value = (ticker as any).markPrice ?? (ticker as any).mark_price;
  const next = finite(value, Number.NaN);
  return Number.isFinite(next) ? next : null;
}

function tickerPct(ticker?: Ticker | null): number | null {
  if (!ticker) return null;
  const value = (ticker as any).changePercent ?? (ticker as any).percentage ?? (ticker as any).change_percent;
  const next = finite(value, Number.NaN);
  return Number.isFinite(next) ? next : null;
}

function tickerDisplayPct(ticker?: Ticker | null): number | null {
  if (!ticker) return null;
  const value = (ticker as any).changePercentToday ?? (ticker as any).change_percent_today ?? tickerPct(ticker);
  const next = finite(value, Number.NaN);
  return Number.isFinite(next) ? next : null;
}

function positionMarginValue(position: LiveExecutionPosition): number {
  return finite(position.initialMargin ?? position.margin ?? position.used, 0);
}

function WatchHeaderMetric({
  label,
  value,
  tone = 'neutral',
}: {
  label: string;
  value: string | number;
  tone?: 'neutral' | 'blue' | 'green' | 'red';
}) {
  return (
    <span
      className={clsx(
        'inline-flex h-6 items-center gap-1 rounded-md border px-2 text-[11px] font-semibold tabular-nums',
        tone === 'blue' && 'border-blue-500/30 bg-blue-500/10 text-blue-200',
        tone === 'green' && 'border-green-500/30 bg-green-500/10 text-green-200',
        tone === 'red' && 'border-red-500/30 bg-red-500/10 text-red-200',
        tone === 'neutral' && 'border-crypto-border bg-white/[0.03] text-gray-300',
      )}
    >
      <span className="font-normal text-gray-500">{label}</span>
      {value}
    </span>
  );
}

function mergeKline(rows: Kline[], update: any): Kline[] {
  const next = normalizeKline(update);
  if (!next) return rows;
  const merged = [...rows];
  const lastIndex = merged.findIndex((row) => row.timestamp === next.timestamp);
  if (lastIndex >= 0) {
    merged[lastIndex] = next;
  } else {
    merged.push(next);
  }
  return merged.sort((a, b) => a.timestamp - b.timestamp).slice(-180);
}

function patchLatestKlineWithPrice(rows: Kline[], price: number | null): Kline[] {
  if (!rows.length || price == null || !Number.isFinite(price)) return rows;
  const latest = rows[rows.length - 1];
  const currentClose = finite(latest.close, Number.NaN);
  const currentHigh = finite(latest.high, price);
  const currentLow = finite(latest.low, price);
  if (currentClose === price && currentHigh >= price && currentLow <= price) return rows;
  const next = [...rows];
  next[next.length - 1] = {
    ...latest,
    close: price,
    high: Math.max(currentHigh, price),
    low: Math.min(currentLow, price),
  };
  return next;
}

function LivePositionCloseConfirm({
  position,
  closeAll,
  submitting,
  onCancel,
  onConfirm,
}: {
  position: LiveExecutionPosition;
  closeAll: boolean;
  submitting: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const symbol = position.symbol || '--';
  const side = contractPositionSide(position);
  const sideLabel = side === 'short' ? '空' : side === 'long' ? '多' : '当前方向';
  return (
    <ThemeDialog
      open
      variant="confirm"
      tone="danger"
      title={closeAll ? '确认市价全平' : '确认平仓'}
      confirmText={submitting ? '提交中...' : closeAll ? '市价全平' : '确认平仓'}
      cancelText="取消"
      onCancel={onCancel}
      onConfirm={onConfirm}
    >
      <div className="space-y-3 text-sm text-gray-300">
        <p>
          将对实盘账户 <span className="font-mono text-gray-100">{ACCOUNT_ID}</span> 提交真实 OKX 市价减仓指令。
        </p>
        <div className="rounded-lg border border-crypto-border bg-crypto-bg px-3 py-2 text-xs">
          <div className="flex items-center justify-between gap-3">
            <span className="text-gray-500">合约</span>
            <span className="font-mono font-semibold text-gray-100">{symbol}</span>
          </div>
          <div className="mt-1 flex items-center justify-between gap-3">
            <span className="text-gray-500">范围</span>
            <span className="font-semibold text-red-200">{closeAll ? '该合约全部方向' : `${sideLabel}仓`}</span>
          </div>
        </div>
        <p className="text-xs leading-5 text-red-200">
          这是实盘操作，请确认当前仓位、方向和账户无误后再继续。
        </p>
      </div>
    </ThemeDialog>
  );
}

function WatchSymbolTile({
  item,
  timeframe,
}: {
  item: WatchlistItem;
  timeframe: string;
}) {
  const symbol = item.symbol;
  const [market, setMarket] = useState<WatchMarketPayload | null>(null);
  const [klines, setKlines] = useState<Kline[]>([]);
  const [markers, setMarkers] = useState<WatchTradeMarker[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [lastLoadedAt, setLastLoadedAt] = useState<Date | null>(null);

  const { ticker: wsTicker, isConnected } = useTickerWebSocket('okx', symbol);
  const { kline: wsKline } = useKlineWebSocket('okx', symbol, timeframe);

  const loadMarket = useCallback(async (quiet = false) => {
    if (!quiet) setLoading(true);
    setError('');
    try {
      const [marketRes, markerRes] = await Promise.all([
        liveWatchApi.getWatchMarket(symbol, ACCOUNT_ID, timeframe, 180),
        liveWatchApi.getTradeMarkers(symbol, ACCOUNT_ID, { limit: 400 }),
      ]);
      setMarket(marketRes);
      setKlines((marketRes.klines || []).map(normalizeKline).filter(Boolean) as Kline[]);
      setMarkers(markerRes.markers || []);
      setLastLoadedAt(new Date());
    } catch (err: any) {
      setError(err?.response?.data?.detail || err?.message || '读取盯盘行情失败');
    } finally {
      if (!quiet) setLoading(false);
    }
  }, [symbol, timeframe]);

  useEffect(() => {
    loadMarket().catch(() => undefined);
  }, [loadMarket]);

  useEffect(() => {
    if (wsKline) setKlines((current) => mergeKline(current, wsKline));
  }, [wsKline]);

  const ticker = useMemo(() => {
    if (!market?.ticker) return wsTicker as Ticker | null;
    return wsTicker ? ({ ...market.ticker, ...wsTicker } as Ticker) : market.ticker;
  }, [market?.ticker, wsTicker]);

  const base = symbolBase(symbol);
  const last = tickerLast(ticker);
  const mark = tickerMark(ticker);
  const displayPrice = mark ?? last;
  const change = tickerDisplayPct(ticker);

  useEffect(() => {
    setKlines((current) => patchLatestKlineWithPrice(current, displayPrice));
  }, [displayPrice]);

  return (
    <section className="watchTileCard min-w-0 overflow-hidden rounded-[22px] border border-crypto-border bg-[#05070b] shadow-[0_18px_44px_rgba(0,0,0,0.32)]">
      <div className="border-b border-crypto-border bg-black px-4 py-4">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <h2 className="truncate text-xl font-black tracking-tight text-white">{base}USDT</h2>
              <span className="rounded-md bg-yellow-500/15 px-2 py-0.5 text-[11px] font-semibold text-yellow-300">永续</span>
              <span className={clsx('h-2 w-2 rounded-full', isConnected ? 'bg-green-400' : 'bg-gray-500')} />
            </div>
            <div className="mt-1 truncate text-xs text-gray-500">{item.sourceStrategyName}</div>
          </div>
          <button
            onClick={() => {
              loadMarket(true).catch(() => undefined);
            }}
            className="inline-flex h-8 w-8 items-center justify-center rounded-full border border-crypto-border bg-gray-900 text-gray-400 hover:text-blue-200"
            aria-label={`刷新 ${symbol}`}
          >
            <RefreshCcw size={14} className={loading ? 'animate-spin' : ''} />
          </button>
        </div>
        <div className="mt-4 grid grid-cols-[1fr_auto] items-end gap-3">
          <div>
            <div className="text-xs text-gray-500">标记价</div>
            <div className={clsx('mt-1 text-4xl font-black tabular-nums', (change || 0) >= 0 ? 'text-rose-500' : 'text-green-400')}>
              {displayPrice != null ? compactNumber(displayPrice, 5) : '--'}
            </div>
            <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
              <span className="text-gray-300">{money(displayPrice, 4)}</span>
              <span className={(change || 0) >= 0 ? 'text-rose-400' : 'text-green-400'}>{pct(change)}</span>
              <span className="text-gray-500">最新成交 {compactNumber(last, 4)}</span>
            </div>
          </div>
          <div className="text-right text-[11px] text-gray-500">
            <div>订单 {item.orderCount}</div>
            <div>{fmtTime(item.lastExecutionAt)}</div>
          </div>
        </div>
      </div>

      <div className="space-y-3 p-4">
        {error && (
          <div className="rounded-lg border border-red-500/40 bg-red-500/10 px-3 py-2 text-xs text-red-200">
            {error}
          </div>
        )}
        <div className="flex items-center justify-between text-[11px] text-gray-500">
          <span className="inline-flex items-center gap-1"><Clock3 size={13} />{lastLoadedAt ? fmtTime(lastLoadedAt.getTime()) : '--'}</span>
          <span className="inline-flex items-center gap-1"><Crosshair size={13} />{markers.length} 个成交点</span>
        </div>

        <div className="space-y-3">
          <WatchKlineChart data={klines} markers={markers} symbol={symbol} timeframe={timeframe} livePrice={displayPrice} height={360} compact />
        </div>
      </div>
    </section>
  );
}

export default function WatchMarket() {
  const [watchlist, setWatchlist] = useState<WatchlistItem[]>([]);
  const [positions, setPositions] = useState<LiveExecutionPosition[]>([]);
  const [historyOrders, setHistoryOrders] = useState<LiveExecutionOrder[]>([]);
  const [orderLog, setOrderLog] = useState<LiveExecutionOrder | null>(null);
  const [timeframe, setTimeframe] = useState(DEFAULT_TIMEFRAME);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [accountPanelError, setAccountPanelError] = useState('');
  const [closeConfirm, setCloseConfirm] = useState<{ position: LiveExecutionPosition; closeAll: boolean } | null>(null);
  const [positionClosingKey, setPositionClosingKey] = useState<string | null>(null);

  const loadWatchlist = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const res = await liveWatchApi.getWatchlist(ACCOUNT_ID);
      setWatchlist(res.items || []);
    } catch (err: any) {
      setError(err?.response?.data?.detail || err?.message || '读取盯盘标的失败');
    } finally {
      setLoading(false);
    }
  }, []);

  const loadLiveAccountPanels = useCallback(async () => {
    setAccountPanelError('');
    try {
      const [positionsRes, historyRes] = await Promise.all([
        liveExecutionApi.listPositions(ACCOUNT_ID),
        liveExecutionApi.listOrderHistory(ACCOUNT_ID, undefined, 100),
      ]);
      setPositions(positionsRes.positions || []);
      setHistoryOrders(historyRes.orders || []);
    } catch (err: any) {
      setAccountPanelError(err?.response?.data?.detail || err?.message || '读取实盘账户明细失败');
    }
  }, []);

  const { isConnected: orderBridgeConnected, subscribe, unsubscribe } = useWebSocket({
    onMessage: (message) => {
      if (message.channel === 'live_order' && message.symbol === ACCOUNT_ID) {
        loadWatchlist().catch(() => undefined);
        loadLiveAccountPanels().catch(() => undefined);
      }
    },
  });

  useEffect(() => {
    loadWatchlist().catch(() => undefined);
    loadLiveAccountPanels().catch(() => undefined);
    const id = window.setInterval(() => {
      loadWatchlist().catch(() => undefined);
      loadLiveAccountPanels().catch(() => undefined);
    }, 10_000);
    return () => window.clearInterval(id);
  }, [loadLiveAccountPanels, loadWatchlist]);

  useEffect(() => {
    if (!orderBridgeConnected) return undefined;
    subscribe('live_order', 'okx', ACCOUNT_ID);
    return () => unsubscribe('live_order', 'okx', ACCOUNT_ID);
  }, [orderBridgeConnected, subscribe, unsubscribe]);

  const contractPositions = useMemo(
    () => positions.filter((position) => !isSpotLivePosition(position)),
    [positions],
  );
  const orderedContractPositions = useMemo(() => {
    const watchlistOrder = new Map(
      watchlist.map((item, index) => [normalizeWatchSymbolKey(item.symbol), index])
    );
    return [...contractPositions].sort((left, right) => {
      const leftOrder = watchlistOrder.get(normalizeWatchSymbolKey(left.symbol)) ?? Number.MAX_SAFE_INTEGER;
      const rightOrder = watchlistOrder.get(normalizeWatchSymbolKey(right.symbol)) ?? Number.MAX_SAFE_INTEGER;
      if (leftOrder !== rightOrder) return leftOrder - rightOrder;
      return normalizeWatchSymbolKey(left.symbol).localeCompare(normalizeWatchSymbolKey(right.symbol));
    });
  }, [contractPositions, watchlist]);

  const contractPositionStats = useMemo(() => {
    const margin = orderedContractPositions.reduce((sum, position) => sum + positionMarginValue(position), 0);
    const pnl = orderedContractPositions.reduce((sum, position) => sum + finite(position.unrealizedPnl, 0), 0);
    return { count: orderedContractPositions.length, margin, pnl };
  }, [orderedContractPositions]);

  const watchlistStats = useMemo(() => {
    const orders = watchlist.reduce((sum, item) => sum + finite(item.orderCount, 0), 0);
    const activeTimeframe = TIMEFRAMES.find((tf) => tf.value === timeframe)?.label || timeframe.toUpperCase();
    return { symbols: watchlist.length, orders, activeTimeframe };
  }, [timeframe, watchlist]);

  const openPositionCloseConfirm = useCallback((position: LiveExecutionPosition, closeAll = false) => {
    setCloseConfirm({ position, closeAll });
  }, []);

  const closeContractPosition = useCallback(async () => {
    if (!closeConfirm) return;
    if (positionClosingKey) return;
    const { position, closeAll } = closeConfirm;
    const key = positionActionKey(position, closeAll);
    setPositionClosingKey(key);
    setAccountPanelError('');
    try {
      const side = contractPositionSide(position);
      await liveExecutionApi.closePosition(ACCOUNT_ID, {
        symbol: position.symbol,
        side: closeAll || side === 'unknown' ? undefined : side,
        closeAll,
        confirmLiveRisk: true,
      });
      setCloseConfirm(null);
      await Promise.all([
        loadWatchlist(),
        loadLiveAccountPanels(),
      ]);
    } catch (err: any) {
      setAccountPanelError(err?.response?.data?.detail || err?.message || '平仓提交失败');
    } finally {
      setPositionClosingKey(null);
    }
  }, [closeConfirm, loadLiveAccountPanels, loadWatchlist, positionClosingKey]);

  return (
    <div className="flex h-full flex-col overflow-hidden bg-crypto-bg text-gray-100">
      <header className="border-b border-crypto-border px-6 py-4">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div>
            <div className="flex items-center gap-3">
              <ScanLine className="h-6 w-6 text-blue-400" />
              <h1 className="text-2xl font-bold tracking-tight text-white">盯盘</h1>
            </div>
            <p className="mt-1 text-sm text-gray-500">实盘策略成交标的每行最多 2 张卡片，按 OKX 手机行情页信息密度组织。</p>
          </div>
          <button
            onClick={() => {
              loadWatchlist().catch(() => undefined);
              loadLiveAccountPanels().catch(() => undefined);
            }}
            className="inline-flex h-10 items-center gap-2 rounded-lg border border-crypto-border bg-crypto-card px-3 text-sm text-gray-300 hover:border-blue-500/60 hover:text-blue-200"
          >
            <RefreshCcw size={16} className={loading ? 'animate-spin' : ''} />
            刷新
          </button>
        </div>

      </header>

      <main className="min-h-0 flex-1 overflow-y-auto p-5">
        {error && (
          <div className="mb-4 rounded-xl border border-red-500/40 bg-red-500/10 px-4 py-3 text-sm text-red-200">
            {error}
          </div>
        )}
        {accountPanelError && (
          <div className="mb-4 rounded-xl border border-red-500/40 bg-red-500/10 px-4 py-3 text-sm text-red-200">
            {accountPanelError}
          </div>
        )}
        <section className="watchWorkspaceLayout flex min-h-0 flex-col gap-4">
          <div className="watchTopPanelGrid grid min-h-0 grid-cols-1 gap-4 xl:grid-cols-[minmax(520px,680px)_minmax(0,1fr)]">
            <LiveContractPositionsPanel
              rows={orderedContractPositions}
              readonly={false}
              headerStats={
                <>
                  <WatchHeaderMetric label="仓位" value={contractPositionStats.count} tone="blue" />
                  <WatchHeaderMetric label="保证金" value={money(contractPositionStats.margin)} />
                  <WatchHeaderMetric
                    label="浮盈"
                    value={signedMoney(contractPositionStats.pnl)}
                    tone={contractPositionStats.pnl > 0 ? 'red' : contractPositionStats.pnl < 0 ? 'green' : 'neutral'}
                  />
                </>
              }
              closingKey={positionClosingKey}
              onClosePosition={(position) => openPositionCloseConfirm(position, false)}
              onCloseAll={(position) => openPositionCloseConfirm(position, true)}
            />
            <div className="watchTilesColumn flex h-[420px] min-h-0 min-w-0 flex-col overflow-hidden rounded-xl border border-crypto-border bg-crypto-bg">
              <div className="watchTilesToolbar flex shrink-0 flex-wrap items-center justify-between gap-3 border-b border-crypto-border px-3 py-2.5">
                <div>
                  <div className="flex flex-wrap items-center gap-2">
                    <div className="text-sm font-semibold text-white">盯盘列表</div>
                    <WatchHeaderMetric label="标的" value={watchlistStats.symbols} tone="blue" />
                    <WatchHeaderMetric label="订单" value={watchlistStats.orders} />
                    <WatchHeaderMetric label="周期" value={watchlistStats.activeTimeframe} />
                  </div>
                  <div className="mt-0.5 text-xs text-gray-500">切换后同步刷新所有可见标的 K 线</div>
                </div>
                <div className="flex bg-crypto-card border border-crypto-border rounded-lg overflow-hidden">
                  {TIMEFRAMES.map((tf) => (
                    <button
                      key={tf.value}
                      onClick={() => setTimeframe(tf.value)}
                      className={clsx(
                        'px-3 py-1.5 text-xs font-medium transition-colors',
                        timeframe === tf.value
                          ? 'bg-blue-600 text-white'
                          : 'text-gray-500 hover:bg-gray-800/60 hover:text-gray-300'
                      )}
                    >
                      {tf.label}
                    </button>
                  ))}
                </div>
              </div>
              <div className="watchTilesBody min-h-0 flex-1 overflow-y-auto p-3">
                {watchlist.length ? (
                  <div className="watchTilesGrid grid grid-cols-1 gap-4 lg:grid-cols-2">
                    {watchlist.map((item) => (
                      <WatchSymbolTile
                        key={item.symbol}
                        item={item}
                        timeframe={timeframe}
                      />
                    ))}
                  </div>
                ) : (
                  <div className="flex h-full min-h-[360px] items-center justify-center rounded-xl border border-dashed border-crypto-border text-gray-500">
                    等待实盘策略成交后自动加入盯盘列表
                  </div>
                )}
              </div>
            </div>
          </div>
          <div className="watchOrdersWidePanel min-w-0">
            <LiveOrderDetailsPanel orders={historyOrders} maxRows={100} onShowLog={setOrderLog} />
          </div>
        </section>
      </main>
      <LiveOrderFailureLogDialog order={orderLog} onClose={() => setOrderLog(null)} />
      {closeConfirm && (
        <LivePositionCloseConfirm
          position={closeConfirm.position}
          closeAll={closeConfirm.closeAll}
          submitting={positionClosingKey === positionActionKey(closeConfirm.position, closeConfirm.closeAll)}
          onCancel={() => setCloseConfirm(null)}
          onConfirm={closeContractPosition}
        />
      )}
    </div>
  );
}
