export interface BacktestData {
  totalReturn?: number;
  annualReturn?: number;
  maxDrawdown?: number;
  sharpeRatio?: number;
  winRate?: number;
  totalTrades?: number;
  profitFactor?: number;
  backtestId?: number;
}

export interface StrategyInfo {
  id: string | number;
  name: string;
  description: string;
  recommended?: boolean;
  riskLevel?: string;
  risk_level?: string;
  timeframe?: string;
  suitableFor?: string;
  backtest?: BacktestData;
  status?: string;
  config?: Record<string, unknown>;
  symbol?: string;
  symbols?: string[];
  initialCapital?: number;
  initial_capital?: number;
  createdAt?: string | null;
  created_at?: string | null;
}

export interface LivePositionRow {
  symbol: string;
  side: string;
  size: number;
  entryPrice?: number;
  markPrice?: number;
  unrealizedPnl?: number;
  unrealizedPnlPct?: number | null;
}

export interface DashboardData {
  system: {
    state: string;
    uptime: string;
    exchange: string;
    symbol: string;
    symbols?: string[];
    timeframe: string;
    strategy: string;
    /** 数据库策略主键，与详情拉取成交一致 */
    strategyId?: number;
    dryRun: boolean;
    mode: string;
  };
  equity: {
    initial?: number;
    current?: number;
    peak?: number;
    change?: number;
    changePct?: number;
  };
  performance: {
    totalPnl?: number;
    totalPnlPct?: number;
    winRate?: number;
    profitFactor?: number;
    totalTrades?: number;
    maxDrawdown?: number;
    sharpeRatio?: number;
  };
  risk: {
    circuitBreaker?: boolean;
    currentDrawdown?: number;
    dailyLoss?: number;
  };
  positions?: LivePositionRow[];
  account?: {
    unrealizedPnl?: number;
  };
  recentEvents: Array<{
    time: string;
    type: string;
    message: string;
    detail?: string;
  }>;
  feishu?: {
    enabled: boolean;
    /** 是否配置了 FEISHU_WEBHOOK_URL */
    webhookConfigured: boolean;
    messagesSent: number;
  };
}

export interface Balance {
  currency: string;
  free: number;
  used: number;
  total: number;
}

export type TradeMode = 'paper' | 'live';
export type AssetClassFilter = 'all' | 'spot' | 'contract';
export type InstanceSortMode =
  | 'created_desc'
  | 'created_asc'
  | 'return_desc'
  | 'return_asc'
  | 'win_rate_desc'
  | 'win_rate_asc'
  | 'profit_factor_desc'
  | 'profit_factor_asc';
export type PageView = 'dashboard' | 'create' | 'detail';
export type CreateStep = 'select' | 'configure' | 'preflight';
export type ConfirmTone = 'danger' | 'warning' | 'default';

/** 前端统一实例 id：paper:<id> | live:engine | live:strategy:<strategyId> */
export type TradingInstance = {
  id: string;
  kind: 'paper' | 'live';
  /** 与库中 is_paper_trading 一致：true=模拟成交，false=真实下单；paper 实例恒为 true */
  dryRun?: boolean;
  /** DB 策略 id（若有） */
  strategyId?: number;
  assetClass: Exclude<AssetClassFilter, 'all'>;
  name: string;
  symbol: string;
  timeframe: string;
  status: string;
  createdAt?: string | null;
  totalPnl?: number;
  totalReturnPct?: number;
  capitalVersion?: number;
  leverage?: number;
  strategyType?: string | null;
  strategyKey?: string | null;
  winRate?: number;
  profitFactor?: number;
  sharpeRatio?: number;
  maxDrawdownPct?: number;
  totalTrades?: number;
  /** 详情页停止：引擎会话 vs 仅策略任务 */
  stopKind: 'engine' | 'strategy' | 'paper';
  isAiAutonomous?: boolean;
};

export type PromotionCandidate = {
  strategy: StrategyInfo;
  status: string;
  returnPct?: number;
  sharpeRatio?: number;
  maxDrawdownPct?: number;
  totalTrades?: number;
};

export const ENGINE_SESSION_ID = 'live:engine';

/** 传给 /live/* 的 instance_id（引擎全局会话不传） */
export function toLiveApiInstanceId(activeInstanceId: string | null): string | number | undefined {
  if (!activeInstanceId || activeInstanceId === ENGINE_SESSION_ID) return undefined;
  if (activeInstanceId.startsWith('live:strategy:')) {
    const n = Number(activeInstanceId.replace('live:strategy:', ''));
    return Number.isFinite(n) ? n : undefined;
  }
  return undefined;
}

export function paperInstanceKey(activeInstanceId: string | null): string | null {
  if (!activeInstanceId || !activeInstanceId.startsWith('paper:')) return null;
  return activeInstanceId.slice('paper:'.length);
}
