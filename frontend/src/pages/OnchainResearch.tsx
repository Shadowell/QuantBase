import { useCallback, useEffect, useMemo, useState } from 'react';
import type { ReactNode } from 'react';
import {
  Banknote,
  Blocks,
  CircleDollarSign,
  DatabaseZap,
  Layers3,
  Loader2,
  Network,
  RefreshCw,
  ShieldAlert,
  TrendingUp,
  Wallet,
} from 'lucide-react';
import clsx from 'clsx';
import { onchainApi, type OnchainSummary } from '../api/client';

type TabId = 'overview' | 'protocols' | 'yields';

const tabs: Array<{ id: TabId; label: string; icon: typeof Network }> = [
  { id: 'overview', label: '综合总览', icon: Network },
  { id: 'protocols', label: '协议研究', icon: Layers3 },
  { id: 'yields', label: '收益机会', icon: Wallet },
];

function numberFrom(record: Record<string, unknown>, key: string): number | null {
  const raw = record[key];
  if (raw === null || raw === undefined || raw === '') return null;
  const value = Number(raw);
  return Number.isFinite(value) ? value : null;
}

function textFrom(record: Record<string, unknown>, key: string, fallback = '--'): string {
  const raw = record[key];
  if (raw === null || raw === undefined || raw === '') return fallback;
  return String(raw);
}

function money(value?: number | null): string {
  const n = Number(value ?? 0);
  if (!Number.isFinite(n)) return '0 美元';
  const abs = Math.abs(n);
  if (abs >= 1_000_000_000_000) return `${(n / 1_000_000_000_000).toFixed(2)} 万亿美元`;
  if (abs >= 100_000_000) return `${(n / 100_000_000).toFixed(2)} 亿美元`;
  if (abs >= 10_000) return `${(n / 10_000).toFixed(2)} 万美元`;
  return `${n.toFixed(2)} 美元`;
}

function pct(value?: number | null, digits = 2): string {
  const n = Number(value ?? 0);
  const prefix = n > 0 ? '+' : '';
  return `${prefix}${n.toFixed(digits)}%`;
}

function dateTime(value?: string): string {
  if (!value) return '--';
  const time = new Date(value);
  if (Number.isNaN(time.getTime())) return value;
  return time.toLocaleString('zh-CN', {
    hour12: false,
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
}

function statusTone(status: string): string {
  if (status === 'ready') return 'border-emerald-500/35 bg-emerald-500/10 text-emerald-200';
  if (status === 'partial') return 'border-amber-500/35 bg-amber-500/10 text-amber-200';
  return 'border-gray-700 bg-gray-900/70 text-gray-400';
}

function statusLabel(status: string): string {
  if (status === 'ready') return '正常';
  if (status === 'partial') return '部分可用';
  if (status === 'waiting_for_data') return '等待数据';
  if (status === 'loading') return '加载中';
  if (status === 'error') return '异常';
  if (status === 'empty') return '空数据';
  return status || '--';
}

function sourceLabel(name: string): string {
  const labels: Record<string, string> = {
    chains: '链锁仓量',
    protocols: '协议锁仓量',
    fees: '协议费用',
    stablecoins: '稳定币供给',
    stablecoin_chains: '稳定币链分布',
    yield_pools: '稳定币收益池',
    stablecoinChains: '稳定币链分布',
    yieldPools: '稳定币收益池',
  };
  return labels[name] || name;
}

function localizeWarning(warning: string): string {
  return warning
    .replace('DeFiLlama yield_pools ', 'DeFiLlama 稳定币收益池 ')
    .replace('DeFiLlama stablecoin_chains ', 'DeFiLlama 稳定币链分布 ')
    .replace(/DeFiLlama (chains|protocols|fees|stablecoins) /g, (_, name: string) => `DeFiLlama ${sourceLabel(name)} `);
}

function EmptyPanel({ text }: { text: string }) {
  return (
    <div className="flex min-h-[116px] items-center justify-center rounded-lg border border-dashed border-crypto-border bg-crypto-bg/45 px-4 text-center text-sm text-gray-500">
      {text}
    </div>
  );
}

function Section({
  title,
  icon,
  children,
}: {
  title: string;
  icon: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="rounded-lg border border-crypto-border bg-crypto-card/80 p-4">
      <div className="mb-3 flex items-center gap-2 text-sm font-semibold text-gray-100">
        {icon}
        {title}
      </div>
      {children}
    </section>
  );
}

function MetricCard({
  label,
  value,
  sub,
  icon,
  tone = 'text-cyan-200',
}: {
  label: string;
  value: string;
  sub?: string;
  icon: ReactNode;
  tone?: string;
}) {
  return (
    <div className="min-h-[102px] rounded-lg border border-crypto-border bg-crypto-card/80 p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="text-xs font-medium text-gray-500">{label}</div>
          <div className={clsx('mt-2 truncate text-2xl font-bold tracking-normal', tone)}>{value}</div>
        </div>
        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-crypto-border bg-crypto-bg/70 text-gray-300">
          {icon}
        </div>
      </div>
      {sub && <div className="mt-3 truncate text-xs text-gray-500">{sub}</div>}
    </div>
  );
}

export default function OnchainResearch() {
  const [summary, setSummary] = useState<OnchainSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [activeTab, setActiveTab] = useState<TabId>('overview');

  const loadSummary = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      setSummary(await onchainApi.getSummary());
    } catch (err: any) {
      setError(err?.response?.data?.detail || err?.message || '链上数据加载失败');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadSummary();
  }, [loadSummary]);

  const kpis = summary?.kpis;
  const endpointStatus = useMemo(() => {
    if (!summary) return [];
    return Object.entries(summary.sourceStatus || {});
  }, [summary]);
  const emptyText = summary?.emptyReason || '等待 DeFiLlama 返回真实链上数据';

  return (
    <div className="h-full w-full min-w-0 p-6">
      <header className="mb-5 flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h1 className="flex items-center gap-2 text-2xl font-bold tracking-normal text-white">
            <Network className="h-6 w-6 text-cyan-300" />
            链上数据
          </h1>
          <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-gray-500">
            <span>DeFiLlama</span>
            <span className={clsx('rounded-full border px-2 py-0.5', statusTone(summary?.status || 'loading'))}>
              {statusLabel(summary?.status || 'loading')}
            </span>
            <span>更新于 {dateTime(summary?.asOf)}</span>
          </div>
        </div>
        <button
          type="button"
          onClick={() => void loadSummary()}
          className="inline-flex h-10 items-center justify-center gap-2 rounded-lg border border-crypto-border bg-crypto-card px-3 text-sm font-semibold text-gray-200 hover:border-cyan-400/45 hover:text-cyan-100"
        >
          {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
          刷新
        </button>
      </header>

      {error && (
        <div className="mb-4 rounded-lg border border-red-500/25 bg-red-500/10 px-4 py-3 text-sm text-red-200">
          {error}
        </div>
      )}

      {summary?.warnings?.length ? (
        <div className="mb-4 rounded-lg border border-amber-500/25 bg-amber-500/10 px-4 py-3">
          <div className="mb-2 flex items-center gap-2 text-sm font-semibold text-amber-200">
            <ShieldAlert className="h-4 w-4" />
            数据源提示
          </div>
          <div className="space-y-1 text-xs text-amber-100/80">
            {summary.warnings.map((warning) => (
              <div key={warning}>{localizeWarning(warning)}</div>
            ))}
          </div>
        </div>
      ) : null}

      <div className="mb-5 flex flex-wrap gap-2">
        {tabs.map((tab) => {
          const Icon = tab.icon;
          return (
            <button
              key={tab.id}
              type="button"
              onClick={() => setActiveTab(tab.id)}
              className={clsx(
                'inline-flex h-9 items-center gap-2 rounded-lg border px-3 text-sm font-medium transition-colors',
                activeTab === tab.id
                  ? 'border-cyan-400/55 bg-cyan-500/15 text-cyan-100'
                  : 'border-crypto-border bg-crypto-card/70 text-gray-400 hover:border-gray-600 hover:text-gray-200'
              )}
            >
              <Icon className="h-4 w-4" />
              {tab.label}
            </button>
          );
        })}
      </div>

      {!summary && loading ? (
        <div className="flex min-h-[420px] items-center justify-center rounded-lg border border-crypto-border bg-crypto-card/70 text-sm text-gray-500">
          <Loader2 className="mr-2 h-4 w-4 animate-spin" />
          读取链上研究数据…
        </div>
      ) : summary ? (
        <div className="space-y-5">
          <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-6">
            <MetricCard label="总锁仓量" value={money(kpis?.totalTvlUsd)} sub={`${kpis?.chainCount || 0} 条链`} icon={<Blocks className="h-4 w-4" />} />
            <MetricCard label="稳定币供给" value={money(kpis?.totalStablecoinsUsd)} sub={`${summary.stablecoins.length} 种稳定币`} icon={<CircleDollarSign className="h-4 w-4" />} tone="text-emerald-200" />
            <MetricCard label="24H 协议费用" value={money(kpis?.fee24hUsd)} sub={`${summary.fees.length} 个协议`} icon={<Banknote className="h-4 w-4" />} tone="text-amber-200" />
            <MetricCard label="稳定币收益池" value={String(kpis?.stableYieldPoolCount || 0)} sub="DeFiLlama 收益池" icon={<TrendingUp className="h-4 w-4" />} tone="text-cyan-200" />
            <MetricCard label="最大公链" value={kpis?.topChain?.name || '--'} sub={money(kpis?.topChain?.tvlUsd)} icon={<Network className="h-4 w-4" />} />
            <MetricCard label="最大协议" value={kpis?.topProtocol?.name || '--'} sub={money(kpis?.topProtocol?.tvlUsd)} icon={<DatabaseZap className="h-4 w-4" />} />
          </div>

          {activeTab === 'overview' && (
            <div className="grid grid-cols-1 gap-5 xl:grid-cols-2">
              <Section title="综合总览 · 链锁仓量" icon={<Network className="h-4 w-4 text-cyan-300" />}>
                {summary.chains.length ? (
                  <div className="max-h-[360px] overflow-auto">
                    <table className="w-full min-w-[520px] text-left text-sm">
                      <thead className="sticky top-0 bg-crypto-card text-xs text-gray-500">
                        <tr>
                          <th className="py-2 pr-3 font-medium">链</th>
                          <th className="py-2 pr-3 font-medium">锁仓量</th>
                          <th className="py-2 pr-3 font-medium">代币</th>
                          <th className="py-2 font-medium">链编号</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-crypto-border/70">
                        {summary.chains.map((row) => (
                          <tr key={textFrom(row, 'name')} className="text-gray-300">
                            <td className="py-2 pr-3 font-medium text-gray-100">{textFrom(row, 'name')}</td>
                            <td className="py-2 pr-3 text-cyan-200">{money(numberFrom(row, 'tvlUsd'))}</td>
                            <td className="py-2 pr-3">{textFrom(row, 'tokenSymbol')}</td>
                            <td className="py-2 text-gray-500">{textFrom(row, 'chainId')}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : (
                  <EmptyPanel text={emptyText} />
                )}
              </Section>

              <Section title="稳定币供给" icon={<CircleDollarSign className="h-4 w-4 text-emerald-300" />}>
                {summary.stablecoins.length ? (
                  <div className="max-h-[360px] overflow-auto">
                    <table className="w-full min-w-[520px] text-left text-sm">
                      <thead className="sticky top-0 bg-crypto-card text-xs text-gray-500">
                        <tr>
                          <th className="py-2 pr-3 font-medium">稳定币</th>
                          <th className="py-2 pr-3 font-medium">供给</th>
                          <th className="py-2 pr-3 font-medium">锚定类型</th>
                          <th className="py-2 font-medium">覆盖链数</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-crypto-border/70">
                        {summary.stablecoins.map((row) => (
                          <tr key={textFrom(row, 'symbol')} className="text-gray-300">
                            <td className="py-2 pr-3">
                              <div className="font-medium text-gray-100">{textFrom(row, 'symbol')}</div>
                              <div className="text-xs text-gray-500">{textFrom(row, 'name')}</div>
                            </td>
                            <td className="py-2 pr-3 text-emerald-200">{money(numberFrom(row, 'supplyUsd'))}</td>
                            <td className="py-2 pr-3">{textFrom(row, 'pegType')}</td>
                            <td className="py-2">{textFrom(row, 'chainCount')}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : (
                  <EmptyPanel text={emptyText} />
                )}
              </Section>
            </div>
          )}

          {activeTab === 'protocols' && (
            <div className="grid grid-cols-1 gap-5 xl:grid-cols-[1.1fr_0.9fr]">
              <Section title="协议研究 · 协议锁仓量" icon={<Layers3 className="h-4 w-4 text-cyan-300" />}>
                {summary.protocols.length ? (
                  <div className="max-h-[460px] overflow-auto">
                    <table className="w-full min-w-[680px] text-left text-sm">
                      <thead className="sticky top-0 bg-crypto-card text-xs text-gray-500">
                        <tr>
                          <th className="py-2 pr-3 font-medium">协议</th>
                          <th className="py-2 pr-3 font-medium">类别</th>
                          <th className="py-2 pr-3 font-medium">锁仓量</th>
                          <th className="py-2 pr-3 font-medium">1日变化</th>
                          <th className="py-2 font-medium">7日变化</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-crypto-border/70">
                        {summary.protocols.map((row) => (
                          <tr key={textFrom(row, 'slug', textFrom(row, 'name'))} className="text-gray-300">
                            <td className="py-2 pr-3">
                              <div className="font-medium text-gray-100">{textFrom(row, 'name')}</div>
                              <div className="text-xs text-gray-500">{textFrom(row, 'chain')}</div>
                            </td>
                            <td className="py-2 pr-3">{textFrom(row, 'category')}</td>
                            <td className="py-2 pr-3 text-cyan-200">{money(numberFrom(row, 'tvlUsd'))}</td>
                            <td className="py-2 pr-3">{pct(numberFrom(row, 'change1d'))}</td>
                            <td className="py-2">{pct(numberFrom(row, 'change7d'))}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : (
                  <EmptyPanel text={emptyText} />
                )}
              </Section>

              <Section title="协议费用排行" icon={<Banknote className="h-4 w-4 text-amber-300" />}>
                {summary.fees.length ? (
                  <div className="max-h-[460px] overflow-auto">
                    <table className="w-full min-w-[520px] text-left text-sm">
                      <thead className="sticky top-0 bg-crypto-card text-xs text-gray-500">
                        <tr>
                          <th className="py-2 pr-3 font-medium">协议</th>
                          <th className="py-2 pr-3 font-medium">24H 费用</th>
                          <th className="py-2 pr-3 font-medium">7日费用</th>
                          <th className="py-2 font-medium">1日变化</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-crypto-border/70">
                        {summary.fees.map((row) => (
                          <tr key={textFrom(row, 'slug', textFrom(row, 'name'))} className="text-gray-300">
                            <td className="py-2 pr-3">
                              <div className="font-medium text-gray-100">{textFrom(row, 'name')}</div>
                              <div className="text-xs text-gray-500">{textFrom(row, 'category')}</div>
                            </td>
                            <td className="py-2 pr-3 text-amber-200">{money(numberFrom(row, 'total24hUsd'))}</td>
                            <td className="py-2 pr-3">{money(numberFrom(row, 'total7dUsd'))}</td>
                            <td className="py-2">{pct(numberFrom(row, 'change1d'))}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : (
                  <EmptyPanel text={emptyText} />
                )}
              </Section>
            </div>
          )}

          {activeTab === 'yields' && (
            <div className="grid grid-cols-1 gap-5 xl:grid-cols-[1.15fr_0.85fr]">
              <Section title="收益机会 · 稳定币收益" icon={<TrendingUp className="h-4 w-4 text-emerald-300" />}>
                {summary.yieldPools.length ? (
                  <div className="max-h-[460px] overflow-auto">
                    <table className="w-full min-w-[680px] text-left text-sm">
                      <thead className="sticky top-0 bg-crypto-card text-xs text-gray-500">
                        <tr>
                          <th className="py-2 pr-3 font-medium">收益池</th>
                          <th className="py-2 pr-3 font-medium">链</th>
                          <th className="py-2 pr-3 font-medium">年化收益</th>
                          <th className="py-2 pr-3 font-medium">30日均值</th>
                          <th className="py-2 pr-3 font-medium">池锁仓量</th>
                          <th className="py-2 font-medium">无常损失风险</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-crypto-border/70">
                        {summary.yieldPools.map((row) => (
                          <tr key={textFrom(row, 'pool')} className="text-gray-300">
                            <td className="py-2 pr-3">
                              <div className="font-medium text-gray-100">{textFrom(row, 'project')}</div>
                              <div className="text-xs text-gray-500">{textFrom(row, 'symbol')}</div>
                            </td>
                            <td className="py-2 pr-3">{textFrom(row, 'chain')}</td>
                            <td className="py-2 pr-3 text-emerald-200">{pct(numberFrom(row, 'apy'))}</td>
                            <td className="py-2 pr-3">{pct(numberFrom(row, 'apyMean30d'))}</td>
                            <td className="py-2 pr-3">{money(numberFrom(row, 'tvlUsd'))}</td>
                            <td className="py-2">{textFrom(row, 'ilRisk')}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : (
                  <EmptyPanel text={emptyText} />
                )}
              </Section>

              <Section title="稳定币链分布" icon={<Blocks className="h-4 w-4 text-cyan-300" />}>
                {summary.stablecoinChains.length ? (
                  <div className="max-h-[460px] overflow-auto">
                    <table className="w-full min-w-[420px] text-left text-sm">
                      <thead className="sticky top-0 bg-crypto-card text-xs text-gray-500">
                        <tr>
                          <th className="py-2 pr-3 font-medium">链</th>
                          <th className="py-2 font-medium">供给</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-crypto-border/70">
                        {summary.stablecoinChains.map((row) => (
                          <tr key={textFrom(row, 'name')} className="text-gray-300">
                            <td className="py-2 pr-3 font-medium text-gray-100">{textFrom(row, 'name')}</td>
                            <td className="py-2 text-emerald-200">{money(numberFrom(row, 'supplyUsd'))}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : (
                  <EmptyPanel text={emptyText} />
                )}
              </Section>
            </div>
          )}

          <Section title="DeFiLlama 数据源状态" icon={<DatabaseZap className="h-4 w-4 text-gray-300" />}>
            <div className="flex flex-wrap gap-2">
              {endpointStatus.map(([name, status]) => (
                <span key={name} className={clsx('rounded-full border px-2.5 py-1 text-xs', statusTone(status))}>
                  {sourceLabel(name)}: {statusLabel(status)}
                </span>
              ))}
            </div>
          </Section>
        </div>
      ) : (
        <EmptyPanel text={emptyText} />
      )}
    </div>
  );
}
