import { lazy, Suspense, useState, useEffect, useRef, useMemo, useCallback } from 'react';
import {
  FlaskConical, Play, Loader2,
  DollarSign, Activity, Info,
  Calendar, List, RefreshCw, Eye,
  Plus, Trash2, Layers, Square,
  ArrowDown, ArrowDownUp, ArrowUp, ChevronLeft, X,
  FileText, BadgeInfo, CheckCircle2, XCircle, AlertTriangle, Search,
} from 'lucide-react';
import { useStore } from '../stores/useStore';
import { backtestApi, marketApi, type WatchTradeMarker } from '../api/client';
import clsx from 'clsx';
import ThemeAlertDialog, { type ThemeAlertTone } from '../components/ThemeAlertDialog';
import ThemeDialog from '../components/ThemeDialog';
import { getTradeSideDisplay } from '../utils/tradeSide';
import type { Kline } from '../types';

const WatchKlineChart = lazy(() => import('../components/WatchKlineChart'));

// ============================================
// 类型定义
// ============================================
interface EquityPoint {
  timestamp: number;
  equity: number;
  drawdown?: number;
}

interface TradeRecord {
  symbol?: string;
  timestamp: number;
  side: string;
  price: number;
  quantity: number;
  notional_usdt?: number;
  leverage?: number;
  margin?: number;
  pnl: number;
  pnl_pct?: number;
  fee?: number;
  reason?: string;
}

interface BacktestResult {
  id?: number;
  strategyId: number;
  strategyName?: string;
  status: string;
  timeframe?: string;
  timeframeMode?: BacktestTimeframeMode;
  matrixResults?: BacktestResult[];
  startDate?: string;
  endDate?: string;
  initialCapital: number;
  finalCapital?: number;
  totalReturn?: number;
  annualReturn?: number;
  maxDrawdown?: number;
  maxDrawdownDurationDays?: number;
  sharpeRatio?: number;
  sortinoRatio?: number;
  calmarRatio?: number;
  winRate?: number;
  profitFactor?: number;
  totalTrades?: number;
  winningTrades?: number;
  losingTrades?: number;
  avgWinPct?: number;
  avgLossPct?: number;
  maxConsecutiveWins?: number;
  maxConsecutiveLosses?: number;
  expectancy?: number;
  totalFees?: number;
  avgHoldingBars?: number;
  totalBars?: number;
  elapsedSeconds?: number;
  monthlyReturns?: Record<string, number>;
  equityCurve?: EquityPoint[];
  trades?: TradeRecord[];
  errorMessage?: string;
  createdAt?: string;
  isHistorical?: boolean;
}

interface BacktestHistoryItem {
  id: number;
  strategyId: number;
  startDate: string;
  endDate: string;
  initialCapital: number;
  finalCapital?: number | null;
  totalReturn?: number | null;
  annualReturn?: number | null;
  maxDrawdown?: number | null;
  sharpeRatio?: number | null;
  winRate?: number | null;
  profitFactor?: number | null;
  totalTrades?: number | null;
  timeframe?: string | null;
  timeframeMode?: BacktestTimeframeMode | null;
  matrixResults?: BacktestResult[];
  status: string;
  createdAt?: string;
}

type BacktestHistoryDeleteTarget = {
  mode: 'single' | 'batch';
  items: BacktestHistoryItem[];
};

type ResultTab = 'overview' | 'performance' | 'trades';
type TimeRange = '1m' | '3m' | '6m' | '1y' | 'all';
type StrategyAssetClass = 'spot' | 'contract';
type HistoryAssetFilter = 'all' | StrategyAssetClass;
type BacktestView = 'dashboard' | 'detail';
type BacktestStatusFilter = 'all' | 'running' | 'completed' | 'failed' | 'cancelled';
type BacktestSortMode =
  | 'created_desc'
  | 'created_asc'
  | 'return_desc'
  | 'return_asc'
  | 'drawdown_desc'
  | 'drawdown_asc'
  | 'win_rate_desc'
  | 'win_rate_asc';
type BacktestSortField = 'created' | 'return' | 'drawdown' | 'win_rate';
type BacktestSortDirection = 'asc' | 'desc';
type ChartValue = number | null;

interface BacktestChartData {
  dates: string[];
  strategyReturn: number[];
  benchmarkReturn: ChartValue[];
  relativeReturn: ChartValue[];
  drawdown: number[];
  rangeReturn: number | null;
  rangeBenchmarkReturn: number | null;
  rangeMaxDrawdown: number | null;
}

type CryptoBacktestPerformanceMetrics = {
  annualizedVolatility: number | null;
  sortinoRatio: number | null;
  calmarRatio: number | null;
  feeDragPct: number | null;
  payoffRatio: number | null;
  expectancy: number | null;
  expectancyPct: number | null;
  tradeFrequencyPerDay: number | null;
  durationDays: number | null;
};

type BacktestHistoryDerivedMetrics = {
  equityCurve: EquityPoint[];
  sortinoRatio: number | null;
  calmarRatio: number | null;
  avgWinPct: number | null;
  avgLossPct: number | null;
  expectancy: number | null;
  totalFees: number | null;
};

type CryptoMetricItem = {
  label: string;
  value: string;
  positive: boolean;
  description: string;
  caption?: string;
  isDrawdown?: boolean;
};

const BACKTEST_PREFS_KEY = 'quantbase_backtest_prefs_v1';
const BACKTEST_INSTANCES_KEY = 'quantbase_backtest_instances_v1';
const SELECTED_BACKTEST_INSTANCE_KEY = 'quantbase_backtest_selected_instance';
/** @deprecated 旧版单任务恢复 key；新页面使用 BACKTEST_INSTANCES_KEY 保存多个实例。 */
const ACTIVE_BACKTEST_JOB_KEY = 'quantbase_backtest_active_job';
const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;
const BACKTEST_BENCHMARK_SYMBOL = 'BTC/USDT';
const BACKTEST_HISTORY_PAGE_SIZE = 20;
const BACKTEST_WIZARD_STEPS = [
  { step: 1, title: '选择策略', desc: '策略与资金模式' },
  { step: 2, title: '配置参数', desc: '区间、资金与成本' },
  { step: 3, title: '执行回测', desc: '异步任务并行运行' },
  { step: 4, title: '查看结果', desc: '绩效、交易与历史' },
] as const;
const HISTORY_ASSET_FILTERS: Array<{ value: HistoryAssetFilter; label: string }> = [
  { value: 'all', label: '全部' },
  { value: 'spot', label: '现货' },
  { value: 'contract', label: '合约' },
];
const BACKTEST_STATUS_FILTERS: Array<{ value: BacktestStatusFilter; label: string }> = [
  { value: 'all', label: '全部' },
  { value: 'running', label: '运行中' },
  { value: 'completed', label: '已完成' },
  { value: 'failed', label: '已失败' },
  { value: 'cancelled', label: '已停止' },
];
const BACKTEST_SORT_CONTROLS: Array<{ field: BacktestSortField; label: string }> = [
  { field: 'return', label: '收益率' },
  { field: 'drawdown', label: '回撤' },
  { field: 'win_rate', label: '胜率' },
  { field: 'created', label: '创建时间' },
];
const BACKTEST_TIMEFRAME_OPTIONS = [
  { value: '1m', label: '1M' },
  { value: '5m', label: '5M' },
  { value: '15m', label: '15M' },
  { value: '30m', label: '30M' },
  { value: '1h', label: '1H' },
  { value: '4h', label: '4H' },
  { value: '1d', label: '1D' },
] as const;
const BACKTEST_TIMEFRAME_MODES: Array<{ value: BacktestTimeframeMode; label: string; hint: string }> = [
  { value: 'strategy', label: '策略定义', hint: '沿用策略配置周期' },
  { value: 'single', label: '指定周期', hint: '本次回测覆盖一个周期' },
  { value: 'matrix', label: '多周期矩阵', hint: '一次对比多个周期' },
];

type BacktestPrefsV1 = {
  v: 1;
  selectedStrategy?: number | null;
  /** @deprecated symbol is now derived from the selected strategy definition. */
  symbol?: string;
  startDate?: string;
  initialCapital?: number;
  /** @deprecated execution costs now reset to OKX defaults for each new run. */
  makerFeeBps?: number;
  /** @deprecated execution costs now reset to OKX defaults for each new run. */
  takerFeeBps?: number;
  /** @deprecated execution costs now reset to OKX defaults for each new run. */
  slippageBps?: number;
};

const OKX_SPOT_BACKTEST_COSTS = { makerFeeBps: 8, takerFeeBps: 10, slippageBps: 1 } as const;
const OKX_SWAP_BACKTEST_COSTS = { makerFeeBps: 2, takerFeeBps: 5, slippageBps: 1 } as const;

type JobProgressState = {
  currentBar: number;
  totalBars: number;
  percent: number | null;
};

type BacktestInstanceStatus = 'idle' | 'running' | 'cancelling' | 'completed' | 'failed' | 'interrupted' | 'cancelled';
type BacktestTimeframeMode = 'strategy' | 'single' | 'matrix';

type BacktestInstanceConfig = {
  selectedStrategy: number | null;
  startDate: string;
  endDate: string;
  initialCapital: number;
  timeframeMode: BacktestTimeframeMode;
  timeframe: string | null;
  timeframes: string[];
  makerFeeBps: number | null;
  takerFeeBps: number | null;
  slippageBps: number | null;
};

interface BacktestInstance {
  id: string;
  name: string;
  status: BacktestInstanceStatus;
  config: BacktestInstanceConfig;
  activeJobId?: string | null;
  resumeJobId?: string | null;
  jobProgress?: JobProgressState | null;
  result?: BacktestResult | null;
  benchmarkKlines?: { timestamp: number; close: number }[];
  errorMessage?: string | null;
  historyId?: number | null;
  isPersistedHistory?: boolean;
  createdAt: string;
  updatedAt: string;
}

function dateInputValue(date: Date): string {
  return [
    date.getFullYear(),
    String(date.getMonth() + 1).padStart(2, '0'),
    String(date.getDate()).padStart(2, '0'),
  ].join('-');
}

function todayDateInputValue(): string {
  return dateInputValue(new Date());
}

function clampIsoDateToToday(value: string | undefined, fallback: string): string {
  if (!value || !ISO_DATE.test(value)) return fallback;
  const today = todayDateInputValue();
  return value > today ? today : value;
}

function defaultBacktestDateRange() {
  const today = new Date();
  const oneYearAgo = new Date(today);
  oneYearAgo.setFullYear(today.getFullYear() - 1);
  return {
    start: dateInputValue(oneYearAgo),
    end: dateInputValue(today),
  };
}

function loadBacktestPrefs(): BacktestPrefsV1 | null {
  try {
    const raw = localStorage.getItem(BACKTEST_PREFS_KEY);
    if (!raw) return null;
    const p = JSON.parse(raw);
    if (!p || p.v !== 1) return null;
    return p;
  } catch {
    return null;
  }
}

function createBacktestInstance(
  partial: Partial<BacktestInstanceConfig> = {},
  nameIndex = 1,
): BacktestInstance {
  const range = defaultBacktestDateRange();
  const now = new Date().toISOString();
  const id =
    typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function'
      ? crypto.randomUUID()
      : `bt_${Date.now()}_${Math.random().toString(16).slice(2)}`;

  return {
    id,
    name: `回测实例 ${nameIndex}`,
    status: 'idle',
    config: {
      selectedStrategy:
        partial.selectedStrategy != null && Number.isFinite(Number(partial.selectedStrategy))
          ? Number(partial.selectedStrategy)
          : null,
      startDate: clampIsoDateToToday(partial.startDate, range.start),
      endDate: clampIsoDateToToday(partial.endDate, range.end),
      initialCapital:
        typeof partial.initialCapital === 'number' && partial.initialCapital > 0
          ? partial.initialCapital
          : 10000,
      timeframeMode: partial.timeframeMode || 'strategy',
      timeframe: partial.timeframe || '15m',
      timeframes: Array.isArray(partial.timeframes) && partial.timeframes.length > 0
        ? partial.timeframes
        : ['5m', '15m', '1h'],
      makerFeeBps:
        typeof partial.makerFeeBps === 'number' && partial.makerFeeBps >= 0
          ? partial.makerFeeBps
          : null,
      takerFeeBps:
        typeof partial.takerFeeBps === 'number' && partial.takerFeeBps >= 0
          ? partial.takerFeeBps
          : null,
      slippageBps:
        typeof partial.slippageBps === 'number' && partial.slippageBps >= 0
          ? partial.slippageBps
          : null,
    },
    activeJobId: null,
    resumeJobId: null,
    jobProgress: null,
    result: null,
    benchmarkKlines: [],
    errorMessage: null,
    createdAt: now,
    updatedAt: now,
  };
}

function createBacktestDraft(partial: Partial<BacktestInstanceConfig> = {}): BacktestInstanceConfig {
  return createBacktestInstance(partial, 1).config;
}

function quickDateRange(months: number) {
  const end = new Date();
  const start = new Date(end);
  start.setMonth(start.getMonth() - months);
  return {
    startDate: dateInputValue(start),
    endDate: dateInputValue(end),
  };
}

function backtestDateValidationMessage(config: BacktestInstanceConfig): string | null {
  if (!ISO_DATE.test(config.startDate) || !ISO_DATE.test(config.endDate)) {
    return '回测日期格式不正确';
  }
  const today = todayDateInputValue();
  if (config.startDate > config.endDate) {
    return '开始日期不能晚于结束日期';
  }
  if (config.endDate > today) {
    return `结束日期不能晚于当前日期 ${today}`;
  }
  return null;
}

function normalizeBacktestInstance(raw: any, index: number): BacktestInstance | null {
  if (!raw || typeof raw !== 'object') return null;
  const range = defaultBacktestDateRange();
  const cfg = raw.config && typeof raw.config === 'object' ? raw.config : raw;
  const status = String(raw.status || 'idle') as BacktestInstanceStatus;
  const hasErrorMessage = Boolean(raw.errorMessage);
  const normalizedStatus: BacktestInstanceStatus = (
    ['idle', 'running', 'cancelling', 'completed', 'failed', 'interrupted', 'cancelled'] as BacktestInstanceStatus[]
  ).includes(status)
    ? status
    : 'idle';
  const activeJobId = typeof raw.activeJobId === 'string' && raw.activeJobId.trim()
    ? raw.activeJobId.trim()
    : null;
  const resumeJobId = typeof raw.resumeJobId === 'string' && raw.resumeJobId.trim()
    ? raw.resumeJobId.trim()
    : null;

  return {
    id: String(raw.id || `legacy-${index + 1}`),
    name: String(raw.name || `回测实例 ${index + 1}`),
    status: hasErrorMessage ? 'failed' : activeJobId && normalizedStatus === 'idle' ? 'running' : normalizedStatus,
    config: {
      selectedStrategy:
        cfg.selectedStrategy != null && Number.isFinite(Number(cfg.selectedStrategy))
          ? Number(cfg.selectedStrategy)
          : null,
      startDate: clampIsoDateToToday(typeof cfg.startDate === 'string' ? cfg.startDate : undefined, range.start),
      endDate: clampIsoDateToToday(typeof cfg.endDate === 'string' ? cfg.endDate : undefined, range.end),
      initialCapital:
        typeof cfg.initialCapital === 'number' && cfg.initialCapital > 0
          ? cfg.initialCapital
          : Number(cfg.initialCapital) > 0
            ? Number(cfg.initialCapital)
            : 10000,
      timeframeMode: (['strategy', 'single', 'matrix'] as BacktestTimeframeMode[]).includes(cfg.timeframeMode)
        ? cfg.timeframeMode
        : 'strategy',
      timeframe: typeof cfg.timeframe === 'string' && cfg.timeframe ? cfg.timeframe : '15m',
      timeframes: Array.isArray(cfg.timeframes) && cfg.timeframes.length > 0
        ? cfg.timeframes.map((value: unknown) => String(value)).filter(Boolean)
        : ['5m', '15m', '1h'],
      makerFeeBps:
        cfg.makerFeeBps != null && Number(cfg.makerFeeBps) >= 0 ? Number(cfg.makerFeeBps) : null,
      takerFeeBps:
        cfg.takerFeeBps != null && Number(cfg.takerFeeBps) >= 0 ? Number(cfg.takerFeeBps) : null,
      slippageBps:
        cfg.slippageBps != null && Number(cfg.slippageBps) >= 0 ? Number(cfg.slippageBps) : null,
    },
    activeJobId,
    resumeJobId,
    jobProgress: raw.jobProgress || null,
    result: null,
    benchmarkKlines: [],
    errorMessage: raw.errorMessage ? String(raw.errorMessage) : null,
    createdAt: String(raw.createdAt || new Date().toISOString()),
    updatedAt: String(raw.updatedAt || new Date().toISOString()),
  };
}

function isBacktestPlaceholderInstance(instance: BacktestInstance): boolean {
  return (
    !instance.config.selectedStrategy &&
    instance.status === 'idle' &&
    !instance.activeJobId &&
    !instance.jobProgress &&
    !instance.result &&
    !instance.errorMessage
  );
}

function isBacktestUnhydratedCompletedInstance(instance: BacktestInstance): boolean {
  return (
    instance.status === 'completed' &&
    !instance.result &&
    !instance.historyId &&
    !instance.isPersistedHistory
  );
}

function loadBacktestInstances(initialBt: BacktestPrefsV1 | null): BacktestInstance[] {
  try {
    const raw = localStorage.getItem(BACKTEST_INSTANCES_KEY);
    if (raw) {
      const parsed = JSON.parse(raw);
      const rawInstances: unknown[] = Array.isArray(parsed?.instances) ? parsed.instances : [];
      const instances = rawInstances
        .map((item: unknown, index: number) => normalizeBacktestInstance(item, index))
        .filter((instance): instance is BacktestInstance => Boolean(instance))
        .filter((instance) => !isBacktestPlaceholderInstance(instance))
        .filter((instance) => !isBacktestUnhydratedCompletedInstance(instance));
      if (instances.length > 0) return instances;
    }
  } catch {
    /* ignore */
  }

  let legacyJobId = '';
  try {
    legacyJobId = sessionStorage.getItem(ACTIVE_BACKTEST_JOB_KEY)?.trim() || '';
  } catch {
    /* ignore */
  }

  const legacy = createBacktestInstance(
    {
      selectedStrategy: initialBt?.selectedStrategy ?? null,
      startDate: initialBt?.startDate,
      initialCapital: initialBt?.initialCapital,
    },
    1,
  );

  if (legacyJobId) {
    legacy.status = 'running';
    legacy.activeJobId = legacyJobId;
  }

  return legacy.activeJobId ? [legacy] : [];
}

function persistableBacktestInstances(instances: BacktestInstance[]) {
  return instances.filter((instance) => !instance.isPersistedHistory).map((instance) => ({
    id: instance.id,
    name: instance.name,
    status: instance.status,
    config: instance.config,
    activeJobId: instance.activeJobId ?? null,
    resumeJobId: instance.resumeJobId ?? null,
    jobProgress: instance.jobProgress ?? null,
    errorMessage: instance.errorMessage ?? null,
    createdAt: instance.createdAt,
    updatedAt: instance.updatedAt,
  }));
}

function backtestInstanceStatusMeta(status: BacktestInstanceStatus) {
  switch (status) {
    case 'running':
      return { label: '运行中', className: 'bg-blue-500/15 text-blue-300 border-blue-500/30' };
    case 'cancelling':
      return { label: '停止中', className: 'bg-amber-500/15 text-amber-300 border-amber-500/30' };
    case 'completed':
      return { label: '已完成', className: 'bg-green-500/15 text-green-300 border-green-500/30' };
    case 'failed':
      return { label: '失败', className: 'bg-red-500/15 text-red-300 border-red-500/30' };
    case 'interrupted':
      return { label: '已中断', className: 'bg-amber-500/15 text-amber-300 border-amber-500/30' };
    case 'cancelled':
      return { label: '已停止', className: 'bg-gray-500/10 text-gray-300 border-gray-500/30' };
    default:
      return { label: '待配置', className: 'bg-gray-500/10 text-gray-400 border-gray-500/20' };
  }
}

function backtestInstanceActionStatusLabel(status: BacktestInstanceStatus) {
  switch (status) {
    case 'completed':
      return '成功';
    case 'failed':
      return '失败';
    case 'running':
      return '运行中';
    case 'cancelling':
      return '停止中';
    case 'interrupted':
      return '中断';
    case 'cancelled':
      return '停止';
    default:
      return '待配置';
  }
}

type BacktestInstanceActionTone = 'blue' | 'success' | 'green' | 'red' | 'amber' | 'neutral';

const BACKTEST_INSTANCE_ACTION_BUTTON_BASE =
  'backtestInstanceActionButton inline-flex h-10 min-w-[92px] items-center justify-center gap-2 rounded-xl border px-4 text-sm font-semibold tracking-[0.01em] transition-[background-color,border-color,color,box-shadow,transform] duration-150 hover:-translate-y-px focus-visible:outline-none focus-visible:ring-2 disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:translate-y-0';

const BACKTEST_INSTANCE_ACTION_BUTTON_TONES: Record<BacktestInstanceActionTone, string> = {
  blue: 'border-blue-400/45 bg-blue-500/[0.12] text-blue-100 shadow-[0_0_0_1px_rgba(59,130,246,0.08),inset_0_1px_0_rgba(255,255,255,0.08)] hover:border-blue-300/70 hover:bg-blue-500/[0.18] hover:shadow-[0_0_18px_-10px_rgba(59,130,246,0.9),inset_0_1px_0_rgba(255,255,255,0.10)] focus-visible:ring-blue-500/30',
  success: 'border-emerald-300/55 bg-emerald-400/[0.18] text-emerald-50 shadow-[0_0_0_1px_rgba(52,211,153,0.14),0_0_20px_-12px_rgba(52,211,153,0.95),inset_0_1px_0_rgba(255,255,255,0.10)] hover:border-emerald-200/75 hover:bg-emerald-400/[0.24] hover:shadow-[0_0_24px_-10px_rgba(52,211,153,0.95),inset_0_1px_0_rgba(255,255,255,0.12)] focus-visible:ring-emerald-300/35',
  green: 'border-emerald-500/40 bg-emerald-500/[0.10] text-emerald-200 shadow-[0_0_0_1px_rgba(16,185,129,0.06),inset_0_1px_0_rgba(255,255,255,0.06)] hover:border-emerald-400/65 hover:bg-emerald-500/[0.16] focus-visible:ring-emerald-500/30',
  red: 'border-red-400/45 bg-red-500/[0.09] text-red-100 shadow-[0_0_0_1px_rgba(248,113,113,0.08),inset_0_1px_0_rgba(255,255,255,0.06)] hover:border-red-300/70 hover:bg-red-500/[0.15] hover:shadow-[0_0_18px_-11px_rgba(248,113,113,0.9),inset_0_1px_0_rgba(255,255,255,0.08)] focus-visible:ring-red-500/30',
  amber: 'border-amber-400/45 bg-amber-500/[0.10] text-amber-100 shadow-[0_0_0_1px_rgba(245,158,11,0.08),inset_0_1px_0_rgba(255,255,255,0.06)] hover:border-amber-300/70 hover:bg-amber-500/[0.16] focus-visible:ring-amber-500/30',
  neutral: 'border-white/10 bg-white/[0.035] text-gray-200 shadow-[0_0_0_1px_rgba(255,255,255,0.03),inset_0_1px_0_rgba(255,255,255,0.05)] hover:border-gray-400/45 hover:bg-white/[0.07] hover:text-white focus-visible:ring-gray-500/25',
};

function backtestInstanceActionButtonClass(tone: BacktestInstanceActionTone, extra?: string) {
  return clsx(BACKTEST_INSTANCE_ACTION_BUTTON_BASE, BACKTEST_INSTANCE_ACTION_BUTTON_TONES[tone], extra);
}

function backtestInstanceActionStatusTone(status: BacktestInstanceStatus): BacktestInstanceActionTone {
  switch (status) {
    case 'completed':
      return 'success';
    case 'failed':
      return 'red';
    case 'running':
      return 'blue';
    case 'cancelling':
    case 'interrupted':
      return 'amber';
    default:
      return 'neutral';
  }
}

function backtestInstanceActionStatusIcon(status: BacktestInstanceStatus) {
  switch (status) {
    case 'completed':
      return <CheckCircle2 className="h-4 w-4" />;
    case 'failed':
      return <XCircle className="h-4 w-4" />;
    case 'running':
    case 'cancelling':
      return <Loader2 className="h-4 w-4 animate-spin" />;
    case 'interrupted':
      return <AlertTriangle className="h-4 w-4" />;
    default:
      return <BadgeInfo className="h-4 w-4" />;
  }
}

function backtestInstanceStatusBucket(status: BacktestInstanceStatus): BacktestStatusFilter {
  if (status === 'running' || status === 'cancelling') return 'running';
  if (status === 'completed') return 'completed';
  if (status === 'failed' || status === 'interrupted') return 'failed';
  if (status === 'cancelled') return 'cancelled';
  return 'all';
}

function backtestInstanceReturn(instance: BacktestInstance): number | null {
  const value = instance.result?.totalReturn;
  return value != null && Number.isFinite(value) ? value : null;
}

function backtestInstanceDrawdown(instance: BacktestInstance): number | null {
  const value = instance.result?.maxDrawdown;
  return value != null && Number.isFinite(value) ? value : null;
}

function backtestInstanceWinRate(instance: BacktestInstance): number | null {
  const value = instance.result?.winRate;
  return value != null && Number.isFinite(value) ? value : null;
}

function backtestInstanceCanContinue(instance: BacktestInstance): boolean {
  if (instance.status === 'running' || instance.status === 'cancelling' || instance.status === 'completed') {
    return false;
  }
  return Boolean(
    instance.resumeJobId ||
      (
        (instance.status === 'interrupted' || instance.status === 'failed') &&
        instance.config.selectedStrategy &&
        instance.config.startDate &&
        instance.config.endDate
      ),
  );
}

function stringList(value: unknown): string[] {
  if (Array.isArray(value)) {
    return value.map((item) => String(item || '').trim()).filter(Boolean);
  }
  if (typeof value === 'string' && value.trim()) {
    return value
      .split(',')
      .map((item) => item.trim())
      .filter(Boolean);
  }
  return [];
}

function strategySymbols(strategy: any | null | undefined): string[] {
  if (!strategy) return [];
  const cfg = strategy.config && typeof strategy.config === 'object' ? strategy.config : {};
  return (
    stringList(strategy.symbols).length > 0
      ? stringList(strategy.symbols)
      : stringList((cfg as Record<string, unknown>).symbols)
  );
}

function strategyBenchmarkSymbol(): string {
  return BACKTEST_BENCHMARK_SYMBOL;
}

function strategyTradeSymbols(strategy: any | null | undefined): string[] {
  if (!strategy) return [];
  const cfg = strategy.config && typeof strategy.config === 'object' ? strategy.config as Record<string, unknown> : {};
  return (
    stringList(cfg.tradeSymbols).length > 0
      ? stringList(cfg.tradeSymbols)
      : stringList(cfg.trade_symbols)
  );
}

function strategyTimeframe(strategy: any | null | undefined): string {
  if (!strategy) return '';
  const cfg = strategy.config && typeof strategy.config === 'object' ? strategy.config as Record<string, unknown> : {};
  const raw = cfg.timeframe ?? cfg.klineTimeframe ?? strategy.timeframe;
  return String(raw || '').trim();
}

function backtestTimeframeLabel(timeframe: string | null | undefined): string {
  const normalized = normalizeBacktestTimeframe(timeframe);
  const option = BACKTEST_TIMEFRAME_OPTIONS.find((item) => item.value === normalized);
  return option?.label || String(timeframe || '未定义').toUpperCase();
}

function normalizeBacktestTimeframe(timeframe: string | null | undefined): string | null {
  const value = String(timeframe || '').trim().toLowerCase();
  if (!value) return null;
  const option = BACKTEST_TIMEFRAME_OPTIONS.find(
    (item) => item.value === value || item.label.toLowerCase() === value,
  );
  return option?.value || value;
}

function uniqueBacktestTimeframes(values: Array<string | null | undefined>): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  values.forEach((value) => {
    const normalized = normalizeBacktestTimeframe(value);
    if (!normalized || seen.has(normalized)) return;
    seen.add(normalized);
    out.push(normalized);
  });
  return out;
}

function backtestResultTimeframes(result: BacktestResult | null | undefined): string[] {
  if (!result) return [];
  if (Array.isArray(result.matrixResults) && result.matrixResults.length > 0) {
    return uniqueBacktestTimeframes(result.matrixResults.map((item) => item.timeframe));
  }
  return uniqueBacktestTimeframes([result.timeframe]);
}

function backtestEffectiveTimeframe(config: BacktestInstanceConfig, strategy: any | null | undefined): string {
  if (config.timeframeMode === 'single' && config.timeframe) return config.timeframe;
  if (config.timeframeMode === 'matrix' && config.timeframes.length > 0) return config.timeframes[0];
  return strategyTimeframe(strategy) || config.timeframe || '1h';
}

function backtestEffectiveTimeframes(config: BacktestInstanceConfig, strategy: any | null | undefined): string[] {
  if (config.timeframeMode === 'matrix') {
    return config.timeframes.length > 0 ? config.timeframes : [backtestEffectiveTimeframe(config, strategy)];
  }
  return [backtestEffectiveTimeframe(config, strategy)];
}

function backtestInstanceTimeframes(instance: BacktestInstance, strategy: any | null | undefined): string[] {
  const resultTimeframes = backtestResultTimeframes(instance.result);
  if (resultTimeframes.length > 0) return resultTimeframes;
  if (instance.config.timeframeMode === 'matrix') {
    return uniqueBacktestTimeframes(instance.config.timeframes);
  }
  return uniqueBacktestTimeframes([backtestEffectiveTimeframe(instance.config, strategy)]);
}

function finiteNumber(value: unknown): number | null {
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

function optionalFiniteNumber(value: unknown): number | undefined {
  return finiteNumber(value) ?? undefined;
}

function backtestTradeNotional(trade: TradeRecord): number | null {
  const explicit = finiteNumber(trade.notional_usdt);
  if (explicit != null) return explicit;
  const inferred = Math.abs(trade.price * trade.quantity);
  return Number.isFinite(inferred) && inferred > 0 ? inferred : null;
}

function backtestTradeMargin(trade: TradeRecord): number | null {
  const explicit = finiteNumber(trade.margin);
  if (explicit != null) return explicit;
  const leverage = finiteNumber(trade.leverage);
  const notional = backtestTradeNotional(trade);
  if (leverage != null && leverage > 0 && notional != null) {
    return notional / leverage;
  }
  return null;
}

function formatBacktestTradeMoney(value: number | null | undefined, digits = 2): string {
  if (value == null || !Number.isFinite(value)) return '-';
  return value.toFixed(digits);
}

function formatBacktestTradeLeverage(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value) || value <= 0) return '-';
  return `${Number.isInteger(value) ? value.toFixed(0) : value.toFixed(2)}x`;
}

function backtestRequestMatchesInstance(request: Record<string, unknown> | null | undefined, instance: BacktestInstance): boolean {
  if (!request || !instance.config.selectedStrategy) return false;
  if (Number(request.strategyId ?? request.strategy_id) !== Number(instance.config.selectedStrategy)) return false;
  if (String(request.startDate ?? request.start_date ?? '') !== instance.config.startDate) return false;
  if (String(request.endDate ?? request.end_date ?? '') !== instance.config.endDate) return false;
  const requestCapital = finiteNumber(request.initialCapital ?? request.initial_capital);
  if (requestCapital == null) return true;
  return Math.abs(requestCapital - instance.config.initialCapital) < 1e-9;
}

function strategyIsContract(strategy: any | null | undefined): boolean {
  if (!strategy) return false;
  const cfg = strategy.config && typeof strategy.config === 'object' ? strategy.config as Record<string, unknown> : {};
  if (String(cfg.marketType ?? cfg.market_type ?? '').toLowerCase() === 'swap') return true;
  if (String(cfg.instType ?? cfg.inst_type ?? '').toUpperCase() === 'SWAP') return true;
  const name = String(strategy.name || '');
  const symbols = [
    ...stringList(strategy.symbols),
    ...stringList(cfg.symbols),
    ...stringList(cfg.tradeSymbols),
    ...stringList(cfg.trade_symbols),
  ];
  return name.startsWith('[合约]') || symbols.some((s) => s.includes(':USDT') || s.endsWith('-SWAP'));
}

function inferStrategyAssetClassFromName(name: unknown): StrategyAssetClass | null {
  const text = String(name || '').trim();
  if (!text) return null;
  if (text.startsWith('[合约]') || text.includes('/USDT:USDT') || text.includes('-SWAP')) return 'contract';
  if (text.startsWith('[现货]')) return 'spot';
  return null;
}

function strategyAssetClass(strategy: any | null | undefined): StrategyAssetClass {
  return strategyIsContract(strategy) ? 'contract' : 'spot';
}

function strategyAssetClassById(strategies: any[], strategyId: number | null | undefined): StrategyAssetClass {
  const strategy = strategies.find((s) => Number(s.id) === Number(strategyId));
  return strategyAssetClass(strategy);
}

function backtestResultAssetClass(
  strategies: any[],
  result: BacktestResult | null | undefined,
  strategyId: number | null | undefined,
): StrategyAssetClass {
  const strategy = strategies.find((s) => Number(s.id) === Number(result?.strategyId ?? strategyId));
  if (strategy) return strategyAssetClass(strategy);
  return inferStrategyAssetClassFromName(result?.strategyName) || 'spot';
}

function backtestInstanceAssetClass(strategies: any[], instance: BacktestInstance): StrategyAssetClass {
  const strategy = strategies.find((s) => Number(s.id) === Number(instance.config.selectedStrategy));
  if (strategy) return strategyAssetClass(strategy);
  return (
    inferStrategyAssetClassFromName(instance.result?.strategyName) ||
    inferStrategyAssetClassFromName(instance.name) ||
    'spot'
  );
}

function strategyNameColorClass(assetClass: StrategyAssetClass): string {
  return assetClass === 'contract' ? 'text-[#FFAB73]' : 'text-yellow-300';
}

function strategyAssetBadgeClass(assetClass: StrategyAssetClass): string {
  return assetClass === 'contract'
    ? 'border-purple-500/30 bg-purple-500/10 text-purple-300'
    : 'border-yellow-500/30 bg-yellow-500/10 text-yellow-300';
}

function isExplicitFalse(value: unknown): boolean {
  if (value === false) return true;
  if (typeof value === 'number') return value === 0;
  if (typeof value === 'string') {
    return ['false', '0', 'no', 'live', 'real'].includes(value.trim().toLowerCase());
  }
  return false;
}

function strategyIsBacktestSelectable(strategy: any | null | undefined): boolean {
  if (!strategy) return false;
  const cfg = strategy.config && typeof strategy.config === 'object' ? strategy.config as Record<string, unknown> : {};
  const name = String(strategy.name || '');
  if (name.includes('[实盘试运行]') || name.includes('[实盘]')) return false;
  if (
    isExplicitFalse(cfg.is_paper_trading) ||
    isExplicitFalse(cfg.isPaperTrading) ||
    isExplicitFalse(cfg.dry_run) ||
    isExplicitFalse(cfg.dryRun)
  ) {
    return false;
  }
  const mode = String(
    cfg.mode ?? cfg.run_mode ?? cfg.runMode ?? cfg.execution_mode ?? cfg.executionMode ?? '',
  ).toLowerCase();
  return !['live', 'real', 'production'].some((token) => mode.includes(token));
}

function strategyBacktestCostDefaults(strategy: any | null | undefined) {
  const isContract = strategyIsContract(strategy);
  const okxCosts = isContract ? OKX_SWAP_BACKTEST_COSTS : OKX_SPOT_BACKTEST_COSTS;

  return {
    makerFeeBps: okxCosts.makerFeeBps,
    takerFeeBps: okxCosts.takerFeeBps,
    slippageBps: okxCosts.slippageBps,
  };
}

function symbolSummary(symbols: string[], max = 5): string {
  if (symbols.length === 0) return '策略未定义';
  const shown = symbols.slice(0, max).join(', ');
  return symbols.length > max ? `${shown} 等 ${symbols.length} 个` : shown;
}

function backtestStrategySearchText(strategy: any): string {
  const cfg = strategy?.config && typeof strategy.config === 'object' ? strategy.config : {};
  const symbols = [...strategySymbols(strategy), ...strategyTradeSymbols(strategy)];
  const assetText = strategyIsContract(strategy) ? '合约 contract swap perpetual futures' : '现货 spot';
  return [
    strategy?.name,
    strategy?.description,
    strategy?.strategy_key,
    strategy?.strategyKey,
    cfg.strategy_key,
    cfg.strategyKey,
    cfg.strategy_type,
    cfg.strategyType,
    cfg.type,
    cfg.name,
    strategyTimeframe(strategy),
    assetText,
    ...symbols,
  ]
    .filter(Boolean)
    .join(' ')
    .toLowerCase();
}

function strategyMatchesBacktestSearch(strategy: any, query: string): boolean {
  const tokens = query
    .trim()
    .toLowerCase()
    .split(/\s+/)
    .filter(Boolean);
  if (tokens.length === 0) return true;
  const haystack = backtestStrategySearchText(strategy);
  return tokens.every((token) => haystack.includes(token));
}

function backtestInstanceSearchText(
  instance: BacktestInstance,
  strategies: any[],
  strategyInfo: any | null | undefined,
): string {
  const timeframes = backtestInstanceTimeframes(instance, strategyInfo).map(backtestTimeframeLabel);
  const assetClass = backtestInstanceAssetClass(strategies, instance);
  const statusMeta = backtestInstanceStatusMeta(instance.status);
  return [
    backtestStrategyDisplayName(strategies, instance.config.selectedStrategy),
    instance.result?.strategyName,
    instance.name,
    instance.config.startDate,
    instance.config.endDate,
    instance.createdAt,
    instance.historyId,
    instance.activeJobId,
    instance.resumeJobId,
    assetClass === 'contract' ? '合约 contract swap perpetual futures' : '现货 spot',
    statusMeta.label,
    instance.status,
    instance.result?.timeframe,
    instance.config.timeframe,
    ...timeframes,
    ...strategySymbols(strategyInfo),
    ...strategyTradeSymbols(strategyInfo),
  ]
    .filter((item) => item != null && item !== '')
    .join(' ')
    .toLowerCase();
}

function backtestInstanceMatchesSearch(
  instance: BacktestInstance,
  strategies: any[],
  strategyInfo: any | null | undefined,
  query: string,
): boolean {
  const tokens = query
    .trim()
    .toLowerCase()
    .split(/\s+/)
    .filter(Boolean);
  if (tokens.length === 0) return true;
  const haystack = backtestInstanceSearchText(instance, strategies, strategyInfo);
  return tokens.every((token) => haystack.includes(token));
}

function strategyNameById(strategies: any[], strategyId: number): string {
  return backtestStrategyDisplayName(strategies, strategyId);
}

function backtestStrategyDisplayName(strategies: any[], strategyId: number | null | undefined): string {
  if (strategyId == null || !Number.isFinite(Number(strategyId))) return '未选择策略';
  const match = strategies.find((s) => Number(s.id) === Number(strategyId));
  if (match?.name) return String(match.name);
  return strategies.length === 0 ? '策略加载中' : '策略已不存在';
}

function formatDateTime(value?: string | null): string {
  if (!value) return '-';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString('zh-CN', { hour12: false });
}

function normalizeTradeTimestamp(value: unknown): number {
  const n = Number(value);
  if (!Number.isFinite(n) || n <= 0) return Date.now();
  return n < 1_000_000_000_000 ? n * 1000 : n;
}

function normalizeHistoryTrades(value: unknown): TradeRecord[] {
  if (!Array.isArray(value)) return [];
  return value.map((trade: any) => ({
    symbol: trade?.symbol,
    timestamp: normalizeTradeTimestamp(trade?.timestamp),
    side: String(trade?.side || ''),
    price: finiteNumber(trade?.price) ?? 0,
    quantity: finiteNumber(trade?.quantity) ?? 0,
    notional_usdt: optionalFiniteNumber(trade?.notional_usdt ?? trade?.notionalUsdt ?? trade?.notional),
    leverage: optionalFiniteNumber(trade?.leverage),
    margin: optionalFiniteNumber(trade?.margin ?? trade?.margin_usdt ?? trade?.marginUsdt),
    pnl: finiteNumber(trade?.pnl) ?? 0,
    pnl_pct: finiteNumber(trade?.pnlPct ?? trade?.pnl_pct) ?? undefined,
    fee: finiteNumber(trade?.fee) ?? undefined,
    reason: trade?.reason ? String(trade.reason) : undefined,
  }));
}

function normalizeBacktestEquityCurve(value: unknown): EquityPoint[] {
  if (!Array.isArray(value)) return [];
  return value
    .map((point: any) => {
      const timestamp = normalizeTradeTimestamp(point?.timestamp);
      const equity = finiteNumber(point?.equity);
      const drawdown = finiteNumber(point?.drawdown);
      if (equity == null || equity <= 0) return null;
      return {
        timestamp,
        equity,
        drawdown: drawdown ?? undefined,
      } satisfies EquityPoint;
    })
    .filter(Boolean) as EquityPoint[];
}

function deriveBacktestTradePnlSamples(
  trades: TradeRecord[],
  totalTrades?: number | null,
): Array<{ timestamp: number; pnl: number; fee: number }> {
  const finiteRows = trades
    .map((trade) => ({
      timestamp: normalizeTradeTimestamp(trade.timestamp),
      pnl: finiteNumber(trade.pnl),
      fee: finiteNumber(trade.fee) ?? 0,
      side: String(trade.side || '').toLowerCase(),
      reason: String(trade.reason || '').toLowerCase(),
    }))
    .filter((trade) => trade.pnl != null) as Array<{
      timestamp: number;
      pnl: number;
      fee: number;
      side: string;
      reason: string;
    }>;
  const nonZeroRows = finiteRows.filter((trade) => Math.abs(trade.pnl) > 1e-9);
  if (nonZeroRows.length > 0) return nonZeroRows;
  const closeLikeRows = finiteRows.filter((trade) => (
    trade.reason.includes('close') ||
    trade.side.includes('close') ||
    trade.side.includes('sell') ||
    trade.side.includes('cover')
  ));
  if (closeLikeRows.length > 0) return closeLikeRows;
  const expected = finiteNumber(totalTrades);
  if (expected != null && expected > 0 && finiteRows.length <= expected + 1) return finiteRows;
  return [];
}

function rebuildEquityCurveFromTradePnl(
  initialCapital: number,
  trades: TradeRecord[],
  totalTrades?: number | null,
): EquityPoint[] {
  if (!Number.isFinite(initialCapital) || initialCapital <= 0) return [];
  const samples = deriveBacktestTradePnlSamples(trades, totalTrades)
    .sort((a, b) => a.timestamp - b.timestamp);
  if (samples.length === 0) return [];
  let equity = initialCapital;
  let peak = initialCapital;
  return samples.map((sample) => {
    equity += sample.pnl;
    peak = Math.max(peak, equity);
    const drawdown = peak > 0 ? ((peak - equity) / peak) * 100 : 0;
    return {
      timestamp: sample.timestamp,
      equity,
      drawdown,
    };
  });
}

function sortinoRatioFromEquityCurve(equityCurve: EquityPoint[]): number | null {
  if (!equityCurve || equityCurve.length < 2) return null;
  const dailyEquity = new Map<string, number>();
  [...equityCurve]
    .filter((point) => Number.isFinite(point.timestamp) && Number.isFinite(point.equity) && point.equity > 0)
    .sort((a, b) => a.timestamp - b.timestamp)
    .forEach((point) => {
      dailyEquity.set(new Date(point.timestamp).toISOString().slice(0, 10), point.equity);
    });
  const values = [...dailyEquity.values()];
  if (values.length < 3) return null;
  const returns: number[] = [];
  for (let i = 1; i < values.length; i++) {
    if (values[i - 1] > 0) returns.push((values[i] - values[i - 1]) / values[i - 1]);
  }
  const downsideReturns = returns.filter((value) => value < 0);
  if (returns.length < 2 || downsideReturns.length < 2) return null;
  const mean = returns.reduce((sum, value) => sum + value, 0) / returns.length;
  const downsideMean = downsideReturns.reduce((sum, value) => sum + value, 0) / downsideReturns.length;
  const downsideVariance = downsideReturns.reduce((sum, value) => sum + (value - downsideMean) ** 2, 0) / downsideReturns.length;
  const downsideStd = Math.sqrt(downsideVariance);
  return downsideStd > 0 ? (mean / downsideStd) * Math.sqrt(365) : null;
}

function deriveBacktestHistoryMetrics(detail: any, trades: TradeRecord[]): BacktestHistoryDerivedMetrics {
  const initialCapital = finiteNumber(detail?.initialCapital ?? detail?.initial_capital) ?? 0;
  const totalTrades = finiteNumber(detail?.totalTrades ?? detail?.total_trades);
  const persistedEquityCurve = normalizeBacktestEquityCurve(detail?.equityCurve ?? detail?.equity_curve);
  const equityCurve = persistedEquityCurve.length > 0
    ? persistedEquityCurve
    : rebuildEquityCurveFromTradePnl(initialCapital, trades, totalTrades);
  const pnlSamples = deriveBacktestTradePnlSamples(trades, totalTrades);
  const wins = pnlSamples.filter((sample) => sample.pnl > 0);
  const losses = pnlSamples.filter((sample) => sample.pnl < 0);
  const avgWin = wins.length ? wins.reduce((sum, sample) => sum + sample.pnl, 0) / wins.length : null;
  const avgLoss = losses.length ? Math.abs(losses.reduce((sum, sample) => sum + sample.pnl, 0) / losses.length) : null;
  const expectancy = pnlSamples.length
    ? pnlSamples.reduce((sum, sample) => sum + sample.pnl, 0) / pnlSamples.length
    : null;
  const fees = trades
    .map((trade) => finiteNumber(trade.fee))
    .filter((fee): fee is number => fee != null);
  const totalFees = fees.length ? fees.reduce((sum, fee) => sum + fee, 0) : null;
  const annualReturn = finiteNumber(detail?.annualReturn ?? detail?.annual_return);
  const maxDrawdown = finiteNumber(detail?.maxDrawdown ?? detail?.max_drawdown);
  const calmarRatio = annualReturn != null && maxDrawdown != null && maxDrawdown > 0
    ? annualReturn / maxDrawdown
    : null;

  return {
    equityCurve,
    sortinoRatio: sortinoRatioFromEquityCurve(equityCurve),
    calmarRatio,
    avgWinPct: avgWin,
    avgLossPct: avgLoss,
    expectancy,
    totalFees,
  };
}

function timeframeMs(timeframe: string): number {
  const normalized = String(timeframe || '').trim().toLowerCase();
  const amount = Number.parseInt(normalized, 10) || 1;
  if (normalized.endsWith('m')) return amount * 60_000;
  if (normalized.endsWith('h')) return amount * 60 * 60_000;
  if (normalized.endsWith('d')) return amount * 24 * 60 * 60_000;
  return 60 * 60_000;
}

function tradeRecordMarkerLabel(side: string): 'B' | 'S' {
  const normalized = side.toLowerCase();
  if (
    normalized.includes('close_short') ||
    normalized.includes('buy') ||
    (normalized.includes('long') && !normalized.includes('close_long'))
  ) {
    return 'B';
  }
  if (
    normalized.includes('close_long') ||
    normalized.includes('sell') ||
    normalized.includes('short') ||
    normalized === 's'
  ) {
    return 'S';
  }
  return 'B';
}

function buildBacktestTradeMarkers(
  trades: TradeRecord[],
  symbol: string,
  strategyId: number,
  strategyName: string,
): WatchTradeMarker[] {
  return trades
    .filter((trade) =>
      trade.symbol === symbol &&
      Number.isFinite(trade.timestamp) &&
      Number.isFinite(trade.price),
    )
    .sort((a, b) => a.timestamp - b.timestamp)
    .map((trade, index) => ({
      id: index + 1,
      label: tradeRecordMarkerLabel(trade.side),
      side: trade.side,
      action: trade.side,
      symbol,
      price: trade.price,
      quantity: trade.quantity,
      timestamp: trade.timestamp,
      datetime: new Date(trade.timestamp).toLocaleString('zh-CN', { hour12: false }),
      sourceStrategyId: strategyId,
      sourceStrategyName: strategyName,
      subscriptionId: 0,
      liveOrderId: null,
      clientOrderId: null,
    }));
}

function normalizeBacktestKline(value: any): Kline | null {
  const timestamp = Number(value?.timestamp);
  const open = Number(value?.open);
  const high = Number(value?.high);
  const low = Number(value?.low);
  const close = Number(value?.close);
  const volume = Number(value?.volume ?? 0);
  if (![timestamp, open, high, low, close].every(Number.isFinite)) return null;
  return { timestamp, open, high, low, close, volume: Number.isFinite(volume) ? volume : 0 };
}

function historyDetailToBacktestResult(detail: any, strategyName: string): BacktestResult {
  const trades = normalizeHistoryTrades(detail?.trades);
  const derivedMetrics = deriveBacktestHistoryMetrics(detail, trades);
  const winningTrades = trades.filter((trade) => (trade.pnl || 0) > 0).length;
  const losingTrades = trades.filter((trade) => (trade.pnl || 0) < 0).length;
  return {
    id: Number(detail?.id),
    strategyId: Number(detail?.strategyId),
    strategyName,
    status: String(detail?.status || 'completed'),
    timeframe: detail?.timeframe,
    timeframeMode: detail?.timeframeMode,
    matrixResults: Array.isArray(detail?.matrixResults) ? detail.matrixResults : undefined,
    startDate: detail?.startDate,
    endDate: detail?.endDate,
    initialCapital: finiteNumber(detail?.initialCapital) ?? 0,
    finalCapital: finiteNumber(detail?.finalCapital) ?? undefined,
    totalReturn: finiteNumber(detail?.totalReturn) ?? undefined,
    annualReturn: finiteNumber(detail?.annualReturn) ?? undefined,
    maxDrawdown: finiteNumber(detail?.maxDrawdown) ?? undefined,
    sharpeRatio: finiteNumber(detail?.sharpeRatio) ?? undefined,
    sortinoRatio: finiteNumber(detail?.sortinoRatio) ?? derivedMetrics.sortinoRatio ?? undefined,
    calmarRatio: finiteNumber(detail?.calmarRatio) ?? derivedMetrics.calmarRatio ?? undefined,
    winRate: finiteNumber(detail?.winRate) ?? undefined,
    profitFactor: finiteNumber(detail?.profitFactor) ?? undefined,
    totalTrades: finiteNumber(detail?.totalTrades) ?? trades.length,
    winningTrades,
    losingTrades,
    avgWinPct: finiteNumber(detail?.avgWinPct) ?? derivedMetrics.avgWinPct ?? undefined,
    avgLossPct: finiteNumber(detail?.avgLossPct) ?? derivedMetrics.avgLossPct ?? undefined,
    expectancy: finiteNumber(detail?.expectancy) ?? derivedMetrics.expectancy ?? undefined,
    totalFees: finiteNumber(detail?.totalFees) ?? derivedMetrics.totalFees ?? undefined,
    avgHoldingBars: finiteNumber(detail?.avgHoldingBars) ?? undefined,
    equityCurve: derivedMetrics.equityCurve,
    trades,
    createdAt: detail?.createdAt,
    isHistorical: true,
  };
}

function normalizeBacktestHistoryStatus(status: string): BacktestInstanceStatus {
  const value = String(status || '').toLowerCase();
  if (value === 'failed') return 'failed';
  if (value === 'cancelled' || value === 'canceled') return 'cancelled';
  if (value === 'running' || value === 'pending') return 'running';
  return 'completed';
}

function nullableMetricIdentity(value: unknown): string {
  const n = finiteNumber(value);
  return n == null ? '-' : n.toFixed(8);
}

function backtestHistorySignature(item: BacktestHistoryItem): string {
  return [
    Number(item.strategyId || 0),
    item.startDate || '',
    item.endDate || '',
    nullableMetricIdentity(item.initialCapital),
    nullableMetricIdentity(item.totalReturn),
    nullableMetricIdentity(item.totalTrades),
  ].join('|');
}

function backtestHistoryIdentity(item: BacktestHistoryItem): string {
  return `result:${item.id}`;
}

function backtestInstanceHistoryIdentities(instance: BacktestInstance): string[] {
  const identities: string[] = [];
  const resultId = finiteNumber(instance.historyId ?? instance.result?.id);
  if (resultId != null) identities.push(`result:${resultId}`);
  if (instance.result) {
    identities.push([
      Number(instance.result.strategyId || instance.config.selectedStrategy || 0),
      instance.result.startDate || instance.config.startDate || '',
      instance.result.endDate || instance.config.endDate || '',
      nullableMetricIdentity(instance.result.initialCapital || instance.config.initialCapital),
      nullableMetricIdentity(instance.result.totalReturn),
      nullableMetricIdentity(instance.result.totalTrades),
    ].join('|'));
  }
  return identities;
}

function historyItemToBacktestInstance(item: BacktestHistoryItem, strategyName: string): BacktestInstance {
  const createdAt = item.createdAt || new Date(0).toISOString();
  const matrixTimeframes = Array.isArray(item.matrixResults)
    ? uniqueBacktestTimeframes(item.matrixResults.map((result) => result.timeframe))
    : [];
  const itemTimeframe = normalizeBacktestTimeframe(item.timeframe);
  const itemTimeframeMode: BacktestTimeframeMode =
    item.timeframeMode === 'matrix'
      ? 'matrix'
      : item.timeframeMode === 'single'
        ? 'single'
        : 'strategy';
  return {
    id: `history-${item.id}`,
    name: strategyName,
    status: normalizeBacktestHistoryStatus(item.status),
    config: {
      selectedStrategy: Number(item.strategyId),
      startDate: item.startDate,
      endDate: item.endDate,
      initialCapital: item.initialCapital,
      timeframeMode: itemTimeframeMode,
      timeframe: itemTimeframe,
      timeframes: matrixTimeframes.length > 0 ? matrixTimeframes : itemTimeframe ? [itemTimeframe] : [],
      makerFeeBps: null,
      takerFeeBps: null,
      slippageBps: null,
    },
    activeJobId: null,
    resumeJobId: null,
    jobProgress: null,
    result: {
      id: item.id,
      strategyId: Number(item.strategyId),
      strategyName,
      status: item.status || 'completed',
      startDate: item.startDate,
      endDate: item.endDate,
      initialCapital: item.initialCapital,
      finalCapital: item.finalCapital ?? undefined,
      totalReturn: item.totalReturn ?? undefined,
      annualReturn: item.annualReturn ?? undefined,
      maxDrawdown: item.maxDrawdown ?? undefined,
      sharpeRatio: item.sharpeRatio ?? undefined,
      winRate: item.winRate ?? undefined,
      profitFactor: item.profitFactor ?? undefined,
      totalTrades: item.totalTrades ?? undefined,
      timeframe: itemTimeframe ?? undefined,
      timeframeMode: itemTimeframeMode,
      matrixResults: Array.isArray(item.matrixResults) ? item.matrixResults : undefined,
      createdAt,
      isHistorical: true,
    },
    benchmarkKlines: [],
    errorMessage: null,
    historyId: item.id,
    isPersistedHistory: true,
    createdAt,
    updatedAt: createdAt,
  };
}

function backtestHistoryItemFromInstance(instance: BacktestInstance): BacktestHistoryItem | null {
  const resultId = finiteNumber(instance.historyId ?? instance.result?.id);
  const strategyId = finiteNumber(instance.result?.strategyId ?? instance.config.selectedStrategy);
  if (resultId == null || strategyId == null) return null;
  return {
    id: resultId,
    strategyId,
    startDate: instance.result?.startDate || instance.config.startDate,
    endDate: instance.result?.endDate || instance.config.endDate,
    initialCapital: instance.result?.initialCapital || instance.config.initialCapital,
    finalCapital: instance.result?.finalCapital ?? null,
    totalReturn: instance.result?.totalReturn ?? null,
    annualReturn: instance.result?.annualReturn ?? null,
    maxDrawdown: instance.result?.maxDrawdown ?? null,
    sharpeRatio: instance.result?.sharpeRatio ?? null,
    winRate: instance.result?.winRate ?? null,
    profitFactor: instance.result?.profitFactor ?? null,
    totalTrades: instance.result?.totalTrades ?? null,
    status: instance.result?.status || instance.status,
    createdAt: instance.result?.createdAt || instance.createdAt,
  };
}

function backtestInstanceLogs(instance: BacktestInstance): string[] {
  const logs: string[] = [];
  if (instance.errorMessage) logs.push(`[ERROR] ${instance.errorMessage}`);
  if (instance.result?.errorMessage) logs.push(`[ERROR] ${instance.result.errorMessage}`);
  if (instance.status === 'failed' && logs.length === 0) logs.push('[ERROR] 回测失败，后端未返回详细错误。');
  if (instance.status === 'interrupted') logs.push('[WARN] 回测任务已中断，可继续回测。');
  if (instance.status === 'cancelled') logs.push('[INFO] 回测任务已停止。');
  if (instance.status === 'completed') logs.push('[INFO] 回测已完成。');
  if (instance.status === 'running') logs.push('[INFO] 回测正在后台执行。');
  if (instance.activeJobId) logs.push(`[JOB] activeJobId=${instance.activeJobId}`);
  if (instance.resumeJobId) logs.push(`[JOB] resumeJobId=${instance.resumeJobId}`);
  const progress = instance.jobProgress;
  if (progress && progress.totalBars > 0) {
    const percent = progress.percent != null ? `, ${progress.percent.toFixed(1)}%` : '';
    logs.push(`[PROGRESS] ${progress.currentBar}/${progress.totalBars}${percent}`);
  }
  return logs.length > 0 ? logs : ['[INFO] 暂无回测日志。'];
}

function backtestStatusDialogContent(instance: BacktestInstance): string {
  const meta = backtestInstanceStatusMeta(instance.status);
  const progress = instance.jobProgress && instance.jobProgress.totalBars > 0
    ? `${instance.jobProgress.currentBar} / ${instance.jobProgress.totalBars}` +
      (instance.jobProgress.percent != null ? ` (${instance.jobProgress.percent.toFixed(1)}%)` : '')
    : '无进行中进度';
  return [
    `状态：${meta.label}`,
    `策略：${instance.name}`,
    `区间：${instance.config.startDate} 至 ${instance.config.endDate}`,
    `初始资金：${instance.config.initialCapital}`,
    `当前任务：${instance.activeJobId || '-'}`,
    `可继续任务：${instance.resumeJobId || '-'}`,
    `进度：${progress}`,
    instance.errorMessage ? `错误：${instance.errorMessage}` : '',
  ].filter(Boolean).join('\n');
}

function dateToStartMs(date: string): number {
  return new Date(`${date}T00:00:00`).getTime();
}

function dateToEndMs(date: string): number {
  return new Date(`${date}T23:59:59.999`).getTime();
}

function filterEquityByTimeRange(equityCurve: EquityPoint[], timeRange: TimeRange): EquityPoint[] {
  if (!equityCurve || equityCurve.length === 0) return [];
  if (timeRange === 'all') return equityCurve;

  const lastTs = equityCurve[equityCurve.length - 1].timestamp;
  const cutoff = new Date(lastTs);
  switch (timeRange) {
    case '1m': cutoff.setMonth(cutoff.getMonth() - 1); break;
    case '3m': cutoff.setMonth(cutoff.getMonth() - 3); break;
    case '6m': cutoff.setMonth(cutoff.getMonth() - 6); break;
    case '1y': cutoff.setFullYear(cutoff.getFullYear() - 1); break;
  }
  return equityCurve.filter((p) => p.timestamp >= cutoff.getTime());
}

function closestBenchmarkClose(sortedKlines: { timestamp: number; close: number }[], timestamp: number): number | null {
  if (sortedKlines.length === 0) return null;
  let closest = sortedKlines[0];
  for (const k of sortedKlines) {
    if (Math.abs(k.timestamp - timestamp) < Math.abs(closest.timestamp - timestamp)) {
      closest = k;
    }
  }
  return closest.close > 0 ? closest.close : null;
}

function buildBacktestChartData(
  equityCurve: EquityPoint[],
  initialCapital: number,
  timeRange: TimeRange,
  benchmarkKlines: { timestamp: number; close: number }[] = [],
): BacktestChartData | null {
  const filtered = filterEquityByTimeRange(equityCurve, timeRange);
  if (filtered.length === 0) return null;

  const sortedKlines = [...benchmarkKlines]
    .filter((k) => Number.isFinite(k.timestamp) && Number.isFinite(k.close) && k.close > 0)
    .sort((a, b) => a.timestamp - b.timestamp);

  const displayData =
    timeRange === 'all' &&
    initialCapital > 0 &&
    sortedKlines.length > 0 &&
    filtered[0].timestamp > sortedKlines[0].timestamp
      ? [{ timestamp: sortedKlines[0].timestamp, equity: initialCapital, drawdown: 0 }, ...filtered]
      : filtered;

  const firstEquity = displayData[0].equity;
  const baseEquity = timeRange === 'all' && initialCapital > 0 ? initialCapital : firstEquity;
  const benchmarkBaseClose =
    sortedKlines.length > 1
      ? timeRange === 'all'
        ? sortedKlines[0].close
        : closestBenchmarkClose(sortedKlines, displayData[0].timestamp)
      : null;

  const dates = displayData.map((p) => {
    const d = new Date(p.timestamp);
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
  });
  const strategyReturn = displayData.map((p) => ((p.equity - baseEquity) / baseEquity) * 100);
  const drawdown = displayData.map((p) => -(p.drawdown || 0));
  const benchmarkReturn = displayData.map((p): ChartValue => {
    if (benchmarkBaseClose == null || benchmarkBaseClose <= 0) return null;
    const close = closestBenchmarkClose(sortedKlines, p.timestamp);
    return close == null ? null : ((close - benchmarkBaseClose) / benchmarkBaseClose) * 100;
  });
  const relativeReturn = strategyReturn.map((sr, i): ChartValue => {
    const br = benchmarkReturn[i];
    return br == null ? null : sr - br;
  });

  return {
    dates,
    strategyReturn,
    benchmarkReturn,
    relativeReturn,
    drawdown,
    rangeReturn: strategyReturn[strategyReturn.length - 1] ?? null,
    rangeBenchmarkReturn: benchmarkReturn[benchmarkReturn.length - 1] ?? null,
    rangeMaxDrawdown: drawdown.length ? Math.max(...drawdown.map((v) => Math.abs(v))) : null,
  };
}

function annualizedVolatilityFromEquityCurve(equityCurve: EquityPoint[]): number | null {
  if (!equityCurve || equityCurve.length < 2) return null;
  const dailyEquity = new Map<string, number>();
  [...equityCurve]
    .filter((point) => Number.isFinite(point.timestamp) && Number.isFinite(point.equity) && point.equity > 0)
    .sort((a, b) => a.timestamp - b.timestamp)
    .forEach((point) => {
      dailyEquity.set(new Date(point.timestamp).toISOString().slice(0, 10), point.equity);
    });
  const values = [...dailyEquity.values()];
  if (values.length < 3) return null;
  const returns: number[] = [];
  for (let i = 1; i < values.length; i++) {
    if (values[i - 1] > 0) returns.push((values[i] - values[i - 1]) / values[i - 1]);
  }
  if (returns.length < 2) return null;
  const mean = returns.reduce((sum, value) => sum + value, 0) / returns.length;
  const variance = returns.reduce((sum, value) => sum + (value - mean) ** 2, 0) / returns.length;
  return Math.sqrt(variance) * Math.sqrt(365) * 100;
}

function backtestDurationDays(result: BacktestResult): number | null {
  if (!result.startDate || !result.endDate) return null;
  const start = dateToStartMs(result.startDate);
  const end = dateToEndMs(result.endDate);
  if (!Number.isFinite(start) || !Number.isFinite(end) || end < start) return null;
  return Math.max(1, (end - start) / 86_400_000);
}

function buildCryptoBacktestPerformanceMetrics(result: BacktestResult): CryptoBacktestPerformanceMetrics {
  const durationDays = backtestDurationDays(result);
  const annualizedVolatility = annualizedVolatilityFromEquityCurve(result.equityCurve || []);
  const tradePnlSamples = deriveBacktestTradePnlSamples(result.trades || [], result.totalTrades);
  const winningPnlSamples = tradePnlSamples.filter((sample) => sample.pnl > 0);
  const losingPnlSamples = tradePnlSamples.filter((sample) => sample.pnl < 0);
  const avgWinningPnl = winningPnlSamples.length
    ? winningPnlSamples.reduce((sum, sample) => sum + sample.pnl, 0) / winningPnlSamples.length
    : null;
  const avgLosingPnl = losingPnlSamples.length
    ? Math.abs(losingPnlSamples.reduce((sum, sample) => sum + sample.pnl, 0) / losingPnlSamples.length)
    : null;
  const derivedTotalFees = result.trades?.length
    ? result.trades.reduce((sum, trade) => sum + (finiteNumber(trade.fee) ?? 0), 0)
    : null;
  const totalFees = result.totalFees ?? derivedTotalFees;
  const feeDragPct =
    totalFees != null && result.initialCapital > 0
      ? (totalFees / result.initialCapital) * 100
      : null;
  const sortinoRatio = result.sortinoRatio ?? sortinoRatioFromEquityCurve(result.equityCurve || []);
  const calmarRatio =
    result.calmarRatio ??
    (result.annualReturn != null && result.maxDrawdown != null && result.maxDrawdown > 0
      ? result.annualReturn / result.maxDrawdown
      : null);
  const payoffRatio =
    result.avgWinPct != null && result.avgLossPct != null && result.avgLossPct !== 0
      ? Math.abs(result.avgWinPct / result.avgLossPct)
      : avgWinningPnl != null && avgLosingPnl != null && avgLosingPnl > 0
        ? avgWinningPnl / avgLosingPnl
        : null;
  const expectancy =
    result.expectancy != null
      ? result.expectancy
      : tradePnlSamples.length
        ? tradePnlSamples.reduce((sum, sample) => sum + sample.pnl, 0) / tradePnlSamples.length
        : null;
  const expectancyPct =
    expectancy != null && result.initialCapital > 0
      ? (expectancy / result.initialCapital) * 100
      : null;
  const totalTrades = result.totalTrades ?? result.trades?.length ?? null;
  const tradeFrequencyPerDay =
    totalTrades != null && durationDays != null && durationDays > 0
      ? totalTrades / durationDays
      : null;
  return {
    annualizedVolatility,
    sortinoRatio,
    calmarRatio,
    feeDragPct,
    payoffRatio,
    expectancy,
    expectancyPct,
    tradeFrequencyPerDay,
    durationDays,
  };
}

function backtestSortDirectionFor(
  sortMode: BacktestSortMode,
  field: BacktestSortField,
): BacktestSortDirection | null {
  const activeDirection: BacktestSortDirection = sortMode.endsWith('_asc') ? 'asc' : 'desc';
  const activeField = sortMode.slice(0, activeDirection === 'asc' ? -4 : -5) as BacktestSortField;
  return activeField === field ? activeDirection : null;
}

function defaultBacktestSortDirection(field: BacktestSortField): BacktestSortDirection {
  return field === 'drawdown' ? 'asc' : 'desc';
}

function nextBacktestSortMode(sortMode: BacktestSortMode, field: BacktestSortField): BacktestSortMode {
  const currentDirection = backtestSortDirectionFor(sortMode, field);
  const nextDirection = currentDirection
    ? currentDirection === 'desc' ? 'asc' : 'desc'
    : defaultBacktestSortDirection(field);
  return `${field}_${nextDirection}` as BacktestSortMode;
}

function backtestApiSortBy(sortMode: BacktestSortMode): 'created' | 'return' | 'drawdown' | 'win_rate' {
  return sortMode.endsWith('_asc')
    ? sortMode.slice(0, -4) as 'created' | 'return' | 'drawdown' | 'win_rate'
    : sortMode.slice(0, -5) as 'created' | 'return' | 'drawdown' | 'win_rate';
}

function backtestApiSortDir(sortMode: BacktestSortMode): 'asc' | 'desc' {
  return sortMode.endsWith('_asc') ? 'asc' : 'desc';
}

function compareNullableBacktestMetric(
  left: number | null,
  right: number | null,
  direction: BacktestSortDirection,
): number {
  if (left == null && right == null) return 0;
  if (left == null) return 1;
  if (right == null) return -1;
  return direction === 'desc' ? right - left : left - right;
}

function BacktestSortArrow({ direction }: { direction: BacktestSortDirection | null }) {
  if (direction === 'asc') return <ArrowUp className="h-3.5 w-3.5" />;
  if (direction === 'desc') return <ArrowDown className="h-3.5 w-3.5" />;
  return <ArrowDownUp className="h-3.5 w-3.5 opacity-60" />;
}

// ============================================
// 回测实例向导阶段
// ============================================
function BacktestWizardStep({ step, title, desc, state, isLast }: {
  step: number;
  title: string;
  desc: string;
  state: 'done' | 'active' | 'pending';
  isLast: boolean;
}) {
  const done = state === 'done';
  const active = state === 'active';

  return (
    <div className="relative flex items-center gap-3 md:flex-col md:items-center md:text-center">
      {!isLast && (
        <div className="pointer-events-none absolute left-7 top-[1.65rem] h-10 w-px bg-crypto-border md:left-[calc(50%+2.75rem)] md:top-7 md:h-px md:w-[calc(100%-5.5rem)]">
          <div
            className={clsx(
              'h-full w-full transition-colors md:h-px',
              done ? 'bg-purple-500/70' : 'bg-crypto-border',
            )}
          />
        </div>
      )}
      <div
        className={clsx(
          'relative z-10 flex h-14 w-14 shrink-0 items-center justify-center rounded-full border-4 text-lg font-bold tabular-nums transition-colors',
          active && 'border-purple-500 bg-purple-500/20 text-purple-100 shadow-[0_0_0_4px_rgba(168,85,247,0.14)]',
          done && 'border-green-500/50 bg-green-500/15 text-green-300',
          state === 'pending' && 'border-crypto-border bg-crypto-bg text-gray-600',
        )}
      >
        {step}
      </div>
      <div className="min-w-0">
        <div
          className={clsx(
            'text-sm font-bold',
            active && 'text-white',
            done && 'text-green-300',
            state === 'pending' && 'text-gray-500',
          )}
        >
          {title}
        </div>
        <div className="mt-1 text-xs text-gray-600">{desc}</div>
      </div>
    </div>
  );
}

// ============================================
// 主组件
// ============================================
export default function Backtest() {
  const { strategies, fetchStrategies } = useStore();
  const [initialBt] = useState(loadBacktestPrefs);
  const [view, setView] = useState<BacktestView>('dashboard');
  const [isCreateModalOpen, setIsCreateModalOpen] = useState(false);
  const [createStep, setCreateStep] = useState<1 | 2 | 3>(1);
  const [createDraft, setCreateDraft] = useState<BacktestInstanceConfig>(() =>
    createBacktestDraft({
      selectedStrategy: initialBt?.selectedStrategy ?? null,
      startDate: initialBt?.startDate,
      initialCapital: initialBt?.initialCapital,
    }),
  );
  const [strategySearchQuery, setStrategySearchQuery] = useState('');
  const [instanceSearchQuery, setInstanceSearchQuery] = useState('');
  const [instanceAssetFilter, setInstanceAssetFilter] = useState<HistoryAssetFilter>('all');
  const [instanceStatusFilter, setInstanceStatusFilter] = useState<BacktestStatusFilter>('all');
  const [instanceSortMode, setInstanceSortMode] = useState<BacktestSortMode>('created_desc');
  const [historyDetailResult, setHistoryDetailResult] = useState<BacktestResult | null>(null);
  const [historyBenchmarkKlines, setHistoryBenchmarkKlines] = useState<{ timestamp: number; close: number }[]>([]);
  const [backtestInstances, setBacktestInstances] = useState<BacktestInstance[]>(() =>
    loadBacktestInstances(initialBt),
  );
  const [selectedInstanceId, setSelectedInstanceId] = useState<string>(() => {
    try {
      return localStorage.getItem(SELECTED_BACKTEST_INSTANCE_KEY) || '';
    } catch {
      return '';
    }
  });
  const instancesRef = useRef<BacktestInstance[]>(backtestInstances);
  const selectedInstanceIdRef = useRef<string>(selectedInstanceId);

  // 结果 Tab & 时间范围
  const [resultTab, setResultTab] = useState<ResultTab>('overview');
  const [timeRange, setTimeRange] = useState<TimeRange>('all');
  const [activeMatrixTimeframe, setActiveMatrixTimeframe] = useState('');
  const [historyItems, setHistoryItems] = useState<BacktestHistoryItem[]>([]);
  const [isLoadingHistory, setIsLoadingHistory] = useState(false);
  const [isLoadingMoreHistory, setIsLoadingMoreHistory] = useState(false);
  const [historyHasMore, setHistoryHasMore] = useState(false);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [selectedHistoryId, setSelectedHistoryId] = useState<number | null>(null);
  const [deletingHistoryId, setDeletingHistoryId] = useState<number | null>(null);
  const [historyDeleteTarget, setHistoryDeleteTarget] = useState<BacktestHistoryDeleteTarget | null>(null);
  const [isDeletingHistoryBatch, setIsDeletingHistoryBatch] = useState(false);
  const [backtestStatusTarget, setBacktestStatusTarget] = useState<BacktestInstance | null>(null);
  const [backtestLogTarget, setBacktestLogTarget] = useState<BacktestInstance | null>(null);
  const [localBacktestDeleteTarget, setLocalBacktestDeleteTarget] = useState<BacktestInstance | null>(null);
  const [cancelBacktestTarget, setCancelBacktestTarget] = useState<BacktestInstance | null>(null);

  const [themeAlert, setThemeAlert] = useState<{
    open: boolean;
    title: string;
    content: string;
    tone?: ThemeAlertTone;
  }>({ open: false, title: '', content: '' });

  const showThemeAlert = (title: string, content: string, tone?: ThemeAlertTone) => {
    setThemeAlert({ open: true, title, content, tone: tone ?? 'danger' });
  };

  const backtestableStrategies = useMemo(
    () => strategies.filter(strategyIsBacktestSelectable),
    [strategies],
  );
  const filteredBacktestStrategyOptions = useMemo(
    () => backtestableStrategies
      .filter((strategy) => strategyMatchesBacktestSearch(strategy, strategySearchQuery))
      .slice(0, 60),
    [backtestableStrategies, strategySearchQuery],
  );
  const selectedInstance = useMemo(() => {
    return (
      backtestInstances.find((instance) => instance.id === selectedInstanceId) ||
      backtestInstances[0] ||
      null
    );
  }, [backtestInstances, selectedInstanceId]);
  const selectedStrategy = selectedInstance?.config.selectedStrategy ?? null;
  const startDate = selectedInstance?.config.startDate ?? defaultBacktestDateRange().start;
  const endDate = selectedInstance?.config.endDate ?? defaultBacktestDateRange().end;
  const initialCapital = selectedInstance?.config.initialCapital ?? 10000;
  const isCancelling = selectedInstance?.status === 'cancelling';
  const isRunning = selectedInstance?.status === 'running' || isCancelling;
  const jobProgress = selectedInstance?.jobProgress ?? null;
  const baseResult = historyDetailResult ?? selectedInstance?.result ?? null;
  const matrixPeriodResults = useMemo(() => {
    if (!baseResult || !Array.isArray(baseResult.matrixResults) || baseResult.matrixResults.length === 0) {
      return [];
    }
    const seen = new Set<string>();
    return baseResult.matrixResults
      .filter((item) => {
        const key = item.timeframe || item.status || String(seen.size);
        if (seen.has(key)) return false;
        seen.add(key);
        return true;
      })
      .map((item): BacktestResult => ({
        ...baseResult,
        ...item,
        id: baseResult.id,
        strategyId: item.strategyId ?? baseResult.strategyId,
        strategyName: item.strategyName || baseResult.strategyName,
        status: item.status || baseResult.status,
        timeframe: item.timeframe || baseResult.timeframe,
        timeframeMode: baseResult.timeframeMode,
        startDate: item.startDate || baseResult.startDate,
        endDate: item.endDate || baseResult.endDate,
        initialCapital: item.initialCapital ?? baseResult.initialCapital,
        isHistorical: baseResult.isHistorical,
        matrixResults: undefined,
      }));
  }, [baseResult]);
  const activeMatrixResult = useMemo(() => {
    if (matrixPeriodResults.length === 0) return null;
    return (
      matrixPeriodResults.find((item) => item.timeframe === activeMatrixTimeframe) ||
      matrixPeriodResults.find((item) => item.timeframe === baseResult?.timeframe) ||
      matrixPeriodResults[0]
    );
  }, [activeMatrixTimeframe, baseResult?.timeframe, matrixPeriodResults]);
  const result = activeMatrixResult ?? baseResult;
  const benchmarkKlines = historyDetailResult ? historyBenchmarkKlines : selectedInstance?.benchmarkKlines ?? [];
  const todayDate = todayDateInputValue();
  useEffect(() => {
    if (matrixPeriodResults.length === 0) {
      if (activeMatrixTimeframe) setActiveMatrixTimeframe('');
      return;
    }
    const preferred =
      matrixPeriodResults.find((item) => item.timeframe === activeMatrixTimeframe)?.timeframe ||
      matrixPeriodResults.find((item) => item.timeframe === baseResult?.timeframe)?.timeframe ||
      matrixPeriodResults[0]?.timeframe ||
      '';
    if (preferred !== activeMatrixTimeframe) setActiveMatrixTimeframe(preferred);
  }, [activeMatrixTimeframe, baseResult?.timeframe, matrixPeriodResults]);
  const displayedTrades = useMemo(() => {
    const trades = result?.trades;
    if (!trades?.length) return [];
    return [...trades].sort((a, b) => b.timestamp - a.timestamp).slice(0, 100);
  }, [result?.trades]);
  const selectedStrategyInfo = useMemo(
    () => backtestableStrategies.find((s) => Number(s.id) === Number(selectedStrategy)) || null,
    [selectedStrategy, backtestableStrategies],
  );
  const resultStrategyInfo = useMemo(
    () => backtestableStrategies.find((s) => Number(s.id) === Number(result?.strategyId ?? selectedStrategy)) || null,
    [result?.strategyId, selectedStrategy, backtestableStrategies],
  );
  const feedSymbols = useMemo(() => strategySymbols(selectedStrategyInfo), [selectedStrategyInfo]);
  const tradeSymbols = useMemo(() => strategyTradeSymbols(selectedStrategyInfo), [selectedStrategyInfo]);
  const benchmarkSymbol = strategyBenchmarkSymbol();
  const selectedStrategyTimeframe = useMemo(() => strategyTimeframe(selectedStrategyInfo), [selectedStrategyInfo]);
  const resultStrategyTimeframe = useMemo(
    () => result?.timeframe || strategyTimeframe(resultStrategyInfo || selectedStrategyInfo) || '1h',
    [result?.timeframe, resultStrategyInfo, selectedStrategyInfo],
  );
  const selectedStrategyTimeframeLabel = selectedStrategyInfo
    ? selectedStrategyTimeframe || '未定义'
    : '请选择策略';
  const tradeChartSymbols = useMemo(() => {
    const symbols = new Set<string>();
    (result?.trades || []).forEach((trade) => {
      if (trade.symbol) symbols.add(trade.symbol);
    });
    return Array.from(symbols);
  }, [result?.trades]);
  const [selectedTradeChartSymbol, setSelectedTradeChartSymbol] = useState('');
  const [tradeChartKlines, setTradeChartKlines] = useState<Kline[]>([]);
  const [tradeChartLoading, setTradeChartLoading] = useState(false);
  const [tradeChartError, setTradeChartError] = useState('');
  const tradeChartMarkers = useMemo(
    () => buildBacktestTradeMarkers(
      result?.trades || [],
      selectedTradeChartSymbol,
      Number(result?.strategyId ?? selectedStrategy ?? 0),
      result?.strategyName || '回测策略',
    ),
    [result?.trades, result?.strategyId, result?.strategyName, selectedStrategy, selectedTradeChartSymbol],
  );
  useEffect(() => {
    if (tradeChartSymbols.length === 0) {
      setSelectedTradeChartSymbol('');
      return;
    }
    if (!selectedTradeChartSymbol || !tradeChartSymbols.includes(selectedTradeChartSymbol)) {
      setSelectedTradeChartSymbol(tradeChartSymbols[0]);
    }
  }, [selectedTradeChartSymbol, tradeChartSymbols]);
  useEffect(() => {
    if ((resultTab !== 'overview' && resultTab !== 'trades') || !selectedTradeChartSymbol || tradeChartMarkers.length === 0) {
      setTradeChartKlines([]);
      setTradeChartError('');
      return;
    }

    const timestamps = tradeChartMarkers.map((marker) => Number(marker.timestamp)).filter(Number.isFinite);
    if (!timestamps.length) return;

    const barMs = timeframeMs(resultStrategyTimeframe);
    const tradeChartStart = Math.max(0, Math.min(...timestamps) - barMs * 30);
    const tradeChartEnd = Math.max(...timestamps) + barMs * 30;
    let cancelled = false;

    setTradeChartLoading(true);
    setTradeChartError('');
    marketApi.getKlines('okx', selectedTradeChartSymbol, resultStrategyTimeframe, 1000, tradeChartStart, tradeChartEnd)
      .then((rows) => {
        if (cancelled) return;
        setTradeChartKlines((rows || []).map(normalizeBacktestKline).filter(Boolean) as Kline[]);
      })
      .catch((error: any) => {
        if (cancelled) return;
        setTradeChartKlines([]);
        setTradeChartError(error?.response?.data?.detail || error?.message || '读取回测 K 线失败');
      })
      .finally(() => {
        if (!cancelled) setTradeChartLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [resultTab, selectedTradeChartSymbol, tradeChartMarkers, resultStrategyTimeframe]);
  const symbolScopeLabel =
    tradeSymbols.length > 0
      ? `交易子池: ${symbolSummary(tradeSymbols)}`
      : symbolSummary(feedSymbols);
  const draftStrategyInfo = useMemo(
    () => backtestableStrategies.find((s) => Number(s.id) === Number(createDraft.selectedStrategy)) || null,
    [backtestableStrategies, createDraft.selectedStrategy],
  );
  const draftCostDefaults = useMemo(() => strategyBacktestCostDefaults(draftStrategyInfo), [draftStrategyInfo]);
  const draftEffectiveMakerFeeBps = createDraft.makerFeeBps ?? draftCostDefaults.makerFeeBps;
  const draftEffectiveTakerFeeBps = createDraft.takerFeeBps ?? draftCostDefaults.takerFeeBps;
  const draftEffectiveSlippageBps = createDraft.slippageBps ?? draftCostDefaults.slippageBps;
  const draftAssetClass = strategyAssetClass(draftStrategyInfo);
  const historicalBacktestInstances = useMemo(() => {
    const localIdentities = new Set(backtestInstances.flatMap(backtestInstanceHistoryIdentities));
    return historyItems
      .filter((item) => {
        const identity = backtestHistoryIdentity(item);
        const signature = backtestHistorySignature(item);
        return !localIdentities.has(identity) && !localIdentities.has(signature);
      })
      .map((item) => historyItemToBacktestInstance(item, strategyNameById(strategies, item.strategyId)));
  }, [backtestInstances, historyItems, strategies]);
  const unifiedBacktestInstances = useMemo(
    () => [...backtestInstances, ...historicalBacktestInstances],
    [backtestInstances, historicalBacktestInstances],
  );
  const shouldRenderBacktestInstances =
    unifiedBacktestInstances.length > 0 || isLoadingHistory || Boolean(historyError);
  const instanceAssetCounts = useMemo(() => {
    const counts: Record<HistoryAssetFilter, number> = { all: unifiedBacktestInstances.length, spot: 0, contract: 0 };
    unifiedBacktestInstances.forEach((instance) => {
      counts[backtestInstanceAssetClass(backtestableStrategies, instance)] += 1;
    });
    return counts;
  }, [unifiedBacktestInstances, backtestableStrategies]);
  const instanceStatusCounts = useMemo(() => {
    const counts: Record<BacktestStatusFilter, number> = {
      all: unifiedBacktestInstances.length,
      running: 0,
      completed: 0,
      failed: 0,
      cancelled: 0,
    };
    unifiedBacktestInstances.forEach((instance) => {
      const bucket = backtestInstanceStatusBucket(instance.status);
      if (bucket !== 'all') counts[bucket] += 1;
    });
    return counts;
  }, [unifiedBacktestInstances]);
  const filteredBacktestInstances = useMemo(() => {
    const filtered = unifiedBacktestInstances.filter((instance) => {
      const strategyInfoForInstance = backtestableStrategies.find((s) => Number(s.id) === Number(instance.config.selectedStrategy));
      const assetMatches =
        instanceAssetFilter === 'all' ||
        backtestInstanceAssetClass(backtestableStrategies, instance) === instanceAssetFilter;
      const statusMatches =
        instanceStatusFilter === 'all' ||
        backtestInstanceStatusBucket(instance.status) === instanceStatusFilter;
      const searchMatches = backtestInstanceMatchesSearch(
        instance,
        backtestableStrategies,
        strategyInfoForInstance,
        instanceSearchQuery,
      );
      return assetMatches && statusMatches && searchMatches;
    });
    return [...filtered].sort((a, b) => {
      if (instanceSortMode === 'created_asc' || instanceSortMode === 'created_desc') {
        const left = new Date(a.createdAt).getTime() || 0;
        const right = new Date(b.createdAt).getTime() || 0;
        return instanceSortMode === 'created_desc' ? right - left : left - right;
      }
      const sortBy = backtestApiSortBy(instanceSortMode);
      const sortDir = backtestApiSortDir(instanceSortMode);
      const metricGetter =
        sortBy === 'drawdown'
          ? backtestInstanceDrawdown
          : sortBy === 'win_rate'
            ? backtestInstanceWinRate
            : backtestInstanceReturn;
      const metricOrder = compareNullableBacktestMetric(metricGetter(a), metricGetter(b), sortDir);
      if (metricOrder !== 0) return metricOrder;
      const leftCreated = new Date(a.createdAt).getTime() || 0;
      const rightCreated = new Date(b.createdAt).getTime() || 0;
      return rightCreated - leftCreated;
    });
  }, [
    unifiedBacktestInstances,
    backtestableStrategies,
    instanceSearchQuery,
    instanceAssetFilter,
    instanceStatusFilter,
    instanceSortMode,
  ]);

  const updateInstance = useCallback((
    instanceId: string,
    updater: (instance: BacktestInstance) => BacktestInstance,
  ) => {
    setBacktestInstances((prev) =>
      prev.map((instance) =>
        instance.id === instanceId
          ? { ...updater(instance), updatedAt: new Date().toISOString() }
          : instance,
      ),
    );
  }, []);

  const addBacktestInstance = () => {
    setCreateDraft(createBacktestDraft({
      startDate,
      initialCapital,
    }));
    setStrategySearchQuery('');
    setCreateStep(1);
    setIsCreateModalOpen(true);
  };

  const updateCreateDraft = (patch: Partial<BacktestInstanceConfig>) => {
    setCreateDraft((prev) => {
      const strategyChanged =
        Object.prototype.hasOwnProperty.call(patch, 'selectedStrategy') &&
        Number(patch.selectedStrategy ?? 0) !== Number(prev.selectedStrategy ?? 0);
      const next = { ...prev, ...patch };
      if (strategyChanged) {
        next.makerFeeBps = null;
        next.takerFeeBps = null;
        next.slippageBps = null;
      }
      if (patch.startDate != null) {
        next.startDate = clampIsoDateToToday(patch.startDate, prev.startDate);
      }
      if (patch.endDate != null) {
        next.endDate = clampIsoDateToToday(patch.endDate, prev.endDate);
      }
      if (next.startDate > todayDateInputValue()) {
        next.startDate = todayDateInputValue();
      }
      if (next.endDate > todayDateInputValue()) {
        next.endDate = todayDateInputValue();
      }
      return next;
    });
  };

  const applyQuickRange = (months: number) => {
    updateCreateDraft(quickDateRange(months));
  };

  const openInstanceDetail = (instanceId: string) => {
    setSelectedInstanceId(instanceId);
    setHistoryDetailResult(null);
    setResultTab('overview');
    setTimeRange('all');
    setView('detail');
  };

  const openBacktestRecordDetail = (instance: BacktestInstance) => {
    if (instance.isPersistedHistory && instance.historyId != null) {
      void loadHistoryDetail(instance.historyId);
      return;
    }
    openInstanceDetail(instance.id);
  };

  const deleteBacktestInstance = (instanceId: string) => {
    const target = backtestInstances.find((instance) => instance.id === instanceId);
    if (!target || target.status === 'running' || target.status === 'cancelling') return;
    setLocalBacktestDeleteTarget(target);
  };

  const confirmDeleteLocalBacktestInstance = () => {
    const target = localBacktestDeleteTarget;
    if (!target || target.status === 'running' || target.status === 'cancelling') {
      setLocalBacktestDeleteTarget(null);
      return;
    }
    const rest = backtestInstances.filter((instance) => instance.id !== target.id);
    const next = rest.map((instance, index) => ({
      ...instance,
      name: instance.name || `回测实例 ${index + 1}`,
    }));
    setBacktestInstances(next);
    if (selectedInstanceId === target.id) {
      setSelectedInstanceId(next[0]?.id || '');
      setHistoryDetailResult(null);
      setView('dashboard');
    }
    try {
      if (target.activeJobId && sessionStorage.getItem(ACTIVE_BACKTEST_JOB_KEY) === target.activeJobId) {
        sessionStorage.removeItem(ACTIVE_BACKTEST_JOB_KEY);
      }
    } catch {
      /* ignore */
    }
    setLocalBacktestDeleteTarget(null);
  };

  const deleteBacktestUnifiedRecord = (instance: BacktestInstance) => {
    if (instance.status === 'running' || instance.status === 'cancelling') return;
    const persistedItem =
      (instance.historyId != null
        ? historyItems.find((item) => Number(item.id) === Number(instance.historyId))
        : null) ||
      backtestHistoryItemFromInstance(instance);
    if (persistedItem) {
      deleteBacktestHistory(persistedItem);
      return;
    }
    deleteBacktestInstance(instance.id);
  };

  const fetchBenchmarkKlinesForResult = useCallback(async (
    backtestResult: BacktestResult,
    fallbackConfig: BacktestInstanceConfig,
  ) => {
    const benchmark = strategyBenchmarkSymbol();
    const benchmarkStartDate = backtestResult.startDate && ISO_DATE.test(backtestResult.startDate)
      ? backtestResult.startDate
      : fallbackConfig.startDate;
    const benchmarkEndDate = backtestResult.endDate && ISO_DATE.test(backtestResult.endDate)
      ? backtestResult.endDate
      : fallbackConfig.endDate;

    try {
      const klinesRes = await marketApi.getKlines(
        'okx',
        benchmark,
        '1d',
        1000,
        dateToStartMs(benchmarkStartDate),
        dateToEndMs(benchmarkEndDate),
      );
      return (klinesRes || []).map((k: any) => ({
        timestamp: k.timestamp,
        close: k.close,
      }));
    } catch {
      return [];
    }
  }, []);

  const loadBacktestHistory = useCallback(async (options?: { reset?: boolean; offset?: number }) => {
    const reset = options?.reset ?? true;
    const offset = reset ? 0 : Math.max(0, options?.offset ?? 0);
    if (reset) {
      setIsLoadingHistory(true);
    } else {
      setIsLoadingMoreHistory(true);
    }
    setHistoryError(null);
    try {
      const items = await backtestApi.getResults({
        limit: BACKTEST_HISTORY_PAGE_SIZE + 1,
        offset,
        query: instanceSearchQuery.trim(),
        sortBy: backtestApiSortBy(instanceSortMode),
        sortDir: backtestApiSortDir(instanceSortMode),
        includeMatrixSummary: false,
      });
      const pageItems = (items as BacktestHistoryItem[]).slice(0, BACKTEST_HISTORY_PAGE_SIZE);
      setHistoryHasMore(items.length > BACKTEST_HISTORY_PAGE_SIZE);
      setHistoryItems((prev) => {
        if (reset) return pageItems;
        const seen = new Set(prev.map((item) => String(item.id)));
        return [...prev, ...pageItems.filter((item) => !seen.has(String(item.id)))];
      });
    } catch (error: any) {
      setHistoryError(String(error?.response?.data?.detail || error?.message || '加载回测记录失败'));
    } finally {
      if (reset) {
        setIsLoadingHistory(false);
      } else {
        setIsLoadingMoreHistory(false);
      }
    }
  }, [instanceSearchQuery, instanceSortMode]);

  const loadHistoryDetail = async (historyId: number) => {
    setSelectedHistoryId(historyId);
    setHistoryBenchmarkKlines([]);
    try {
      const detail = await backtestApi.getResult(historyId);
      const strategyName = strategyNameById(strategies, Number(detail.strategyId));
      const detailResult = historyDetailToBacktestResult(detail, strategyName);
      setHistoryDetailResult(detailResult);
      setView('detail');
      setResultTab('overview');
      setTimeRange('all');
      const historyBenchmarkConfig: BacktestInstanceConfig = {
        selectedStrategy: Number(detailResult.strategyId) || null,
        startDate: detailResult.startDate || defaultBacktestDateRange().start,
        endDate: detailResult.endDate || defaultBacktestDateRange().end,
        initialCapital: detailResult.initialCapital || 10000,
        timeframeMode: 'strategy',
        timeframe: null,
        timeframes: [],
        makerFeeBps: null,
        takerFeeBps: null,
        slippageBps: null,
      };
      const benchmark = await fetchBenchmarkKlinesForResult(detailResult, historyBenchmarkConfig);
      setHistoryBenchmarkKlines(benchmark);
    } catch (error: any) {
      showThemeAlert(
        '回测详情加载失败',
        String(error?.response?.data?.detail || error?.message || '未知错误'),
        'danger',
      );
    } finally {
      setSelectedHistoryId(null);
    }
  };

  const deleteBacktestHistory = (item: BacktestHistoryItem) => {
    setHistoryDeleteTarget({ mode: 'single', items: [item] });
  };

  const confirmDeleteBacktestHistory = async () => {
    if (!historyDeleteTarget || isDeletingHistoryBatch) return;
    const ids = historyDeleteTarget.items.map((item) => item.id);
    const idSet = new Set(ids);
    setDeletingHistoryId(ids.length === 1 ? ids[0] : null);
    setIsDeletingHistoryBatch(true);
    try {
      await Promise.all(ids.map((id) => backtestApi.deleteResult(id)));
      setHistoryItems((prev) => prev.filter((history) => !idSet.has(history.id)));
      setBacktestInstances((prev) =>
        prev.filter((instance) => !idSet.has(Number(instance.historyId ?? instance.result?.id))),
      );
      if (historyDetailResult?.isHistorical && idSet.has(Number(historyDetailResult.id))) {
        setHistoryDetailResult(null);
        setView('dashboard');
        setResultTab('overview');
        setTimeRange('all');
      }
      setHistoryDeleteTarget(null);
    } catch (error: any) {
      showThemeAlert(
        '删除回测记录失败',
        String(error?.response?.data?.detail || error?.message || '未知错误'),
        'danger',
      );
    } finally {
      setDeletingHistoryId(null);
      setIsDeletingHistoryBatch(false);
    }
  };

  useEffect(() => { fetchStrategies(); }, []);

  useEffect(() => {
    instancesRef.current = backtestInstances;
  }, [backtestInstances]);

  useEffect(() => {
    selectedInstanceIdRef.current = selectedInstance?.id || selectedInstanceId;
    if (!selectedInstance && backtestInstances[0]) {
      setSelectedInstanceId(backtestInstances[0].id);
    } else if (!selectedInstance && !backtestInstances[0] && selectedInstanceId) {
      setSelectedInstanceId('');
    }
  }, [backtestInstances, selectedInstance, selectedInstanceId]);

  useEffect(() => {
    void loadBacktestHistory();
  }, [loadBacktestHistory]);

  useEffect(() => {
    const recoverableInstances = backtestInstances.filter(
      (instance) =>
        (instance.status === 'interrupted' || instance.status === 'failed') &&
        !instance.resumeJobId &&
        !instance.activeJobId &&
        instance.config.selectedStrategy,
    );
    if (recoverableInstances.length === 0) return;

    let cancelled = false;
    const recoverJobs = async () => {
      try {
        const jobs = await backtestApi.getJobs({
          status: 'interrupted,failed,pending,running,cancelling',
          limit: 100,
        });
        if (cancelled) return;
        setBacktestInstances((prev) => {
          let changed = false;
          const usedJobIds = new Set(prev.map((instance) => instance.resumeJobId || instance.activeJobId).filter(Boolean));
          const next = prev.map((instance) => {
            if (
              !(
                (instance.status === 'interrupted' || instance.status === 'failed') &&
                !instance.resumeJobId &&
                !instance.activeJobId &&
                instance.config.selectedStrategy
              )
            ) {
              return instance;
            }
            const matched = jobs.find((job) => {
              if (!job.resumable || usedJobIds.has(job.jobId)) return false;
              return backtestRequestMatchesInstance(job.request, instance);
            });
            if (!matched) return instance;
            usedJobIds.add(matched.jobId);
            changed = true;
            return {
              ...instance,
              resumeJobId: matched.jobId,
              jobProgress: matched.totalBars > 0
                ? {
                    currentBar: matched.currentBar,
                    totalBars: matched.totalBars,
                    percent: matched.percent,
                  }
                : instance.jobProgress,
              errorMessage: instance.errorMessage || matched.message || matched.errorMessage || null,
            };
          });
          return changed ? next : prev;
        });
      } catch {
        /* 恢复入口是增强能力，失败不阻断页面渲染 */
      }
    };

    void recoverJobs();
    return () => {
      cancelled = true;
    };
  }, [backtestInstances]);

  useEffect(() => {
    if (strategies.length === 0) return;
    const selectableIds = new Set(backtestableStrategies.map((s) => Number(s.id)));
    setBacktestInstances((prev) => {
      let changed = false;
      const next = prev.map((instance) => {
        const strategyId = instance.config.selectedStrategy;
        if (!strategyId || selectableIds.has(Number(strategyId))) return instance;
        changed = true;
        return {
          ...instance,
          status: (instance.status === 'running' ? instance.status : 'idle') as BacktestInstanceStatus,
          config: { ...instance.config, selectedStrategy: null },
          result: null,
          benchmarkKlines: [],
          errorMessage: '原策略已不再适合回测，请重新选择策略。',
          updatedAt: new Date().toISOString(),
        };
      });
      return changed ? next : prev;
    });
  }, [strategies.length, backtestableStrategies]);

  useEffect(() => {
    try {
      localStorage.setItem(
        BACKTEST_INSTANCES_KEY,
        JSON.stringify({
          v: 1,
          instances: persistableBacktestInstances(backtestInstances),
        }),
      );
      if (selectedInstance?.id) {
        localStorage.setItem(SELECTED_BACKTEST_INSTANCE_KEY, selectedInstance.id);
      } else {
        localStorage.removeItem(SELECTED_BACKTEST_INSTANCE_KEY);
      }
      const payload: BacktestPrefsV1 = {
        v: 1,
        selectedStrategy,
        startDate,
        initialCapital,
      };
      localStorage.setItem(BACKTEST_PREFS_KEY, JSON.stringify(payload));
      const firstRunningJob = backtestInstances.find((instance) => instance.status === 'running' && instance.activeJobId)
        ?.activeJobId;
      if (firstRunningJob) {
        sessionStorage.setItem(ACTIVE_BACKTEST_JOB_KEY, firstRunningJob);
      } else {
        sessionStorage.removeItem(ACTIVE_BACKTEST_JOB_KEY);
      }
    } catch {
      /* ignore */
    }
  }, [
    backtestInstances,
    selectedInstance?.id,
    selectedStrategy,
    startDate,
    initialCapital,
  ]);

  const runningJobKey = useMemo(
    () =>
      backtestInstances
        .filter((instance) => (instance.status === 'running' || instance.status === 'cancelling') && instance.activeJobId)
        .map((instance) => `${instance.id}:${instance.activeJobId}`)
        .join('|'),
    [backtestInstances],
  );

  useEffect(() => {
    if (!runningJobKey) return;
    let cancelled = false;

    const tick = async () => {
      const runningInstances = instancesRef.current.filter(
        (instance) => (instance.status === 'running' || instance.status === 'cancelling') && instance.activeJobId,
      );
      await Promise.all(runningInstances.map(async (instance) => {
        if (cancelled || !instance.activeJobId) return;
        try {
          const st = await backtestApi.getJob(instance.activeJobId);
          if (cancelled) return;

          const nextProgress =
            st.totalBars > 0
              ? { currentBar: st.currentBar, totalBars: st.totalBars, percent: st.percent }
              : { currentBar: 0, totalBars: 0, percent: null };

          if (st.status === 'completed' && st.result) {
            const benchmark = await fetchBenchmarkKlinesForResult(st.result, instance.config);
            if (cancelled) return;
            updateInstance(instance.id, (current) => ({
              ...current,
              status: 'completed',
              activeJobId: null,
              resumeJobId: null,
              jobProgress: null,
              result: st.result,
              benchmarkKlines: benchmark,
              errorMessage: null,
            }));
            if (selectedInstanceIdRef.current === instance.id) {
              setResultTab('overview');
              setTimeRange('all');
            }
            void loadBacktestHistory();
            return;
          }

          if (st.status === 'cancelled') {
            updateInstance(instance.id, (current) => ({
              ...current,
              status: 'cancelled',
              activeJobId: null,
              resumeJobId: null,
              jobProgress: nextProgress,
              errorMessage: st.message || '回测已停止',
            }));
            if (selectedInstanceIdRef.current === instance.id) {
              showThemeAlert('回测已停止', st.message || '用户已停止回测', 'warning');
            }
            return;
          }

          if (st.status === 'failed' || st.status === 'interrupted') {
            const hint =
              st.status === 'interrupted' && st.totalBars > 0
                ? `最后进度: ${st.currentBar} / ${st.totalBars}` +
                  (st.percent != null ? `（${st.percent.toFixed(1)}%）` : '')
                : '';
            const message =
              st.status === 'failed'
                ? st.errorMessage || '未知错误'
                : [st.message, hint].filter(Boolean).join('。') || '任务已结束';
            updateInstance(instance.id, (current) => ({
              ...current,
              status: st.status as BacktestInstanceStatus,
              activeJobId: null,
              resumeJobId: st.resumable === false ? null : instance.activeJobId,
              jobProgress: null,
              errorMessage: message,
            }));
            if (selectedInstanceIdRef.current === instance.id) {
              showThemeAlert(st.status === 'failed' ? '回测失败' : '回测已中断', message, st.status === 'failed' ? 'danger' : 'warning');
            }
            return;
          }

          updateInstance(instance.id, (current) => ({
            ...current,
            status: st.status === 'cancelling' ? 'cancelling' : 'running',
            jobProgress: nextProgress,
            errorMessage: null,
          }));
        } catch (err: any) {
          if (cancelled) return;
          const message = String(err?.response?.data?.detail || err?.message || '任务查询失败');
          updateInstance(instance.id, (current) => ({
            ...current,
            status: 'failed',
            activeJobId: null,
            resumeJobId: instance.activeJobId,
            jobProgress: null,
            errorMessage: message,
          }));
          if (selectedInstanceIdRef.current === instance.id) {
            showThemeAlert('回测任务查询失败', message, 'danger');
          }
        }
      }));
    };

    void tick();
    const id = window.setInterval(() => void tick(), 1000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [runningJobKey, updateInstance, fetchBenchmarkKlinesForResult, loadBacktestHistory]);

  const runBacktest = async (configOverride?: BacktestInstanceConfig) => {
    const runConfig = configOverride ?? selectedInstance?.config;
    if (!runConfig?.selectedStrategy) {
      showThemeAlert('提示', '请选择策略', 'warning');
      return;
    }
    const dateError = backtestDateValidationMessage(runConfig);
    if (dateError) {
      showThemeAlert('回测日期无效', dateError, 'warning');
      return;
    }

    let instanceId = selectedInstance?.id || '';
    if (configOverride) {
      const strategyInfo = backtestableStrategies.find((s) => Number(s.id) === Number(runConfig.selectedStrategy));
      const next = {
        ...createBacktestInstance(runConfig, backtestInstances.length + 1),
        name: strategyInfo?.name || `回测实例 ${backtestInstances.length + 1}`,
        status: 'running' as BacktestInstanceStatus,
      };
      instanceId = next.id;
      setBacktestInstances((prev) => [next, ...prev]);
      setSelectedInstanceId(next.id);
      setHistoryDetailResult(null);
      setView('dashboard');
      setIsCreateModalOpen(false);
      setResultTab('overview');
      setTimeRange('all');
    } else {
      if (!selectedInstance) {
        showThemeAlert('提示', '请先新增回测实例', 'warning');
        return;
      }
      if (selectedInstance.status === 'running' || selectedInstance.status === 'cancelling') {
        showThemeAlert('提示', '当前实例已有回测在运行，可切换其它实例继续配置新任务。', 'warning');
        return;
      }
      updateInstance(instanceId, (instance) => ({
        ...instance,
        status: 'running',
        activeJobId: null,
        resumeJobId: null,
        jobProgress: null,
        result: null,
        benchmarkKlines: [],
        errorMessage: null,
      }));
    }

    const strategyInfo = backtestableStrategies.find((s) => Number(s.id) === Number(runConfig.selectedStrategy));
    const defaults = strategyBacktestCostDefaults(strategyInfo);
    const effectiveMakerFeeBps = runConfig.makerFeeBps ?? defaults.makerFeeBps;
    const effectiveTakerFeeBps = runConfig.takerFeeBps ?? defaults.takerFeeBps;
    const effectiveSlippageBps = runConfig.slippageBps ?? defaults.slippageBps;
    const effectiveTimeframes = backtestEffectiveTimeframes(runConfig, strategyInfo);
    const effectiveTimeframe = backtestEffectiveTimeframe(runConfig, strategyInfo);

    try {
      const { jobId } = await backtestApi.runJob({
        strategy_id: runConfig.selectedStrategy,
        exchange: 'okx',
        timeframe_mode: runConfig.timeframeMode,
        timeframe: effectiveTimeframe,
        timeframes: runConfig.timeframeMode === 'matrix' ? effectiveTimeframes : undefined,
        start_date: runConfig.startDate,
        end_date: runConfig.endDate,
        initial_capital: runConfig.initialCapital,
        maker_fee_bps: effectiveMakerFeeBps,
        taker_fee_bps: effectiveTakerFeeBps,
        slippage_bps: effectiveSlippageBps,
      });
      try {
        sessionStorage.setItem(ACTIVE_BACKTEST_JOB_KEY, jobId);
      } catch {
        /* ignore */
      }
      updateInstance(instanceId, (instance) => ({
        ...instance,
        status: 'running',
        activeJobId: jobId,
        resumeJobId: null,
        jobProgress: { currentBar: 0, totalBars: 0, percent: null },
      }));
    } catch (error: any) {
      console.error('Backtest job start failed:', error);
      try {
        sessionStorage.removeItem(ACTIVE_BACKTEST_JOB_KEY);
      } catch {
        /* ignore */
      }
      const message = String(
        error.response?.data?.error?.message ||
          error.response?.data?.detail ||
          error.message,
      );
      updateInstance(instanceId, (instance) => ({
        ...instance,
        status: 'failed',
        activeJobId: null,
        resumeJobId: null,
        jobProgress: null,
        errorMessage: message,
      }));
      showThemeAlert('无法启动回测', message, 'danger');
    }
  };

  const cancelBacktestInstance = (instanceId: string) => {
    const target = backtestInstances.find((instance) => instance.id === instanceId);
    if (!target?.activeJobId || target.status === 'cancelling') return;
    setCancelBacktestTarget(target);
  };

  const confirmCancelBacktestInstance = async () => {
    const target = cancelBacktestTarget;
    if (!target?.activeJobId || target.status === 'cancelling') {
      setCancelBacktestTarget(null);
      return;
    }
    setCancelBacktestTarget(null);
    const instanceId = target.id;

    updateInstance(instanceId, (instance) => ({
      ...instance,
      status: 'cancelling',
      errorMessage: null,
    }));

    try {
      const st = await backtestApi.cancelJob(target.activeJobId);
      updateInstance(instanceId, (instance) => ({
        ...instance,
        status: st.status === 'cancelled' ? 'cancelled' : 'cancelling',
        activeJobId: st.status === 'cancelled' ? null : instance.activeJobId,
        jobProgress: {
          currentBar: st.currentBar || 0,
          totalBars: st.totalBars || 0,
          percent: st.percent ?? null,
        },
        errorMessage: st.message || (st.status === 'cancelled' ? '回测已停止' : null),
      }));
      if (st.status === 'cancelled') {
        try {
          if (sessionStorage.getItem(ACTIVE_BACKTEST_JOB_KEY) === target.activeJobId) {
            sessionStorage.removeItem(ACTIVE_BACKTEST_JOB_KEY);
          }
        } catch {
          /* ignore */
        }
      }
    } catch (error: any) {
      const message = String(error?.response?.data?.detail || error?.message || '停止回测失败');
      updateInstance(instanceId, (instance) => ({
        ...instance,
        status: 'running',
        errorMessage: message,
      }));
      showThemeAlert('停止回测失败', message, 'danger');
    }
  };

  const resumeBacktestInstance = async (instanceId: string) => {
    const target = backtestInstances.find((instance) => instance.id === instanceId);
    const jobId = target?.resumeJobId || target?.activeJobId;
    if (!target || target.status === 'running' || target.status === 'cancelling') return;
    if (!jobId && !target.config.selectedStrategy) {
      showThemeAlert('继续回测失败', '缺少策略配置，无法继续回测', 'warning');
      return;
    }
    const dateError = backtestDateValidationMessage(target.config);
    if (dateError) {
      showThemeAlert('回测日期无效', dateError, 'warning');
      return;
    }

    updateInstance(instanceId, (instance) => ({
      ...instance,
      status: 'running',
      activeJobId: jobId || null,
      resumeJobId: null,
      jobProgress: instance.jobProgress || { currentBar: 0, totalBars: 0, percent: null },
      errorMessage: null,
    }));

    try {
      if (!jobId) {
        const strategyInfo = backtestableStrategies.find((s) => Number(s.id) === Number(target.config.selectedStrategy));
        const defaults = strategyBacktestCostDefaults(strategyInfo);
        const effectiveTimeframes = backtestEffectiveTimeframes(target.config, strategyInfo);
        const effectiveTimeframe = backtestEffectiveTimeframe(target.config, strategyInfo);
        const { jobId: newJobId } = await backtestApi.runJob({
          strategy_id: target.config.selectedStrategy,
          exchange: 'okx',
          timeframe_mode: target.config.timeframeMode,
          timeframe: effectiveTimeframe,
          timeframes: target.config.timeframeMode === 'matrix' ? effectiveTimeframes : undefined,
          start_date: target.config.startDate,
          end_date: target.config.endDate,
          initial_capital: target.config.initialCapital,
          maker_fee_bps: target.config.makerFeeBps ?? defaults.makerFeeBps,
          taker_fee_bps: target.config.takerFeeBps ?? defaults.takerFeeBps,
          slippage_bps: target.config.slippageBps ?? defaults.slippageBps,
        });
        try {
          sessionStorage.setItem(ACTIVE_BACKTEST_JOB_KEY, newJobId);
        } catch {
          /* ignore */
        }
        updateInstance(instanceId, (instance) => ({
          ...instance,
          status: 'running',
          activeJobId: newJobId,
          resumeJobId: null,
          jobProgress: { currentBar: 0, totalBars: 0, percent: null },
          result: null,
          benchmarkKlines: [],
          errorMessage: null,
        }));
        return;
      }

      const st = await backtestApi.resumeJob(jobId);
      try {
        sessionStorage.setItem(ACTIVE_BACKTEST_JOB_KEY, st.jobId || jobId);
      } catch {
        /* ignore */
      }
      updateInstance(instanceId, (instance) => ({
        ...instance,
        status: st.status === 'cancelling' ? 'cancelling' : 'running',
        activeJobId: st.jobId || jobId,
        resumeJobId: null,
        jobProgress: {
          currentBar: st.currentBar || 0,
          totalBars: st.totalBars || 0,
          percent: st.percent ?? null,
        },
        errorMessage: null,
      }));
    } catch (error: any) {
      const message = String(error?.response?.data?.detail || error?.message || '继续回测失败');
      updateInstance(instanceId, (instance) => ({
        ...instance,
        status: target.status,
        activeJobId: null,
        resumeJobId: jobId || null,
        errorMessage: message,
      }));
      showThemeAlert('继续回测失败', message, 'danger');
    }
  };

  const fmt = (n: number | undefined | null, d = 2) => n == null ? '-' : n.toFixed(d);
  const fmtPct = (n: number | undefined | null) => n == null ? '-' : `${n >= 0 ? '+' : ''}${n.toFixed(2)}%`;

  const hasResult = result && result.status === 'completed';
  const resultAssetClass = backtestResultAssetClass(strategies, result, selectedStrategy);
  const chartRangeStats = useMemo(() => {
    if (!hasResult || !result?.equityCurve?.length) return null;
    return buildBacktestChartData(
      result.equityCurve,
      result.initialCapital || initialCapital,
      timeRange,
      benchmarkKlines,
    );
  }, [hasResult, result, initialCapital, timeRange, benchmarkKlines]);
  const renderBacktestKlineReview = ({
    height = 520,
    showRangeStats = false,
  }: { height?: number; showRangeStats?: boolean } = {}) => (
    <section className="rounded-xl border border-crypto-border bg-crypto-bg/45 p-4">
      <div className="mb-3 flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
        <div>
          <h4 className="text-sm font-semibold text-white">买卖点 K线复盘</h4>
          <p className="mt-1 text-xs text-gray-500">
            复用盯盘 K 线风格展示回测成交 B/S 点，K 线按策略周期读取真实历史行情。
          </p>
        </div>
        <div className="flex flex-col gap-2 lg:items-end">
          {showRangeStats && (
            <div className="flex flex-wrap items-center gap-4 text-xs">
              <span className="text-gray-500">
                区间收益 <span className={clsx('font-bold', (chartRangeStats?.rangeReturn ?? result?.totalReturn ?? 0) >= 0 ? 'text-up' : 'text-down')}>
                  {fmtPct(chartRangeStats?.rangeReturn ?? result?.totalReturn)}
                </span>
              </span>
              <span className="text-gray-500">
                区间最大回撤 <span className="font-bold text-down">{fmt(chartRangeStats?.rangeMaxDrawdown ?? result?.maxDrawdown)}%</span>
              </span>
            </div>
          )}
          {tradeChartSymbols.length > 1 && (
            <div className="flex flex-wrap gap-1 rounded-lg border border-crypto-border bg-crypto-card p-1">
              {tradeChartSymbols.map((symbol) => (
                <button
                  key={symbol}
                  type="button"
                  onClick={() => setSelectedTradeChartSymbol(symbol)}
                  className={clsx(
                    'rounded-md px-2.5 py-1.5 text-xs font-semibold transition-colors',
                    selectedTradeChartSymbol === symbol
                      ? 'bg-blue-600/25 text-blue-200'
                      : 'text-gray-500 hover:text-gray-200',
                  )}
                >
                  {symbol}
                </button>
              ))}
            </div>
          )}
        </div>
      </div>
      {tradeChartLoading ? (
        <div style={{ height }} className="flex items-center justify-center text-sm text-gray-500">
          <Loader2 className="mr-2 h-4 w-4 animate-spin text-blue-400" />
          正在加载回测 K 线...
        </div>
      ) : tradeChartError ? (
        <div className="flex h-[220px] items-center justify-center rounded-xl border border-red-500/20 bg-red-500/5 text-sm text-red-300">
          {tradeChartError}
        </div>
      ) : tradeChartKlines.length > 0 && selectedTradeChartSymbol ? (
        <Suspense fallback={<div style={{ height }} className="flex items-center justify-center text-sm text-gray-500">K 线图加载中...</div>}>
          <WatchKlineChart
            data={tradeChartKlines}
            markers={tradeChartMarkers}
            symbol={selectedTradeChartSymbol}
            timeframe={resultStrategyTimeframe}
            height={height}
          />
        </Suspense>
      ) : (
        <div className="flex h-[220px] items-center justify-center rounded-xl border border-crypto-border bg-crypto-card/60 text-sm text-gray-500">
          暂无 K 线复盘数据
        </div>
      )}
    </section>
  );

  // 计算基准收益率和贝塔 (基于benchmarkKlines)
  const benchmarkStats = useMemo(() => {
    if (!hasResult || !benchmarkKlines || benchmarkKlines.length < 2 || !result?.equityCurve?.length) {
      return { benchmarkReturn: null, beta: null, alpha: null };
    }

    const eq = [...result.equityCurve].sort((a, b) => a.timestamp - b.timestamp);
    const rangeStart = dateToStartMs(result.startDate || startDate);
    const rangeEnd = dateToEndMs(result.endDate || endDate);
    const sorted = [...benchmarkKlines]
      .filter((k) => k.timestamp >= rangeStart && k.timestamp <= rangeEnd)
      .sort((a, b) => a.timestamp - b.timestamp);
    if (sorted.length < 2) return { benchmarkReturn: null, beta: null, alpha: null };
    const firstClose = sorted[0].close;
    const lastClose = sorted[sorted.length - 1].close;
    const benchmarkReturn = ((lastClose - firstClose) / firstClose) * 100;

    const strategyTotalReturn =
      result.totalReturn ??
      (eq[0]?.equity > 0 ? ((eq[eq.length - 1].equity - eq[0].equity) / eq[0].equity) * 100 : null);
    const alpha = strategyTotalReturn == null ? null : strategyTotalReturn - benchmarkReturn;

    // 计算贝塔：用同一批 1D 基准 K 线时间点对齐策略权益，Cov(策略日收益, 基准日收益) / Var(基准日收益)
    if (eq.length < 3) return { benchmarkReturn, beta: null, alpha };

    let equityCursor = 0;
    const equityAtOrBefore = (timestamp: number): number | null => {
      while (equityCursor + 1 < eq.length && eq[equityCursor + 1].timestamp <= timestamp) {
        equityCursor += 1;
      }
      return eq[equityCursor]?.timestamp <= timestamp ? eq[equityCursor].equity : null;
    };
    const strategyReturns: number[] = [];
    const benchReturns: number[] = [];
    let prevEquity = equityAtOrBefore(sorted[0].timestamp);
    let prevClose = sorted[0].close;
    for (let i = 1; i < sorted.length; i++) {
      const currentEquity = equityAtOrBefore(sorted[i].timestamp);
      const currentClose = sorted[i].close;
      if (prevEquity != null && currentEquity != null && prevEquity > 0 && prevClose > 0) {
        strategyReturns.push((currentEquity - prevEquity) / prevEquity);
        benchReturns.push((currentClose - prevClose) / prevClose);
      }
      if (currentEquity != null) prevEquity = currentEquity;
      prevClose = currentClose;
    }

    // 协方差和方差
    const n = Math.min(strategyReturns.length, benchReturns.length);
    if (n < 2) return { benchmarkReturn, beta: null, alpha };

    const meanS = strategyReturns.slice(0, n).reduce((a, b) => a + b, 0) / n;
    const meanB = benchReturns.slice(0, n).reduce((a, b) => a + b, 0) / n;
    let cov = 0, varB = 0;
    for (let i = 0; i < n; i++) {
      cov += (strategyReturns[i] - meanS) * (benchReturns[i] - meanB);
      varB += (benchReturns[i] - meanB) ** 2;
    }
    cov /= n;
    varB /= n;

    const beta = varB > 0 ? cov / varB : 0;

    return { benchmarkReturn, beta, alpha };
  }, [hasResult, benchmarkKlines, result, startDate, endDate]);
  const cryptoPerformanceMetrics = useMemo(() => (
    hasResult && result ? buildCryptoBacktestPerformanceMetrics(result) : null
  ), [hasResult, result]);

  const detailStatusMeta = backtestInstanceStatusMeta(
    historyDetailResult ? 'completed' : selectedInstance?.status ?? 'idle',
  );
  const detailStrategyName = result?.strategyName ||
    (selectedStrategy ? strategyNameById(backtestableStrategies, selectedStrategy) : '未选择策略');
  const cryptoMetricGroups = result && cryptoPerformanceMetrics ? [
    {
      title: '收益表现',
      subtitle: '净值增长与基准对比',
      tone: 'green' as const,
      metrics: [
        {
          label: '净收益',
          value: fmtPct(result.totalReturn),
          positive: (result.totalReturn ?? 0) >= 0,
          description: '期末权益相对初始资金的净收益率，已计入本次回测设置的手续费和滑点。',
        },
        {
          label: '年化收益',
          value: fmtPct(result.annualReturn),
          positive: (result.annualReturn ?? 0) >= 0,
          description: '按回测区间长度折算的年化收益；短周期和高波动 crypto 回测只作为归一化参考。',
        },
        {
          label: '基准超额',
          value: benchmarkStats.alpha != null ? fmtPct(benchmarkStats.alpha) : '-',
          positive: (benchmarkStats.alpha ?? 0) >= 0,
          caption: `vs ${benchmarkSymbol}`,
          description: `策略净收益 - ${benchmarkSymbol} 基准收益，衡量是否跑赢同周期买入持有基准；基准缺失时不显示。`,
        },
        {
          label: '期末权益',
          value: result.finalCapital != null ? `$${fmt(result.finalCapital)}` : '-',
          positive: (result.finalCapital ?? 0) >= result.initialCapital,
          description: '回测结束时的账户权益金额，包含已实现盈亏和回测撮合后的资金变化。',
        },
      ] satisfies CryptoMetricItem[],
    },
    {
      title: '风险控制',
      subtitle: '回撤、波动与风险调整',
      tone: 'red' as const,
      metrics: [
        {
          label: '最大回撤',
          value: result.maxDrawdown != null ? `${fmt(result.maxDrawdown)}%` : '-',
          positive: false,
          description: '权益曲线从历史高点到后续低点的最大跌幅；crypto 策略首看该指标控制尾部风险。',
          isDrawdown: true,
        },
        {
          label: 'Calmar',
          value: fmt(cryptoPerformanceMetrics.calmarRatio),
          positive: (cryptoPerformanceMetrics.calmarRatio ?? 0) >= 1,
          description: '年化收益 / 最大回撤，用于判断收益是否足以覆盖回撤风险。',
        },
        {
          label: '年化波动率',
          value: cryptoPerformanceMetrics.annualizedVolatility != null ? `${fmt(cryptoPerformanceMetrics.annualizedVolatility)}%` : '-',
          positive: (cryptoPerformanceMetrics.annualizedVolatility ?? 0) <= 80,
          description: '按 365 天 crypto 连续交易市场年化的日收益波动率，越高代表权益曲线越颠簸。',
        },
        {
          label: '索提诺',
          value: cryptoPerformanceMetrics.sortinoRatio != null ? fmt(cryptoPerformanceMetrics.sortinoRatio) : '-',
          positive: (cryptoPerformanceMetrics.sortinoRatio ?? 0) >= 0,
          description: '只惩罚下行波动的风险调整收益，比夏普更贴近高波动市场里的回撤体验。',
        },
      ] satisfies CryptoMetricItem[],
    },
    {
      title: '交易质量',
      subtitle: '命中率与单笔质量',
      tone: 'blue' as const,
      metrics: [
        {
          label: '胜率',
          value: result.winRate != null ? `${fmt(result.winRate)}%` : '-',
          positive: (result.winRate ?? 0) >= 50,
          description: '盈利交易笔数 / 有盈亏结果的交易笔数，只表示命中率，需要结合盈亏比和赔率一起看。',
        },
        {
          label: '盈亏比',
          value: fmt(result.profitFactor),
          positive: (result.profitFactor ?? 0) >= 1,
          description: '总盈利 / 总亏损绝对值，也称 Profit Factor；大于 1 表示盈利覆盖亏损。',
        },
        {
          label: '赔率',
          value: cryptoPerformanceMetrics.payoffRatio != null ? fmt(cryptoPerformanceMetrics.payoffRatio) : '-',
          positive: (cryptoPerformanceMetrics.payoffRatio ?? 0) >= 1,
          description: '平均盈利幅度 / 平均亏损幅度，用于判断亏损能否被少数大盈利覆盖。',
        },
        {
          label: '期望/笔',
          value: cryptoPerformanceMetrics.expectancy != null ? `$${fmt(cryptoPerformanceMetrics.expectancy)}` : '-',
          positive: (cryptoPerformanceMetrics.expectancy ?? 0) >= 0,
          caption: cryptoPerformanceMetrics.expectancyPct != null ? `${fmtPct(cryptoPerformanceMetrics.expectancyPct)} 初始资金` : undefined,
          description: '每笔交易的平均期望收益金额；同时显示其占初始资金比例，便于比较不同资金规模的回测。',
        },
      ] satisfies CryptoMetricItem[],
    },
    {
      title: '成本与频率',
      subtitle: '手续费压力和交易密度',
      tone: 'amber' as const,
      metrics: [
        {
          label: '总交易',
          value: String(result.totalTrades ?? result.trades?.length ?? 0),
          positive: (result.totalTrades ?? 0) > 0,
          description: '回测期间产生的交易笔数，用于判断样本量是否足够支撑结论。',
        },
        {
          label: '手续费占本金',
          value: cryptoPerformanceMetrics.feeDragPct != null ? `${fmt(cryptoPerformanceMetrics.feeDragPct)}%` : '-',
          positive: (cryptoPerformanceMetrics.feeDragPct ?? 0) <= 2,
          description: '总手续费 / 初始资金。高频 crypto 策略需要重点检查该成本拖累。',
        },
        {
          label: '交易频率',
          value: cryptoPerformanceMetrics.tradeFrequencyPerDay != null ? `${fmt(cryptoPerformanceMetrics.tradeFrequencyPerDay)} 笔/日` : '-',
          positive: true,
          caption: cryptoPerformanceMetrics.durationDays != null ? `${fmt(cryptoPerformanceMetrics.durationDays, 1)} 天` : undefined,
          description: '总交易笔数除以回测天数，用于识别过度交易或样本过少。',
        },
        {
          label: '平均持仓',
          value: result.avgHoldingBars != null ? `${fmt(result.avgHoldingBars)} bars` : '-',
          positive: true,
          description: '平均每笔交易持有的 K 线数量，结合策略周期判断换手和持仓风格。',
        },
      ] satisfies CryptoMetricItem[],
    },
  ] : [];

  return (
    <div className="h-full w-full min-w-0 p-6">
      {view === 'dashboard' ? (
        <div className="space-y-6">
          <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <h1 className="text-xl font-bold text-white flex items-center gap-2">
                <FlaskConical className="w-6 h-6 text-purple-400" />
                回测实例控制台
              </h1>
              <p className="text-sm text-gray-500 mt-1">
                管理多个异步回测实例；创建任务后在卡片中跟踪状态，打开详情查看绩效和成交。
              </p>
            </div>
            <button
              type="button"
              onClick={addBacktestInstance}
              className="inline-flex items-center justify-center gap-2 rounded-xl bg-purple-600 px-5 py-2.5 text-sm font-semibold text-white shadow-lg shadow-purple-900/20 transition-colors hover:bg-purple-700"
            >
              <Plus className="h-4 w-4" />
              创建回测实例
            </button>
          </div>

          {shouldRenderBacktestInstances && (
            <>
              <div className="flex flex-wrap items-center gap-2">
                <div className="inline-flex items-center rounded-xl border border-crypto-border bg-crypto-card p-1">
                  {HISTORY_ASSET_FILTERS.map((option) => {
                    const active = instanceAssetFilter === option.value;
                    return (
                      <button
                        key={option.value}
                        type="button"
                        aria-pressed={active}
                        onClick={() => setInstanceAssetFilter(option.value)}
                        className={clsx(
                          'inline-flex min-w-20 items-center justify-center gap-2 rounded-lg px-3 py-1.5 text-xs font-semibold transition-colors',
                          active && option.value === 'all' && 'bg-blue-500/20 text-blue-300',
                          active && option.value === 'spot' && 'bg-yellow-500/15 text-yellow-300',
                          active && option.value === 'contract' && 'bg-purple-500/15 text-purple-300',
                          !active && 'text-gray-500 hover:bg-white/5 hover:text-gray-300',
                        )}
                      >
                        <span>{option.label}</span>
                        <span className={clsx('rounded-md px-1.5 py-0.5 text-[10px]', active ? 'bg-black/20' : 'bg-crypto-bg text-gray-500')}>
                          {instanceAssetCounts[option.value] ?? 0}
                        </span>
                      </button>
                    );
                  })}
                </div>
                <div className="inline-flex items-center rounded-xl border border-crypto-border bg-crypto-card p-1">
                  {BACKTEST_STATUS_FILTERS.map((option) => {
                    const active = instanceStatusFilter === option.value;
                    return (
                      <button
                        key={option.value}
                        type="button"
                        aria-pressed={active}
                        onClick={() => setInstanceStatusFilter(option.value)}
                        className={clsx(
                          'inline-flex min-w-20 items-center justify-center gap-2 rounded-lg px-3 py-1.5 text-xs font-semibold transition-colors',
                          active ? 'bg-purple-500/20 text-purple-200' : 'text-gray-500 hover:bg-white/5 hover:text-gray-300',
                        )}
                      >
                        <span>{option.label}</span>
                        <span className={clsx('rounded-md px-1.5 py-0.5 text-[10px]', active ? 'bg-purple-400/15 text-purple-100' : 'bg-crypto-bg text-gray-500')}>
                          {instanceStatusCounts[option.value] ?? 0}
                        </span>
                      </button>
                    );
                  })}
                </div>
                <div className="inline-flex h-11 items-center gap-1 rounded-xl border border-crypto-border bg-crypto-card/80 p-1">
                  {BACKTEST_SORT_CONTROLS.map((control) => {
                    const direction = backtestSortDirectionFor(instanceSortMode, control.field);
                    const active = direction !== null;
                    return (
                      <button
                        key={control.field}
                        type="button"
                        aria-pressed={active}
                        onClick={() => setInstanceSortMode(nextBacktestSortMode(instanceSortMode, control.field))}
                        className={clsx(
                          'inline-flex h-9 items-center justify-center gap-1.5 rounded-lg px-3 text-xs font-semibold transition-colors',
                          active
                            ? 'bg-purple-500/20 text-purple-200 ring-1 ring-purple-400/20'
                            : 'text-gray-500 hover:bg-white/5 hover:text-gray-300',
                        )}
                      >
                        <span>{control.label}</span>
                        <BacktestSortArrow direction={direction} />
                      </button>
                    );
                  })}
                </div>
              </div>

              <div className="rounded-xl border border-crypto-border bg-crypto-card p-4">
                <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
                  <div className="flex min-w-0 flex-1 flex-wrap items-center gap-3">
                    <h2 className="flex shrink-0 items-center gap-2 text-sm font-semibold text-white">
                      <Layers className="h-4 w-4 text-purple-400" />
                      回测实例
                      <span className="text-[11px] font-normal text-gray-500">{filteredBacktestInstances.length} / {unifiedBacktestInstances.length} 个</span>
                    </h2>
                    <div className="relative min-w-[240px] flex-1 sm:max-w-sm lg:max-w-md">
                      <Search className="pointer-events-none absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-500" />
                      <input
                        type="search"
                        value={instanceSearchQuery}
                        onChange={(event) => setInstanceSearchQuery(event.target.value)}
                        placeholder="搜索回测实例、策略、标的、周期..."
                        className="h-10 w-full rounded-xl border border-crypto-border bg-crypto-bg/60 pl-10 pr-10 text-sm text-white outline-none transition-colors placeholder:text-gray-600 focus:border-blue-500/70 focus:ring-2 focus:ring-blue-500/10"
                        aria-label="搜索回测实例"
                      />
                      {instanceSearchQuery && (
                        <button
                          type="button"
                          aria-label="清空回测实例搜索"
                          onClick={() => setInstanceSearchQuery('')}
                          className="absolute right-2 top-1/2 inline-flex h-7 w-7 -translate-y-1/2 items-center justify-center rounded-lg text-gray-500 transition-colors hover:bg-white/5 hover:text-gray-200"
                        >
                          <X className="h-3.5 w-3.5" />
                        </button>
                      )}
                    </div>
                  </div>
                  <button
                    type="button"
                    onClick={() => void loadBacktestHistory({ reset: true })}
                    disabled={isLoadingHistory || isLoadingMoreHistory}
                    className="inline-flex items-center gap-1.5 rounded-lg border border-crypto-border px-3 py-2 text-xs text-gray-300 transition-colors hover:border-blue-500/60 hover:text-white disabled:cursor-not-allowed disabled:opacity-40"
                  >
                    <RefreshCw className={clsx('h-3.5 w-3.5', isLoadingHistory && 'animate-spin')} />
                    刷新记录
                  </button>
                </div>

            {historyError ? (
              <div className="mb-3 rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-300">
                {historyError}
              </div>
            ) : isLoadingHistory && filteredBacktestInstances.length === 0 ? (
              <div className="flex items-center gap-2 rounded-lg border border-crypto-border bg-crypto-bg/40 px-3 py-3 text-xs text-gray-500">
                <Loader2 className="h-4 w-4 animate-spin text-blue-400" />
                正在加载回测记录…
              </div>
            ) : filteredBacktestInstances.length === 0 ? (
              <div className="flex flex-col items-center justify-center rounded-xl border border-dashed border-crypto-border py-16 text-sm text-gray-500">
                <FlaskConical className="mb-3 h-10 w-10 opacity-40" />
                当前筛选下暂无回测实例。
              </div>
            ) : (
              <div className="space-y-3">
                {filteredBacktestInstances.map((instance) => {
                  const meta = backtestInstanceStatusMeta(instance.status);
                  const actionStatusLabel = backtestInstanceActionStatusLabel(instance.status);
                  const strategyInfoForInstance = backtestableStrategies.find((s) => Number(s.id) === Number(instance.config.selectedStrategy));
                  const assetClass = backtestInstanceAssetClass(backtestableStrategies, instance);
                  const strategyLabel = backtestStrategyDisplayName(backtestableStrategies, instance.config.selectedStrategy);
                  const instanceTimeframes = backtestInstanceTimeframes(instance, strategyInfoForInstance);
                  const instanceRunning = instance.status === 'running' || instance.status === 'cancelling';
                  const instanceResumable = backtestInstanceCanContinue(instance);
                  const hasBacktestError = Boolean(instance.errorMessage || instance.result?.errorMessage || instance.status === 'failed');
                  const returnPct = backtestInstanceReturn(instance);
                  const progress = instance.jobProgress;
                  const historyRecordBusy =
                    instance.historyId != null &&
                    (selectedHistoryId === instance.historyId || deletingHistoryId === instance.historyId);
                  const selectedRecord =
                    selectedInstance?.id === instance.id ||
                    (historyDetailResult?.id != null && instance.historyId === Number(historyDetailResult.id));
                  return (
                    <div
                      key={instance.id}
                      role="button"
                      tabIndex={0}
                      onClick={() => openBacktestRecordDetail(instance)}
                      onKeyDown={(event) => {
                        if (event.key === 'Enter' || event.key === ' ') openBacktestRecordDetail(instance);
                      }}
                      className={clsx(
                        'cursor-pointer rounded-xl border bg-crypto-bg/40 p-3 transition-colors hover:border-gray-600 md:grid md:grid-cols-[minmax(420px,1.75fr)_minmax(260px,0.75fr)_minmax(300px,auto)] md:items-center md:gap-2 md:py-3 xl:grid-cols-[minmax(560px,2fr)_minmax(320px,0.8fr)_minmax(300px,auto)]',
                        selectedRecord ? 'border-purple-500/70' : 'border-crypto-border',
                      )}
                    >
                      <div className="md:col-start-1 md:row-start-1">
                        <div className="min-w-0">
                          <div className={clsx('break-words text-sm font-semibold leading-snug', strategyNameColorClass(assetClass))}>
                            {strategyLabel}
                          </div>
                          <div className="mt-1 flex flex-wrap items-center gap-2">
                            <span className={clsx('rounded border px-2 py-0.5 text-[10px] font-bold', strategyAssetBadgeClass(assetClass))}>
                              {assetClass === 'contract' ? '合约' : '现货'}
                            </span>
                            <span className={clsx('rounded-full border px-2 py-0.5 text-[10px] font-bold', meta.className)}>
                              {meta.label}
                            </span>
                            {instanceTimeframes.length > 0 && (
                              <>
                                <span className="text-[10px] font-medium text-gray-500">周期</span>
                                {instanceTimeframes.map((timeframe) => (
                                  <span
                                    key={timeframe}
                                    className="backtestInstanceTimeframeChip rounded border border-blue-500/30 bg-blue-500/10 px-2 py-0.5 text-[10px] font-bold text-blue-300"
                                  >
                                    {backtestTimeframeLabel(timeframe)}
                                  </span>
                                ))}
                              </>
                            )}
                          </div>
                          <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-gray-500">
                            <span>{instance.config.startDate} 至 {instance.config.endDate}</span>
                            <span>
                              {instance.isPersistedHistory ? formatDateTime(instance.createdAt) : `#${instance.name.replace('回测实例 ', '')}`}
                            </span>
                          </div>
                        </div>
                      </div>

                      <div className="mt-3 grid grid-cols-5 gap-1 text-center md:col-start-2 md:row-start-1 md:mt-0">
                        <MiniMetric label="收益" value={returnPct == null ? '--' : fmtPct(returnPct)} color={returnPct == null ? 'text-gray-500' : returnPct >= 0 ? 'text-up' : 'text-down'} />
                        <MiniMetric label="夏普" value={fmt(instance.result?.sharpeRatio)} />
                        <MiniMetric label="回撤" value={instance.result?.maxDrawdown == null ? '--' : `${fmt(instance.result.maxDrawdown)}%`} color="text-down" />
                        <MiniMetric label="胜率" value={instance.result?.winRate == null ? '--' : `${fmt(instance.result.winRate)}%`} />
                        <MiniMetric label="交易" value={String(instance.result?.totalTrades ?? 0)} color="text-blue-300" />
                      </div>

                      {instanceRunning && (
                        <div className="mt-2 md:col-start-2 md:row-start-2">
                          <div className="mb-1 flex justify-between text-[10px] text-gray-500">
                            <span>{instance.status === 'cancelling' ? '正在停止' : '后台回测中'}</span>
                            <span className="tabular-nums">
                              {progress && progress.totalBars > 0
                                ? `${progress.currentBar} / ${progress.totalBars}`
                                : '准备中'}
                            </span>
                          </div>
                          <div className="h-1.5 overflow-hidden rounded-full bg-crypto-card">
                            <div
                              className="h-full bg-purple-500 transition-all duration-300"
                              style={{ width: `${Math.min(100, progress?.percent ?? 0)}%` }}
                            />
                          </div>
                        </div>
                      )}

                      <div className="mt-3 flex flex-wrap gap-2 border-t border-crypto-border pt-3 md:col-start-3 md:row-start-1 md:mt-0 md:flex-row md:items-center md:justify-end md:border-t-0 md:pt-0">
                        <button
                          type="button"
                          aria-label="打开详情"
                          onClick={(event) => {
                            event.stopPropagation();
                            openBacktestRecordDetail(instance);
                          }}
                          disabled={historyRecordBusy}
                          className={backtestInstanceActionButtonClass('blue')}
                        >
                          {historyRecordBusy && selectedHistoryId === instance.historyId ? <Loader2 className="h-4 w-4 animate-spin" /> : <Eye className="h-4 w-4" />}
                          详情
                        </button>
                        <button
                          type="button"
                          aria-label={`查看回测状态：${actionStatusLabel}`}
                          onClick={(event) => {
                            event.stopPropagation();
                            setBacktestStatusTarget(instance);
                          }}
                          className={backtestInstanceActionButtonClass(backtestInstanceActionStatusTone(instance.status))}
                        >
                          {backtestInstanceActionStatusIcon(instance.status)}
                          {actionStatusLabel}
                        </button>
                        <button
                          type="button"
                          aria-label="查看日志"
                          onClick={(event) => {
                            event.stopPropagation();
                            setBacktestLogTarget(instance);
                          }}
                          className={backtestInstanceActionButtonClass(hasBacktestError ? 'red' : 'neutral')}
                        >
                          <FileText className="h-4 w-4" />
                          日志
                        </button>
                        {instanceRunning ? (
                          <>
                            <button
                              type="button"
                              aria-label={instance.status === 'cancelling' ? '停止中' : '停止回测'}
                              onClick={(event) => {
                                event.stopPropagation();
                                void cancelBacktestInstance(instance.id);
                              }}
                              disabled={instance.status === 'cancelling'}
                              className={backtestInstanceActionButtonClass('red')}
                            >
                              {instance.status === 'cancelling' ? <Loader2 className="h-4 w-4 animate-spin" /> : <Square className="h-4 w-4" />}
                              {instance.status === 'cancelling' ? '停止中' : '停止'}
                            </button>
                            <button
                              type="button"
                              disabled
                              aria-label="删除实例"
                              title="请先停止回测"
                              className={backtestInstanceActionButtonClass('red', 'opacity-35')}
                            >
                              <Trash2 className="h-4 w-4" />
                              删除
                            </button>
                          </>
                        ) : (
                          <>
                            {instanceResumable && (
                              <button
                                type="button"
                                aria-label="继续回测"
                                onClick={(event) => {
                                  event.stopPropagation();
                                  void resumeBacktestInstance(instance.id);
                                }}
                                className={backtestInstanceActionButtonClass('green')}
                              >
                                <Play className="h-4 w-4" />
                                继续
                              </button>
                            )}
                            <button
                              type="button"
                              aria-label={instance.isPersistedHistory || instance.historyId ? '删除记录' : '删除实例'}
                              onClick={(event) => {
                                event.stopPropagation();
                                deleteBacktestUnifiedRecord(instance);
                              }}
                              disabled={historyRecordBusy}
                              title={instance.isPersistedHistory || instance.historyId ? '删除落库记录' : '删除本地实例'}
                              className={backtestInstanceActionButtonClass('red')}
                            >
                              {historyRecordBusy && deletingHistoryId === instance.historyId ? <Loader2 className="h-4 w-4 animate-spin" /> : <Trash2 className="h-4 w-4" />}
                              删除
                            </button>
                          </>
                        )}
                      </div>
                    </div>
                  );
                })}
                {historyHasMore && (
                  <button
                    type="button"
                    onClick={() => void loadBacktestHistory({ reset: false, offset: historyItems.length })}
                    disabled={isLoadingMoreHistory}
                    className="flex w-full items-center justify-center gap-2 rounded-lg border border-crypto-border bg-crypto-bg/40 px-3 py-2 text-xs font-semibold text-blue-300 transition-colors hover:border-blue-500/60 hover:bg-blue-500/10 disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    {isLoadingMoreHistory ? <Loader2 className="h-4 w-4 animate-spin" /> : <Layers className="h-4 w-4" />}
                    {isLoadingMoreHistory ? '正在加载更多记录…' : '加载更多回测记录'}
                  </button>
                )}
              </div>
            )}
              </div>
            </>
          )}

        </div>
      ) : (
        <div className="space-y-4">
          <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
            <div>
              <button
                type="button"
                onClick={() => {
                  setHistoryDetailResult(null);
                  setView('dashboard');
                }}
                className="mb-4 inline-flex items-center gap-2 rounded-xl border border-crypto-border bg-crypto-card px-4 py-2 text-sm font-semibold text-gray-300 transition-colors hover:border-blue-500/50 hover:text-blue-300"
              >
                <ChevronLeft className="h-4 w-4" />
                返回控制台
              </button>
              <h1 className={clsx('text-xl font-bold', strategyNameColorClass(resultAssetClass))}>
                {detailStrategyName}
              </h1>
              <div className="mt-2 flex flex-wrap items-center gap-2">
                <span className={clsx('rounded border px-2 py-0.5 text-[11px] font-bold', strategyAssetBadgeClass(resultAssetClass))}>
                  {resultAssetClass === 'contract' ? '合约' : '现货'}
                </span>
                <span className={clsx('rounded-full border px-2 py-0.5 text-[11px] font-bold', detailStatusMeta.className)}>
                  {historyDetailResult ? '已完成' : detailStatusMeta.label}
                </span>
                <span className="text-xs text-gray-500">
                  {result?.startDate || startDate} 至 {result?.endDate || endDate}
                </span>
              </div>
            </div>
            {!historyDetailResult && selectedInstance && (
              <div className="flex flex-wrap items-center gap-2">
                {(selectedInstance.status === 'running' || selectedInstance.status === 'cancelling') ? (
                  <button
                    type="button"
                    onClick={() => void cancelBacktestInstance(selectedInstance.id)}
                    disabled={selectedInstance.status === 'cancelling'}
                    className="inline-flex items-center justify-center gap-2 rounded-xl border border-red-500/50 px-4 py-2 text-sm font-semibold text-red-300 hover:bg-red-500/10 disabled:cursor-not-allowed disabled:opacity-60"
                  >
                    {selectedInstance.status === 'cancelling' ? <Loader2 className="h-4 w-4 animate-spin" /> : <Square className="h-4 w-4" />}
                    {selectedInstance.status === 'cancelling' ? '停止中' : '停止回测'}
                  </button>
                ) : (
                  <>
                    {backtestInstanceCanContinue(selectedInstance) && (
                      <button
                        type="button"
                        onClick={() => void resumeBacktestInstance(selectedInstance.id)}
                        className="inline-flex items-center justify-center gap-2 rounded-xl border border-green-500/50 px-4 py-2 text-sm font-semibold text-green-300 hover:bg-green-500/10"
                      >
                        <Play className="h-4 w-4" />
                        继续回测
                      </button>
                    )}
                    <button
                      type="button"
                      onClick={() => deleteBacktestInstance(selectedInstance.id)}
                      className="inline-flex items-center justify-center gap-2 rounded-xl border border-red-500/50 px-4 py-2 text-sm font-semibold text-red-300 hover:bg-red-500/10"
                    >
                      <Trash2 className="h-4 w-4" />
                      删除实例
                    </button>
                  </>
                )}
              </div>
            )}
          </div>

          {!hasResult ? (
            <div className="bg-crypto-card border border-crypto-border rounded-xl flex flex-col items-center justify-center py-24 px-6 text-center">
              {isRunning ? (
                <>
                  <Loader2 className="w-14 h-14 text-purple-500 mb-4 animate-spin" />
                  <p className="text-white text-sm font-medium">{isCancelling ? '回测停止中' : '回测进行中'}</p>
                  {jobProgress && jobProgress.totalBars > 0 ? (
                    <p className="text-gray-400 text-xs mt-2 tabular-nums">
                      {jobProgress.currentBar} / {jobProgress.totalBars} 根 K 线
                      {jobProgress.percent != null ? `（${jobProgress.percent.toFixed(1)}%）` : ''}
                    </p>
                  ) : (
                    <p className="text-gray-500 text-xs mt-2">正在加载行情与初始化引擎…</p>
                  )}
                </>
              ) : (
                <>
                  {selectedInstance?.status === 'failed' ? (
                    <>
                      <FileText className="mb-4 h-16 w-16 text-red-500/50" />
                      <p className="text-sm font-medium text-red-300">回测失败</p>
                      <p className="mt-2 text-xs text-gray-500">在实例列表点击「日志」查看失败原因。</p>
                    </>
                  ) : (
                    <>
                      <FlaskConical className="w-16 h-16 text-gray-700 mb-4" />
                      <p className="text-gray-500 text-sm">选择策略并运行回测后查看绩效报告</p>
                    </>
                  )}
                </>
              )}
            </div>
          ) : (
            <>
              {/* ====== BigQuant 风格 - 概要指标行 ====== */}
              <div className="bg-crypto-card border border-crypto-border rounded-xl p-5">
                <div className="flex items-center justify-between mb-4">
                  <div className="flex items-center gap-2">
                    <span
                      className="text-xs text-gray-500 bg-crypto-bg px-2 py-0.5 rounded"
                      title={tradeSymbols.length > 0 ? tradeSymbols.join(', ') : feedSymbols.join(', ')}
                    >
                      回测时间范围：{result.startDate || startDate || '-'} 至 {result.endDate || endDate || '-'}
                    </span>
                    <span
                      className="text-xs text-gray-500 bg-crypto-bg px-2 py-0.5 rounded"
                      title={tradeSymbols.length > 0 ? tradeSymbols.join(', ') : feedSymbols.join(', ')}
                    >
                      {symbolScopeLabel} · {backtestTimeframeLabel(result.timeframe || selectedStrategyTimeframeLabel)}
                    </span>
                  </div>
                  {/* Tab 切换 */}
                  <div className="flex items-center gap-1 bg-crypto-bg rounded-lg p-0.5">
                    {([
                      ['overview', '概要'],
                      ['performance', '绩效'],
                      ['trades', '交易记录'],
                    ] as [ResultTab, string][]).map(([key, label]) => (
                      <button key={key} onClick={() => setResultTab(key)}
                        className={clsx('px-3 py-1.5 rounded-md text-xs font-medium transition-colors',
                          resultTab === key ? 'bg-purple-500/20 text-purple-400' : 'text-gray-500 hover:text-gray-300'
                        )}>
                        {label}
                      </button>
                    ))}
                  </div>
                </div>

                {matrixPeriodResults.length > 0 && (
                  <div className="mb-4 overflow-hidden rounded-xl border border-crypto-border bg-crypto-bg/45">
                    <div className="flex flex-col gap-3 border-b border-crypto-border px-4 py-3 xl:flex-row xl:items-center xl:justify-between">
                      <div>
                        <div className="text-sm font-semibold text-white">多周期矩阵</div>
                        <div className="mt-0.5 text-xs text-gray-500">选择周期详情后，下方指标、K线复盘和交易记录都会切换到该周期。</div>
                      </div>
                      <div className="backtestMatrixTimeframeTabs flex flex-wrap items-center gap-2">
                        <span className="text-xs font-semibold text-gray-500">周期详情</span>
                        {matrixPeriodResults.map((item) => {
                          const active = activeMatrixTimeframe === item.timeframe;
                          return (
                            <button
                              key={item.timeframe || item.status}
                              type="button"
                              aria-pressed={active}
                              onClick={() => setActiveMatrixTimeframe(item.timeframe || '')}
                              className={clsx(
                                'inline-flex items-center gap-2 rounded-lg border px-3 py-1.5 text-xs font-semibold transition-colors',
                                active
                                  ? 'border-purple-400/60 bg-purple-500/20 text-purple-100 shadow-[0_0_0_1px_rgba(168,85,247,0.12)]'
                                  : 'border-crypto-border bg-crypto-card/70 text-gray-500 hover:border-purple-500/40 hover:text-gray-200',
                              )}
                            >
                              <span>{backtestTimeframeLabel(item.timeframe)}</span>
                              <span className={clsx('tabular-nums', (item.totalReturn ?? 0) >= 0 ? 'text-up' : 'text-down')}>
                                {fmtPct(item.totalReturn)}
                              </span>
                            </button>
                          );
                        })}
                        <span className="rounded bg-purple-500/10 px-2 py-1 text-xs text-purple-300">
                          {matrixPeriodResults.length} 个周期
                        </span>
                      </div>
                    </div>
                    <div className="overflow-x-auto">
                      <table className="w-full min-w-[720px] text-left text-xs">
                        <thead className="bg-crypto-card/60 text-gray-500">
                          <tr>
                            <th className="px-4 py-2 font-medium">周期</th>
                            <th className="px-4 py-2 font-medium">状态</th>
                            <th className="px-4 py-2 font-medium">收益率</th>
                            <th className="px-4 py-2 font-medium">最大回撤</th>
                            <th className="px-4 py-2 font-medium">胜率</th>
                            <th className="px-4 py-2 font-medium">盈亏比</th>
                            <th className="px-4 py-2 font-medium">交易数</th>
                            <th className="px-4 py-2 font-medium">执行时间</th>
                          </tr>
                        </thead>
                        <tbody className="divide-y divide-crypto-border">
                          {matrixPeriodResults.map((item) => (
                            <tr key={item.timeframe || item.status} className={activeMatrixTimeframe === item.timeframe ? 'bg-purple-500/5' : undefined}>
                              <td className="px-4 py-2 font-semibold text-white">{backtestTimeframeLabel(item.timeframe)}</td>
                              <td className="px-4 py-2 text-gray-300">{item.status === 'completed' ? '完成' : item.status || '-'}</td>
                              <td className={clsx('px-4 py-2 font-semibold', (item.totalReturn ?? 0) >= 0 ? 'text-up' : 'text-down')}>{fmtPct(item.totalReturn)}</td>
                              <td className="px-4 py-2 text-down">{fmt(item.maxDrawdown)}%</td>
                              <td className="px-4 py-2 text-gray-200">{fmt(item.winRate)}%</td>
                              <td className="px-4 py-2 text-gray-200">{fmt(item.profitFactor)}</td>
                              <td className="px-4 py-2 text-gray-200">{item.totalTrades ?? 0}</td>
                              <td className="px-4 py-2 text-gray-400">{item.elapsedSeconds != null ? `${item.elapsedSeconds.toFixed(1)}s` : '-'}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                )}

                {/* ====== Crypto 常用绩效指标 ====== */}
                <div className="grid grid-cols-1 gap-3 lg:grid-cols-2 2xl:grid-cols-4">
                  {cryptoMetricGroups.map((group) => (
                    <CryptoMetricGroup
                      key={group.title}
                      title={group.title}
                      subtitle={group.subtitle}
                      tone={group.tone}
                      metrics={group.metrics}
                    />
                  ))}
                </div>
              </div>

              {/* ====== 概要 Tab ====== */}
              {resultTab === 'overview' && (
                <>
                  {renderBacktestKlineReview({ height: 520, showRangeStats: true })}
                </>
              )}

              {/* ====== 绩效 Tab ====== */}
              {resultTab === 'performance' && (
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                  {/* 交易统计 */}
                  <div className="bg-crypto-card border border-crypto-border rounded-xl p-5">
                    <h3 className="text-sm font-semibold text-white mb-4 flex items-center gap-2">
                      <Activity className="w-4 h-4 text-purple-400" />交易统计
                    </h3>
                    <div className="space-y-2.5">
                      <StatRow label="总交易次数" value={`${result.totalTrades || 0}`} />
                      <StatRow label="盈利交易" value={`${result.winningTrades || 0}`} color="text-up" />
                      <StatRow label="亏损交易" value={`${result.losingTrades || 0}`} color="text-down" />
                      <StatRow label="胜率" value={`${fmt(result.winRate)}%`} color={(result.winRate ?? 0) >= 50 ? 'text-up' : 'text-down'} />
                      <StatRow label="盈亏比" value={fmt(result.profitFactor)} />
                      <StatRow label="平均盈利" value={fmtPct(result.avgWinPct)} color="text-up" />
                      <StatRow label="平均亏损" value={fmtPct(result.avgLossPct)} color="text-down" />
                      <StatRow label="期望收益/笔" value={`$${fmt(result.expectancy)}`} />
                      <StatRow label="最大连胜" value={`${result.maxConsecutiveWins || 0}`} color="text-up" />
                      <StatRow label="最大连亏" value={`${result.maxConsecutiveLosses || 0}`} color="text-down" />
                      <StatRow label="平均持仓" value={`${fmt(result.avgHoldingBars)} bars`} />
                    </div>
                  </div>

                  {/* 资金统计 + 月度 */}
                  <div className="space-y-4">
                    <div className="bg-crypto-card border border-crypto-border rounded-xl p-5">
                      <h3 className="text-sm font-semibold text-white mb-4 flex items-center gap-2">
                        <DollarSign className="w-4 h-4 text-green-400" />资金统计
                      </h3>
                      <div className="space-y-2.5">
                        <StatRow label="初始资金" value={`$${fmt(result.initialCapital)}`} />
                        <StatRow label="最终资金" value={`$${fmt(result.finalCapital)}`}
                          color={(result.finalCapital ?? 0) >= result.initialCapital ? 'text-up' : 'text-down'} />
                        <StatRow label="总手续费" value={`$${fmt(result.totalFees)}`} />
                        <StatRow label="最大回撤" value={`${fmt(result.maxDrawdown)}%`} color="text-down" />
                        <StatRow label="回撤持续" value={`${result.maxDrawdownDurationDays || 0} 天`} />
                        <StatRow label="夏普比率" value={fmt(result.sharpeRatio)} />
                        <StatRow label="Sortino" value={fmt(result.sortinoRatio)} />
                        <StatRow label="Calmar" value={fmt(result.calmarRatio)} />
                      </div>
                    </div>

                    {/* 月度收益热力图 */}
                    {result.monthlyReturns && Object.keys(result.monthlyReturns).length > 0 && (
                      <div className="bg-crypto-card border border-crypto-border rounded-xl p-5">
                        <h3 className="text-sm font-semibold text-white mb-3 flex items-center gap-2">
                          <Calendar className="w-4 h-4 text-blue-400" />月度收益
                        </h3>
                        <div className="grid grid-cols-4 gap-1.5">
                          {Object.entries(result.monthlyReturns).sort().map(([month, ret]) => (
                            <div key={month}
                              className={clsx(
                                'px-2 py-1.5 rounded-lg text-center text-xs font-medium',
                                ret >= 0 ? 'bg-up text-up' : 'bg-down text-down'
                              )}>
                              <div className="text-[10px] text-gray-500">{month.slice(5)}</div>
                              <div>{ret >= 0 ? '+' : ''}{ret.toFixed(1)}%</div>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>
                </div>
              )}

              {/* ====== 交易记录 Tab ====== */}
              {resultTab === 'trades' && (
                <div className="bg-crypto-card border border-crypto-border rounded-xl p-5">
                  <h3 className="text-sm font-semibold text-white mb-4 flex items-center gap-2">
                    <List className="w-4 h-4 text-blue-400" />
                    交易记录
                    <span className="text-xs text-gray-500 ml-auto">{result.trades?.length || 0} 笔</span>
                  </h3>
                  <p className="mb-3 text-xs text-gray-500">
                    默认按时间倒序显示最近 100 笔；价格为历史撮合价（含滑点），不是当前行情价。
                  </p>
                  {result.trades && result.trades.length > 0 ? (
                    <div className="space-y-5">
                      {renderBacktestKlineReview({ height: 520 })}

                      <div className="overflow-x-auto">
                        <table className="w-full text-sm">
                          <thead>
                            <tr className="text-[11px] text-gray-500 border-b border-crypto-border">
                              <th className="text-left py-2.5 font-medium">时间</th>
                              <th className="text-left py-2.5 font-medium">交易对</th>
                              <th className="text-left py-2.5 font-medium">方向</th>
                              <th className="text-right py-2.5 font-medium">历史成交价</th>
                              <th className="text-right py-2.5 font-medium">数量</th>
                              <th className="text-right py-2.5 font-medium">杠杆</th>
                              <th className="text-right py-2.5 font-medium">保证金</th>
                              <th className="text-right py-2.5 font-medium">成交名义</th>
                              <th className="text-right py-2.5 font-medium">盈亏</th>
                              <th className="text-right py-2.5 font-medium">手续费</th>
                              <th className="text-left py-2.5 font-medium">原因</th>
                            </tr>
                          </thead>
                          <tbody>
                            {displayedTrades.map((trade, i) => {
                              const sideDisplay = getTradeSideDisplay(trade.side);
                              const margin = backtestTradeMargin(trade);
                              const notional = backtestTradeNotional(trade);
                              return (
                              <tr key={i} className="border-b border-crypto-border/20 hover:bg-white/[0.02] transition-colors">
                                <td className="py-2 text-xs text-gray-400">{new Date(trade.timestamp).toLocaleString('zh-CN')}</td>
                                <td className="py-2 text-xs text-gray-300">{trade.symbol || '-'}</td>
                                <td className={clsx('py-2 text-xs font-semibold', sideDisplay.className)}>
                                  {sideDisplay.label}
                                </td>
                                <td className="py-2 text-right text-xs text-white" title="历史撮合价，已计入滑点假设">{trade.price.toFixed(2)}</td>
                                <td className="py-2 text-right text-xs text-white">{trade.quantity.toFixed(4)}</td>
                                <td className="py-2 text-right text-xs text-gray-300">{formatBacktestTradeLeverage(trade.leverage)}</td>
                                <td className="py-2 text-right text-xs text-gray-300">{formatBacktestTradeMoney(margin)}</td>
                                <td className="py-2 text-right text-xs text-gray-300">{formatBacktestTradeMoney(notional)}</td>
                                <td className={clsx('py-2 text-right text-xs font-medium', trade.pnl >= 0 ? 'text-up' : 'text-down')}>
                                  {trade.pnl ? `${trade.pnl >= 0 ? '+' : ''}${trade.pnl.toFixed(2)}` : '-'}
                                </td>
                                <td className="py-2 text-right text-xs text-gray-500">{trade.fee ? trade.fee.toFixed(2) : '-'}</td>
                                <td className="py-2 text-xs text-gray-500">{trade.reason || '-'}</td>
                              </tr>
                              );
                            })}
                          </tbody>
                        </table>
                        {result.trades.length > displayedTrades.length && (
                          <p className="text-center text-gray-500 text-xs mt-3">
                            共 {result.trades.length} 笔交易（按时间倒序显示最近100笔）
                          </p>
                        )}
                      </div>
                    </div>
                  ) : (
                    <div className="text-center py-12 text-gray-500 text-sm">暂无交易记录</div>
                  )}
                </div>
              )}
            </>
          )}
        </div>
      )}

      {isCreateModalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4 backdrop-blur-sm">
          <div className="max-h-[90vh] w-full max-w-4xl overflow-y-auto rounded-2xl border border-crypto-border bg-crypto-card shadow-2xl shadow-black/40">
            <div className="sticky top-0 z-10 flex items-start justify-between gap-4 border-b border-crypto-border bg-crypto-card/95 px-6 py-5 backdrop-blur">
              <div>
                <h2 className="flex items-center gap-2 text-lg font-bold text-white">
                  <FlaskConical className="h-5 w-5 text-purple-400" />
                  创建回测实例
                </h2>
                <p className="mt-1 text-xs text-gray-500">
                  选择策略、设置区间和成本，提交后生成独立回测实例并异步运行。
                </p>
              </div>
              <button
                type="button"
                onClick={() => setIsCreateModalOpen(false)}
                className="rounded-lg border border-crypto-border p-2 text-gray-500 hover:text-gray-300"
              >
                <X className="h-4 w-4" />
              </button>
            </div>

            <div className="px-6 py-5">
              <div className="mb-6 rounded-xl border border-crypto-border bg-crypto-bg/35 px-4 py-5">
                <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
                  {BACKTEST_WIZARD_STEPS.slice(0, 3).map((step, index) => (
                    <BacktestWizardStep
                      key={step.step}
                      step={step.step}
                      title={step.title}
                      desc={step.step === 3 ? '确认并启动回测' : step.desc}
                      state={step.step < createStep ? 'done' : step.step === createStep ? 'active' : 'pending'}
                      isLast={index === 2}
                    />
                  ))}
                </div>
              </div>

              {createStep === 1 && (
                <div className="space-y-4">
                  <Field label="选择策略">
                    <div className="backtestStrategySearchCombobox space-y-2">
                      <div className="relative">
                        <Search className="pointer-events-none absolute left-4 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-500" />
                        <input
                          type="search"
                          role="combobox"
                          aria-expanded="true"
                          aria-controls="backtest-strategy-search-results"
                          aria-autocomplete="list"
                          value={strategySearchQuery}
                          onChange={(event) => setStrategySearchQuery(event.target.value)}
                          onKeyDown={(event) => {
                            if (event.key === 'Enter' && filteredBacktestStrategyOptions[0]) {
                              event.preventDefault();
                              const firstStrategy = filteredBacktestStrategyOptions[0];
                              updateCreateDraft({ selectedStrategy: Number(firstStrategy.id) || null });
                              setStrategySearchQuery(String(firstStrategy.name || ''));
                            }
                            if (event.key === 'Escape') {
                              setStrategySearchQuery('');
                            }
                          }}
                          placeholder="搜索策略名 / 标的 / 周期 / 类型"
                          className="h-12 w-full rounded-xl border border-white/10 bg-[#0b1220]/95 pl-11 pr-12 text-sm font-semibold text-gray-100 outline-none shadow-[inset_0_1px_0_rgba(255,255,255,0.04),0_10px_28px_rgba(2,6,23,0.28)] transition duration-150 placeholder:text-gray-600 hover:border-blue-400/40 hover:bg-[#101a2b] focus:border-blue-400/70 focus:ring-2 focus:ring-blue-500/30"
                        />
                        {strategySearchQuery && (
                          <button
                            type="button"
                            aria-label="清空策略搜索"
                            onClick={() => setStrategySearchQuery('')}
                            className="absolute right-3 top-1/2 -translate-y-1/2 rounded-lg p-1 text-gray-500 transition hover:bg-white/5 hover:text-gray-200"
                          >
                            <X className="h-4 w-4" />
                          </button>
                        )}
                      </div>

                      <div
                        id="backtest-strategy-search-results"
                        role="listbox"
                        className="max-h-[320px] overflow-y-auto rounded-xl border border-crypto-border bg-crypto-bg/60 p-2 shadow-[inset_0_1px_0_rgba(255,255,255,0.04)]"
                      >
                        {filteredBacktestStrategyOptions.length === 0 ? (
                          <div className="rounded-lg border border-dashed border-white/10 px-4 py-6 text-center text-sm text-gray-500">
                            {backtestableStrategies.length === 0 ? '暂无可回测策略' : '没有匹配的策略'}
                          </div>
                        ) : (
                          <div className="space-y-2">
                            {filteredBacktestStrategyOptions.map((strategy) => {
                              const optionAssetClass = strategyAssetClass(strategy);
                              const optionSelected = Number(strategy.id) === Number(createDraft.selectedStrategy);
                              const optionSymbols = strategyTradeSymbols(strategy).length
                                ? strategyTradeSymbols(strategy)
                                : strategySymbols(strategy);
                              return (
                                <button
                                  key={strategy.id}
                                  type="button"
                                  role="option"
                                  aria-selected={optionSelected}
                                  onClick={() => {
                                    updateCreateDraft({ selectedStrategy: Number(strategy.id) || null });
                                    setStrategySearchQuery(String(strategy.name || ''));
                                  }}
                                  className={clsx(
                                    'w-full rounded-lg border px-3 py-3 text-left transition duration-150',
                                    optionSelected
                                      ? 'border-purple-400/60 bg-purple-500/15 shadow-[0_0_18px_-12px_rgba(168,85,247,0.95)]'
                                      : 'border-white/5 bg-white/[0.025] hover:border-blue-400/35 hover:bg-white/[0.055]',
                                  )}
                                >
                                  <div className="flex items-start justify-between gap-3">
                                    <div className="min-w-0">
                                      <div className="mb-1 flex min-w-0 items-center gap-2">
                                        <span className={clsx('shrink-0 rounded border px-2 py-0.5 text-[11px] font-bold', strategyAssetBadgeClass(optionAssetClass))}>
                                          {optionAssetClass === 'contract' ? '合约' : '现货'}
                                        </span>
                                        <span className={clsx('truncate text-sm font-semibold', strategyNameColorClass(optionAssetClass))}>
                                          {strategy.name}
                                        </span>
                                      </div>
                                      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-gray-500">
                                        <span>周期 <span className="text-gray-300">{strategyTimeframe(strategy) || '未定义'}</span></span>
                                        <span>范围 <span className="text-gray-300">{symbolSummary(optionSymbols)}</span></span>
                                      </div>
                                    </div>
                                    {optionSelected && (
                                      <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-emerald-300" />
                                    )}
                                  </div>
                                </button>
                              );
                            })}
                          </div>
                        )}
                      </div>
                    </div>
                  </Field>
                  {draftStrategyInfo && (
                    <div className="rounded-xl border border-crypto-border bg-crypto-bg/50 p-4">
                      <div className="mb-2 flex items-center gap-2">
                        <span className={clsx('rounded border px-2 py-0.5 text-[11px] font-bold', strategyAssetBadgeClass(draftAssetClass))}>
                          {draftAssetClass === 'contract' ? '合约' : '现货'}
                        </span>
                        <span className={clsx('truncate text-sm font-semibold', strategyNameColorClass(draftAssetClass))}>
                          {draftStrategyInfo.name}
                        </span>
                      </div>
                      <div className="grid grid-cols-1 gap-3 text-xs text-gray-500 md:grid-cols-2">
                        <div>策略周期：<span className="text-gray-300">{strategyTimeframe(draftStrategyInfo) || '未定义'}</span></div>
                        <div>交易范围：<span className="text-gray-300">{symbolSummary(strategyTradeSymbols(draftStrategyInfo).length ? strategyTradeSymbols(draftStrategyInfo) : strategySymbols(draftStrategyInfo))}</span></div>
                      </div>
                    </div>
                  )}
                </div>
              )}

              {createStep === 2 && (
                <div className="space-y-4">
                  <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
                    <Field label="开始日期">
                      <input
                        type="date"
                        value={createDraft.startDate}
                        onChange={(event) => updateCreateDraft({ startDate: event.target.value })}
                        max={todayDate}
                        className="w-full rounded-lg border border-crypto-border bg-crypto-bg px-3 py-3 text-sm text-white"
                      />
                    </Field>
                    <Field label="结束日期">
                      <input
                        type="date"
                        value={createDraft.endDate}
                        onChange={(event) => updateCreateDraft({ endDate: event.target.value })}
                        min={createDraft.startDate}
                        max={todayDate}
                        className="w-full rounded-lg border border-crypto-border bg-crypto-bg px-3 py-3 text-sm text-white"
                      />
                    </Field>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    {[
                      [1, '最近1月'],
                      [6, '最近6月'],
                      [12, '最近1年'],
                      [24, '最近2年'],
                    ].map(([months, label]) => (
                      <button
                        key={String(months)}
                        type="button"
                        onClick={() => applyQuickRange(Number(months))}
                        className="rounded-lg border border-crypto-border px-3 py-1.5 text-xs font-semibold text-gray-400 hover:border-purple-500/50 hover:text-purple-300"
                      >
                        {label}
                      </button>
                    ))}
                  </div>
                  <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
                    <Field label="初始资金 (USDT)">
                      <input
                        type="number"
                        value={createDraft.initialCapital}
                        onChange={(event) => updateCreateDraft({ initialCapital: Math.max(0, Number(event.target.value)) })}
                        className="w-full rounded-lg border border-crypto-border bg-crypto-bg px-3 py-3 text-sm text-white"
                      />
                    </Field>
                    <Field label="周期模式">
                      <div className="grid gap-2 sm:grid-cols-3">
                        {BACKTEST_TIMEFRAME_MODES.map((mode) => (
                          <button
                            key={mode.value}
                            type="button"
                            onClick={() => updateCreateDraft({ timeframeMode: mode.value })}
                            className={clsx(
                              'rounded-lg border px-3 py-2 text-left transition-colors',
                              createDraft.timeframeMode === mode.value
                                ? 'border-purple-400/60 bg-purple-500/20 text-white'
                                : 'border-crypto-border bg-crypto-bg text-gray-400 hover:border-purple-500/40 hover:text-purple-200',
                            )}
                          >
                            <div className="text-xs font-semibold">{mode.label}</div>
                            <div className="mt-1 text-[10px] text-gray-500">{mode.hint}</div>
                          </button>
                        ))}
                      </div>
                    </Field>
                    <Field label="K线周期">
                      <div className="rounded-lg border border-crypto-border bg-crypto-bg p-2">
                        {createDraft.timeframeMode === 'strategy' ? (
                          <div className="flex min-h-[38px] items-center justify-between px-2 text-sm">
                            <span className={draftStrategyInfo ? 'text-white' : 'text-gray-500'}>
                              {draftStrategyInfo ? strategyTimeframe(draftStrategyInfo) || '未定义' : '请选择策略'}
                            </span>
                            {draftStrategyInfo && <span className="rounded bg-purple-500/10 px-2 py-0.5 text-[10px] text-purple-300">策略定义</span>}
                          </div>
                        ) : (
                          <div className="flex flex-wrap gap-2">
                            {BACKTEST_TIMEFRAME_OPTIONS.map((option) => {
                              const active = createDraft.timeframeMode === 'matrix'
                                ? createDraft.timeframes.includes(option.value)
                                : createDraft.timeframe === option.value;
                              return (
                                <button
                                  key={option.value}
                                  type="button"
                                  onClick={() => {
                                    if (createDraft.timeframeMode === 'matrix') {
                                      const exists = createDraft.timeframes.includes(option.value);
                                      const next = exists
                                        ? createDraft.timeframes.filter((value) => value !== option.value)
                                        : [...createDraft.timeframes, option.value];
                                      updateCreateDraft({ timeframes: next.length > 0 ? next : [option.value] });
                                    } else {
                                      updateCreateDraft({ timeframe: option.value });
                                    }
                                  }}
                                  className={clsx(
                                    'min-w-[58px] rounded-md px-3 py-2 text-xs font-semibold transition-colors',
                                    active
                                      ? 'bg-purple-500/25 text-purple-100 ring-1 ring-purple-400/40'
                                      : 'bg-crypto-card text-gray-500 hover:text-gray-200',
                                  )}
                                >
                                  {option.label}
                                </button>
                              );
                            })}
                          </div>
                        )}
                      </div>
                    </Field>
                    <Field label="Maker 手续费 (bps)">
                      <input
                        type="number"
                        value={draftEffectiveMakerFeeBps}
                        onChange={(event) => updateCreateDraft({ makerFeeBps: Math.max(0, Number(event.target.value)) })}
                        step="0.1"
                        className="w-full rounded-lg border border-crypto-border bg-crypto-bg px-3 py-3 text-sm text-white"
                      />
                    </Field>
                    <Field label="Taker 手续费 (bps)">
                      <input
                        type="number"
                        value={draftEffectiveTakerFeeBps}
                        onChange={(event) => updateCreateDraft({ takerFeeBps: Math.max(0, Number(event.target.value)) })}
                        step="0.1"
                        className="w-full rounded-lg border border-crypto-border bg-crypto-bg px-3 py-3 text-sm text-white"
                      />
                    </Field>
                    <Field label="滑点 (bps)">
                      <input
                        type="number"
                        value={draftEffectiveSlippageBps}
                        onChange={(event) => updateCreateDraft({ slippageBps: Math.max(0, Number(event.target.value)) })}
                        step="0.1"
                        className="w-full rounded-lg border border-crypto-border bg-crypto-bg px-3 py-3 text-sm text-white"
                      />
                    </Field>
                  </div>
                </div>
              )}

              {createStep === 3 && (
                <div className="rounded-xl border border-crypto-border bg-crypto-bg/50 p-4">
                  <div className="mb-3 text-sm font-semibold text-white">确认回测任务</div>
                  <div className="grid grid-cols-1 gap-3 text-sm md:grid-cols-2">
                    <StatRow label="策略" value={draftStrategyInfo?.name || '未选择'} color={strategyNameColorClass(draftAssetClass)} />
                    <StatRow label="资产类型" value={draftAssetClass === 'contract' ? '合约' : '现货'} />
                    <StatRow label="区间" value={`${createDraft.startDate} 至 ${createDraft.endDate}`} />
                    <StatRow label="初始资金" value={`$${fmt(createDraft.initialCapital)}`} />
                    <StatRow label="Maker/Taker" value={`${fmt(draftEffectiveMakerFeeBps)}/${fmt(draftEffectiveTakerFeeBps)} bps`} />
                    <StatRow label="滑点" value={`${fmt(draftEffectiveSlippageBps)} bps`} />
                  </div>
                </div>
              )}
            </div>

            <div className="sticky bottom-0 flex items-center justify-between gap-3 border-t border-crypto-border bg-crypto-card/95 px-6 py-4 backdrop-blur">
              <button
                type="button"
                onClick={() => setIsCreateModalOpen(false)}
                className="rounded-xl border border-crypto-border px-4 py-2 text-sm font-semibold text-gray-400 hover:text-gray-200"
              >
                取消
              </button>
              <div className="flex gap-2">
                {createStep > 1 && (
                  <button
                    type="button"
                    onClick={() => setCreateStep((step) => Math.max(1, step - 1) as 1 | 2 | 3)}
                    className="rounded-xl border border-crypto-border px-4 py-2 text-sm font-semibold text-gray-300 hover:text-white"
                  >
                    上一步
                  </button>
                )}
                {createStep < 3 ? (
                  <button
                    type="button"
                    onClick={() => {
                      if (createStep === 1 && !createDraft.selectedStrategy) {
                        showThemeAlert('提示', '请选择策略', 'warning');
                        return;
                      }
                      if (createStep === 2) {
                        const dateError = backtestDateValidationMessage(createDraft);
                        if (dateError) {
                          showThemeAlert('回测日期无效', dateError, 'warning');
                          return;
                        }
                      }
                      setCreateStep((step) => Math.min(3, step + 1) as 1 | 2 | 3);
                    }}
                    className="rounded-xl bg-purple-600 px-5 py-2 text-sm font-semibold text-white hover:bg-purple-700"
                  >
                    下一步
                  </button>
                ) : (
                  <button
                    type="button"
                    onClick={() => void runBacktest(createDraft)}
                    disabled={!createDraft.selectedStrategy || createDraft.initialCapital <= 0}
                    className="inline-flex items-center gap-2 rounded-xl bg-purple-600 px-5 py-2 text-sm font-semibold text-white hover:bg-purple-700 disabled:cursor-not-allowed disabled:bg-gray-700 disabled:text-gray-500"
                  >
                    <Play className="h-4 w-4" />
                    开始回测
                  </button>
                )}
              </div>
            </div>
          </div>
        </div>
      )}

      <ThemeAlertDialog
        open={themeAlert.open}
        title={themeAlert.title}
        content={themeAlert.content}
        tone={themeAlert.tone}
        onClose={() => setThemeAlert((a) => ({ ...a, open: false }))}
      />
      <ThemeDialog
        open={Boolean(backtestStatusTarget)}
        title="回测状态"
        tone={backtestStatusTarget?.status === 'failed' ? 'danger' : 'default'}
        content={backtestStatusTarget ? backtestStatusDialogContent(backtestStatusTarget) : ''}
        onClose={() => setBacktestStatusTarget(null)}
      />
      <ThemeDialog
        open={Boolean(backtestLogTarget)}
        title="回测日志"
        tone={backtestLogTarget?.status === 'failed' ? 'danger' : 'default'}
        onClose={() => setBacktestLogTarget(null)}
      >
        <pre className="max-h-[420px] overflow-y-auto whitespace-pre-wrap rounded-xl border border-crypto-border bg-black/30 p-3 text-xs leading-6 text-gray-200">
          {backtestLogTarget ? backtestInstanceLogs(backtestLogTarget).join('\n') : ''}
        </pre>
      </ThemeDialog>
      <ThemeDialog
        open={Boolean(localBacktestDeleteTarget)}
        variant="confirm"
        tone="danger"
        title="删除本地回测实例"
        confirmText="确认删除"
        cancelText="取消"
        content={localBacktestDeleteTarget
          ? `删除本地回测实例 ${localBacktestDeleteTarget.name}？已落库的回测记录不会删除。`
          : ''}
        onCancel={() => setLocalBacktestDeleteTarget(null)}
        onConfirm={confirmDeleteLocalBacktestInstance}
      />
      <ThemeDialog
        open={Boolean(cancelBacktestTarget)}
        variant="confirm"
        tone="warning"
        title="停止当前回测"
        confirmText="确认停止"
        cancelText="取消"
        content={cancelBacktestTarget
          ? `停止 ${cancelBacktestTarget.name} 的当前回测？已完成进度会保留，但不会写入回测记录。`
          : ''}
        onCancel={() => setCancelBacktestTarget(null)}
        onConfirm={confirmCancelBacktestInstance}
      />
      <ThemeDialog
        open={Boolean(historyDeleteTarget)}
        variant="confirm"
        tone="danger"
        title={historyDeleteTarget?.mode === 'batch'
          ? `删除 ${historyDeleteTarget.items.length} 条回测记录`
          : '删除回测记录'}
        confirmText={isDeletingHistoryBatch ? '删除中...' : '确认删除'}
        cancelText="取消"
        onCancel={() => {
          if (!isDeletingHistoryBatch) setHistoryDeleteTarget(null);
        }}
        onConfirm={confirmDeleteBacktestHistory}
      >
        {historyDeleteTarget && (
          <div className="space-y-3 text-sm text-gray-300">
            <p>删除后无法恢复，已落库的回测摘要和成交记录会被移除。</p>
            <div className="max-h-56 overflow-y-auto rounded-xl border border-red-500/20 bg-red-500/5 p-3">
              {historyDeleteTarget.items.slice(0, 6).map((item) => (
                <div key={item.id} className="border-b border-red-500/10 py-2 last:border-0">
                  <div className={clsx('font-semibold', strategyNameColorClass(strategyAssetClassById(strategies, item.strategyId)))}>
                    {strategyNameById(strategies, item.strategyId)}
                  </div>
                  <div className="mt-1 text-xs text-gray-500">
                    {item.startDate} 至 {item.endDate} · 回测时间 {formatDateTime(item.createdAt)}
                  </div>
                </div>
              ))}
              {historyDeleteTarget.items.length > 6 && (
                <div className="pt-2 text-xs text-gray-500">
                  另有 {historyDeleteTarget.items.length - 6} 条记录将一起删除。
                </div>
              )}
            </div>
          </div>
        )}
      </ThemeDialog>
    </div>
  );
}

function CryptoMetricGroup({
  title,
  subtitle,
  tone,
  metrics,
}: {
  title: string;
  subtitle: string;
  tone: 'green' | 'red' | 'blue' | 'amber';
  metrics: CryptoMetricItem[];
}) {
  const toneClass = {
    green: 'border-green-500/30 bg-green-500/5 text-green-300',
    red: 'border-red-500/30 bg-red-500/5 text-red-300',
    blue: 'border-blue-500/30 bg-blue-500/5 text-blue-300',
    amber: 'border-amber-500/30 bg-amber-500/5 text-amber-300',
  }[tone];
  const metricAccentClass = {
    green: 'border-l-green-400/70',
    red: 'border-l-red-400/70',
    blue: 'border-l-blue-400/70',
    amber: 'border-l-amber-400/70',
  }[tone];

  return (
    <section className="rounded-xl border border-crypto-border bg-crypto-card/70 p-4 shadow-sm shadow-black/20">
      <div className="mb-3 flex items-start justify-between gap-3">
        <div>
          <h3 className="text-base font-semibold text-white">{title}</h3>
          <p className="mt-1 text-xs text-gray-400">{subtitle}</p>
        </div>
        <span className={clsx('rounded-full border px-2 py-0.5 text-[11px] font-bold', toneClass)}>
          Crypto
        </span>
      </div>
      <div className="grid grid-cols-2 gap-2.5">
        {metrics.map((metric) => {
          const textColor = metric.isDrawdown
            ? 'text-down'
            : metric.value === '-'
              ? 'text-gray-400'
              : metric.positive ? 'text-up' : 'text-down';
          return (
            <div
              key={metric.label}
              className={clsx(
                'min-h-[88px] rounded-lg border border-l-2 border-crypto-border/80 bg-crypto-bg/55 px-3 py-2.5',
                metricAccentClass,
              )}
              title={metric.description}
              aria-label={`${metric.label}：${metric.description}`}
            >
              <div className={clsx('break-words text-[clamp(0.875rem,0.82vw,1.05rem)] font-semibold leading-snug tracking-normal tabular-nums opacity-90', textColor)}>
                {metric.value}
              </div>
              <div className="mt-2 flex items-center gap-1.5 text-[11px] font-medium text-gray-300">
                <span className="truncate">{metric.label}</span>
                <Info className="h-3 w-3 shrink-0 text-gray-500" aria-hidden="true" />
              </div>
              {metric.caption ? (
                <div className="mt-1 truncate text-[10px] text-gray-500">{metric.caption}</div>
              ) : null}
            </div>
          );
        })}
      </div>
    </section>
  );
}

// ============================================
// 表单字段
// ============================================
function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="text-xs text-gray-400 mb-1 block">{label}</span>
      {children}
    </label>
  );
}

// ============================================
// 统计行
// ============================================
function StatRow({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div className="flex justify-between items-center py-0.5">
      <span className="text-xs text-gray-500">{label}</span>
      <span className={clsx('text-xs font-medium', color || 'text-white')}>{value}</span>
    </div>
  );
}

function MiniMetric({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div>
      <div className={clsx('text-[11px] font-bold tabular-nums', color || 'text-white')}>
        {value}
      </div>
      <div className="mt-1 text-[11px] font-medium text-gray-500">{label}</div>
    </div>
  );
}
