import { Suspense, useState, useRef, useEffect } from 'react';
import type { ReactNode } from 'react';
import { Outlet, NavLink, useLocation } from 'react-router-dom';
import {
  LayoutDashboard,
  TrendingUp,
  Code2,
  FlaskConical,
  Activity,
  Eye,
  Settings,
  Database,
  X,
  Sparkles,
  Send,
  CheckCircle2,
  AlertCircle,
  ArrowLeftRight,
  Cpu,
  PlugZap,
  Plus,
  Network,
} from 'lucide-react';
import clsx from 'clsx';
import {
  settingsApi,
  type LLMModelSettings,
} from '../api/client';
import { useSettingsStore, type ColorScheme } from '../stores/useSettingsStore';
import CryptoSelect from './CryptoSelect';
import { QuantBaseLogo } from './QuantBaseLogo';
import { PageErrorBoundary } from './PageErrorBoundary';

const navItems = [
  { path: '/', icon: LayoutDashboard, label: '首页' },
  { path: '/market', icon: TrendingUp, label: '行情' },
  { path: '/strategy', icon: Code2, label: '策略' },
  { path: '/backtest', icon: FlaskConical, label: '回测' },
  { path: '/arbitrage', icon: ArrowLeftRight, label: '套利' },
  { path: '/live', icon: Activity, label: '模拟' },
  { path: '/monitor', icon: Eye, label: '监控' },
  { path: '/data', icon: Database, label: '数据' },
  { path: '/onchain', icon: Network, label: '链上' },
  { path: '/ai-lab', icon: Sparkles, label: 'AI研发' },
];

/** 颜色方案预览卡片 */
function ColorSchemeCard({
  label,
  scheme,
  selected,
  onSelect,
}: {
  label: string;
  scheme: ColorScheme;
  selected: boolean;
  onSelect: () => void;
}) {
  const isRedUp = scheme === 'redUpGreenDown';
  const upColor = isRedUp ? '#FF1744' : '#00C853';
  const downColor = isRedUp ? '#00C853' : '#FF1744';

  return (
    <button
      onClick={onSelect}
      className={clsx(
        'flex h-full flex-col items-center justify-center rounded-xl border p-4 transition-all w-full',
        selected
          ? 'border-blue-500 bg-blue-500/15 shadow-[0_0_0_1px_rgba(59,130,246,0.25)]'
          : 'border-crypto-border hover:border-gray-500 bg-crypto-bg/60'
      )}
    >
      {/* 迷你K线预览 */}
      <div className="flex items-end space-x-1 mb-2 h-10">
        {/* 涨 */}
        <div className="flex flex-col items-center">
          <div className="w-0.5 h-2" style={{ backgroundColor: upColor }} />
          <div className="w-3 h-5 rounded-sm" style={{ backgroundColor: upColor }} />
          <div className="w-0.5 h-1" style={{ backgroundColor: upColor }} />
        </div>
        {/* 跌 */}
        <div className="flex flex-col items-center">
          <div className="w-0.5 h-1" style={{ backgroundColor: downColor }} />
          <div className="w-3 h-4 rounded-sm" style={{ backgroundColor: downColor }} />
          <div className="w-0.5 h-2" style={{ backgroundColor: downColor }} />
        </div>
        {/* 涨 */}
        <div className="flex flex-col items-center">
          <div className="w-0.5 h-1.5" style={{ backgroundColor: upColor }} />
          <div className="w-3 h-6 rounded-sm" style={{ backgroundColor: upColor }} />
          <div className="w-0.5 h-1" style={{ backgroundColor: upColor }} />
        </div>
      </div>
      <span className="text-xs text-gray-300 font-medium">{label}</span>
      <div className="flex items-center space-x-2 mt-1 text-[10px]">
        <span style={{ color: upColor }}>▲ 涨</span>
        <span style={{ color: downColor }}>▼ 跌</span>
      </div>
    </button>
  );
}

function SettingsSection({
  title,
  description,
  icon,
  status,
  children,
}: {
  title: string;
  description?: string;
  icon?: ReactNode;
  status?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="rounded-2xl border border-crypto-border bg-crypto-bg/45 p-5">
      <div className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <div className="flex items-center gap-2 text-sm font-semibold text-gray-100">
            {icon}
            {title}
          </div>
          {description && (
            <p className="mt-1 text-xs leading-relaxed text-gray-500">{description}</p>
          )}
        </div>
        {status}
      </div>
      {children}
    </section>
  );
}

export default function MainLayout() {
  const location = useLocation();
  const { colorScheme, setColorScheme } = useSettingsStore();
  const [showSettings, setShowSettings] = useState(false);
  const [feishuWebhookUrl, setFeishuWebhookUrl] = useState('');
  const [feishuWebhookConfigured, setFeishuWebhookConfigured] = useState(false);
  const [feishuMaskedWebhookUrl, setFeishuMaskedWebhookUrl] = useState<string | null>(null);
  const [feishuSaving, setFeishuSaving] = useState(false);
  const [feishuError, setFeishuError] = useState('');
  const [feishuSaved, setFeishuSaved] = useState(false);
  const [llmConfig, setLlmConfig] = useState<LLMModelSettings | null>(null);
  const [llmModel, setLlmModel] = useState('');
  const [llmNewModel, setLlmNewModel] = useState('');
  const [llmAdding, setLlmAdding] = useState(false);
  const [llmSaving, setLlmSaving] = useState(false);
  const [llmTesting, setLlmTesting] = useState(false);
  const [llmStatus, setLlmStatus] = useState('');
  const settingsRef = useRef<HTMLDivElement>(null);

  // 点击外部关闭设置面板
  useEffect(() => {
    if (!showSettings) return;
    const handleClick = (e: MouseEvent) => {
      if (settingsRef.current && !settingsRef.current.contains(e.target as Node)) {
        setShowSettings(false);
      }
    };
    document.addEventListener('mousedown', handleClick);
    return () => document.removeEventListener('mousedown', handleClick);
  }, [showSettings]);

  useEffect(() => {
    if (!showSettings) return;
    let cancelled = false;
    setFeishuError('');
    setFeishuSaved(false);
    settingsApi.getFeishuWebhook()
      .then((res) => {
        if (cancelled) return;
        setFeishuWebhookConfigured(res.webhookConfigured);
        setFeishuMaskedWebhookUrl(res.maskedWebhookUrl || null);
      })
      .catch(() => {
        if (!cancelled) setFeishuError('读取飞书 Webhook 配置失败');
      });
    settingsApi.getLLMModel()
      .then((res) => {
        if (cancelled) return;
        setLlmConfig(res);
        setLlmModel(res.model);
      })
      .catch(() => {
        if (!cancelled) setLlmStatus('读取大模型配置失败');
      });
    return () => {
      cancelled = true;
    };
  }, [showSettings]);

  const saveFeishuWebhook = async () => {
    const next = feishuWebhookUrl.trim();
    if (!next || feishuSaving) return;
    setFeishuSaving(true);
    setFeishuError('');
    setFeishuSaved(false);
    try {
      const res = await settingsApi.setFeishuWebhook(next);
      setFeishuWebhookConfigured(res.webhookConfigured);
      setFeishuMaskedWebhookUrl(res.maskedWebhookUrl || null);
      setFeishuWebhookUrl('');
      setFeishuSaved(true);
    } catch {
      setFeishuError('保存飞书 Webhook 失败');
    } finally {
      setFeishuSaving(false);
    }
  };

  const saveLLMModel = async () => {
    const next = llmModel.trim();
    if (!next || llmSaving) return;
    setLlmSaving(true);
    setLlmStatus('');
    try {
      const res = await settingsApi.setLLMModel(next);
      setLlmConfig(res);
      setLlmModel(res.model);
      setLlmStatus('大模型配置已保存，后续 AI 研发和行情分析会使用该模型');
    } catch (e: any) {
      setLlmStatus(e?.response?.data?.detail || e.message || '保存大模型配置失败');
    } finally {
      setLlmSaving(false);
    }
  };

  const addLLMModel = async () => {
    const next = llmNewModel.trim();
    if (!next || llmSaving) return;
    setLlmSaving(true);
    setLlmStatus('');
    try {
      const res = await settingsApi.addLLMModel(next);
      setLlmConfig(res);
      setLlmModel(res.model);
      setLlmNewModel('');
      setLlmAdding(false);
      setLlmStatus(`已新增并启用模型：${res.model}`);
    } catch (e: any) {
      setLlmStatus(e?.response?.data?.detail || e.message || '新增模型失败');
    } finally {
      setLlmSaving(false);
    }
  };

  const testLLMModel = async () => {
    if (llmTesting) return;
    setLlmTesting(true);
    setLlmStatus('');
    try {
      const res = await settingsApi.testLLMModel();
      setLlmStatus(`模型连接正常：${res.model} · ${res.reply || 'OK'}`);
    } catch (e: any) {
      setLlmStatus(e?.response?.data?.detail || e.message || '模型连接测试失败');
    } finally {
      setLlmTesting(false);
    }
  };

  return (
    <div className="flex h-screen bg-crypto-bg">
      {/* 侧边栏 */}
      <aside className="w-16 shrink-0 bg-crypto-card border-r border-crypto-border flex flex-col overflow-hidden">
        {/* Logo */}
        <div className="h-16 flex items-center justify-center border-b border-crypto-border">
          <QuantBaseLogo className="h-11 w-11" />
        </div>

        {/* 导航 */}
        <nav className="flex-1 py-4">
          {navItems.map((item) => (
            <NavLink
              key={item.path}
              to={item.path}
              className={({ isActive }) =>
                clsx(
                  'flex flex-col items-center justify-center h-16 text-xs transition-colors overflow-hidden',
                  isActive
                    ? 'text-blue-500 bg-blue-500/10'
                    : 'text-gray-400 hover:text-gray-200 hover:bg-gray-800'
                )
              }
            >
              <item.icon className="w-5 h-5 mb-1" />
              {item.label}
            </NavLink>
          ))}
        </nav>

        {/* 底部: 交易所选择 + 设置 */}
        <div className="border-t border-crypto-border p-1 space-y-1">
          {/* 交易所标识 */}
          <div className="w-full flex items-center justify-center py-1.5 text-[10px] rounded bg-blue-600 text-white font-medium">
            OKX
          </div>
          <button
            onClick={() => setShowSettings(true)}
            className={clsx(
              'w-full flex flex-col items-center justify-center h-10 text-xs rounded transition-colors',
              showSettings
                ? 'text-blue-400 bg-blue-500/10'
                : 'text-gray-400 hover:text-gray-200 hover:bg-gray-800'
            )}
          >
            <Settings className="w-4 h-4" />
          </button>
        </div>
      </aside>

      {/* 主内容区 */}
      <main className="flex-1 overflow-auto min-h-0">
        <PageErrorBoundary resetKey={location.pathname}>
          <Suspense
            fallback={
              <div className="flex min-h-[40vh] items-center justify-center text-sm text-gray-500">
                页面加载中…
              </div>
            }
          >
            <Outlet />
          </Suspense>
        </PageErrorBoundary>
      </main>

      {/* 设置面板 */}
      {showSettings && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/55 px-4 py-6">
          <div
            ref={settingsRef}
            className="flex max-h-[86vh] w-full max-w-4xl flex-col overflow-hidden rounded-2xl border border-crypto-border bg-crypto-card shadow-2xl"
          >
            {/* 头部 */}
            <div className="flex items-start justify-between gap-4 border-b border-crypto-border px-6 py-5">
              <div>
                <h3 className="text-base font-semibold text-white">设置</h3>
                <p className="mt-1 text-xs text-gray-500">显示偏好、AI 模型和通知配置集中管理。</p>
              </div>
              <button
                onClick={() => setShowSettings(false)}
                className="rounded-lg p-1.5 text-gray-400 transition-colors hover:bg-gray-700 hover:text-white"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            {/* 内容 */}
            <div className="space-y-5 overflow-y-auto px-6 py-5">
              {/* K线颜色方案 */}
              <SettingsSection
                title="显示偏好"
                description="选择全站 K 线和涨跌箭头的颜色口径。"
              >
                <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                  <ColorSchemeCard
                    label="红涨绿跌"
                    scheme="redUpGreenDown"
                    selected={colorScheme === 'redUpGreenDown'}
                    onSelect={() => setColorScheme('redUpGreenDown')}
                  />
                  <ColorSchemeCard
                    label="绿涨红跌"
                    scheme="greenUpRedDown"
                    selected={colorScheme === 'greenUpRedDown'}
                    onSelect={() => setColorScheme('greenUpRedDown')}
                  />
                </div>
              </SettingsSection>

              <SettingsSection
                title="大模型配置"
                icon={<Cpu className="h-4 w-4 text-blue-400" />}
                description={`DashScope · ${llmConfig?.baseUrl || 'https://dashscope.aliyuncs.com/compatible-mode/v1'}${llmConfig?.requestTimeout ? ` · 超时 ${llmConfig.requestTimeout}s` : ''}${llmConfig?.enableThinking ? ' · thinking 开启' : ' · thinking 关闭'}`}
                status={
                  <div
                    className={clsx(
                      'inline-flex shrink-0 items-center gap-1 rounded-full border px-2 py-1 text-[10px]',
                      llmConfig?.apiKeyConfigured
                        ? 'border-green-500/30 bg-green-500/10 text-green-400'
                        : 'border-red-500/30 bg-red-500/10 text-red-400',
                    )}
                  >
                    {llmConfig?.apiKeyConfigured ? <CheckCircle2 className="h-3 w-3" /> : <AlertCircle className="h-3 w-3" />}
                    {llmConfig?.apiKeyConfigured
                      ? `${llmConfig.apiKeySource || 'DASHSCOPE_API_KEY'} 已配置`
                      : 'DASHSCOPE_API_KEY 未配置'}
                  </div>
                }
              >
                <div className="grid grid-cols-1 gap-2 lg:grid-cols-[minmax(0,1fr)_auto_auto_auto]">
                  <CryptoSelect
                    value={llmModel}
                    onChange={(e) => {
                      setLlmModel(e.target.value);
                      setLlmStatus('');
                    }}
                    disabled={!llmConfig}
                    wrapperClassName="min-w-0"
                  >
                    {(llmConfig?.models?.length ? llmConfig.models : [llmModel || llmConfig?.defaultModel || 'qwen3.6-plus'])
                      .filter(Boolean)
                      .map((model) => (
                        <option key={model} value={model}>
                          {model}
                        </option>
                      ))}
                  </CryptoSelect>
                  <button
                    type="button"
                    onClick={() => {
                      setLlmAdding((value) => !value);
                      setLlmStatus('');
                    }}
                    className="inline-flex h-10 items-center justify-center gap-2 rounded-lg border border-crypto-border px-4 text-sm font-medium text-gray-200 transition-colors hover:border-blue-500 hover:text-blue-300"
                  >
                    <Plus className="h-4 w-4" />
                    新增模型
                  </button>
                  <button
                    type="button"
                    onClick={() => void saveLLMModel()}
                    disabled={llmSaving || !llmModel.trim()}
                    className="inline-flex h-10 items-center justify-center gap-2 rounded-lg border border-blue-500/40 bg-blue-600/15 px-4 text-sm font-medium text-blue-300 transition-colors hover:bg-blue-600/25 disabled:cursor-not-allowed disabled:border-crypto-border disabled:bg-crypto-bg disabled:text-gray-600"
                  >
                    <Cpu className="h-4 w-4" />
                    {llmSaving ? '保存中' : '保存模型'}
                  </button>
                  <button
                    type="button"
                    onClick={() => void testLLMModel()}
                    disabled={llmTesting || !llmConfig?.apiKeyConfigured}
                    className="inline-flex h-10 items-center justify-center gap-2 rounded-lg border border-crypto-border px-4 text-sm font-medium text-gray-200 transition-colors hover:border-blue-500 hover:text-blue-300 disabled:cursor-not-allowed disabled:text-gray-600"
                  >
                    <PlugZap className="h-4 w-4" />
                    {llmTesting ? '测试中' : '测试连接'}
                  </button>
                </div>
                {llmAdding && (
                  <div className="mt-2 grid grid-cols-1 gap-2 lg:grid-cols-[minmax(0,1fr)_auto]">
                    <input
                      value={llmNewModel}
                      onChange={(e) => {
                        setLlmNewModel(e.target.value);
                        setLlmStatus('');
                      }}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter') void addLLMModel();
                        if (e.key === 'Escape') {
                          setLlmAdding(false);
                          setLlmNewModel('');
                        }
                      }}
                      placeholder="输入 DashScope 兼容模型名，如 deepseek-v4-flash"
                      className="h-10 min-w-0 rounded-lg border border-crypto-border bg-crypto-bg px-3 text-sm text-white outline-none placeholder:text-gray-600 focus:border-blue-500/60"
                    />
                    <button
                      type="button"
                      onClick={() => void addLLMModel()}
                      disabled={llmSaving || !llmNewModel.trim()}
                      className="inline-flex h-10 items-center justify-center gap-2 rounded-lg border border-green-500/40 bg-green-600/15 px-4 text-sm font-medium text-green-300 transition-colors hover:bg-green-600/25 disabled:cursor-not-allowed disabled:border-crypto-border disabled:bg-crypto-bg disabled:text-gray-600"
                    >
                      <Plus className="h-4 w-4" />
                      确认新增
                    </button>
                  </div>
                )}
                {llmStatus && (
                  <div
                    className={clsx(
                      'mt-2 text-[11px]',
                      llmStatus.includes('失败') || llmStatus.includes('未配置') || llmStatus.includes('不能为空')
                        ? 'text-red-400'
                        : 'text-green-400',
                    )}
                  >
                    {llmStatus}
                  </div>
                )}
                <div className="mt-2 text-[11px] leading-relaxed text-gray-500">
                  AI策略助手、现有策略优化等大模型调用共用此配置；API Key 仍只从服务器环境变量读取，不在浏览器保存。
                  {llmConfig?.freeTierModels?.length
                    ? ` 百炼免费候选池已内置 ${llmConfig.freeTierModels.length} 个模型，AI自主交易会在免费额度耗尽时自动尝试下一个候选。`
                    : ''}
                </div>
              </SettingsSection>

              <SettingsSection
                title="飞书 Webhook"
                description={feishuWebhookConfigured ? (feishuMaskedWebhookUrl || '已配置') : '未配置'}
                status={
                  <div
                    className={clsx(
                      'inline-flex shrink-0 items-center gap-1 rounded-full border px-2 py-1 text-[10px]',
                      feishuWebhookConfigured
                        ? 'border-green-500/30 bg-green-500/10 text-green-400'
                        : 'border-crypto-border bg-crypto-bg text-gray-500',
                    )}
                  >
                    {feishuWebhookConfigured ? <CheckCircle2 className="h-3 w-3" /> : <AlertCircle className="h-3 w-3" />}
                    {feishuWebhookConfigured ? '已配置' : '未配置'}
                  </div>
                }
              >
                <div className="flex flex-col gap-2 sm:flex-row">
                  <input
                    type="password"
                    value={feishuWebhookUrl}
                    onChange={(e) => {
                      setFeishuWebhookUrl(e.target.value);
                      setFeishuSaved(false);
                    }}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') void saveFeishuWebhook();
                    }}
                    placeholder={feishuWebhookConfigured ? '已配置，留空不修改' : '粘贴飞书机器人 Webhook URL'}
                    className="h-10 min-w-0 flex-1 rounded-lg border border-crypto-border bg-crypto-bg px-3 text-sm text-white outline-none placeholder:text-gray-600 focus:border-blue-500/60"
                  />
                  <button
                    type="button"
                    onClick={() => void saveFeishuWebhook()}
                    disabled={feishuSaving || !feishuWebhookUrl.trim()}
                    className="inline-flex h-10 items-center justify-center gap-2 rounded-lg border border-blue-500/40 bg-blue-600/15 px-4 text-sm font-medium text-blue-300 transition-colors hover:bg-blue-600/25 disabled:cursor-not-allowed disabled:border-crypto-border disabled:bg-crypto-bg disabled:text-gray-600"
                  >
                    <Send className="h-4 w-4" />
                    {feishuSaving ? '保存中' : '保存'}
                  </button>
                </div>
                {(feishuError || feishuSaved) && (
                  <div className={clsx('mt-2 text-[11px]', feishuError ? 'text-red-400' : 'text-green-400')}>
                    {feishuError || '已保存'}
                  </div>
                )}
              </SettingsSection>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
