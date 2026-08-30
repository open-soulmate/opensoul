'use client';

import { useEffect, useState, useCallback } from 'react';
import { apiFetch } from '@/lib/api';
import { Settings, Database, HardDrive, Cpu, RefreshCw, ToggleLeft, ToggleRight, Server, Clock, Shield, X, Save } from 'lucide-react';

interface SystemInfo {
  status: string;
  version: string;
  database: string;
  uptime: string;
}

interface Organ {
  key: string;
  enabled: boolean;
  config: Record<string, any>;
}

type Tab = 'overview' | 'organs' | 'config';

export default function SystemPage() {
  const [info, setInfo] = useState<SystemInfo | null>(null);
  const [organs, setOrgans] = useState<Organ[]>([]);
  const [config, setConfig] = useState<Record<string, any>>({});
  const [configText, setConfigText] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [tab, setTab] = useState<Tab>('overview');
  const [saving, setSaving] = useState(false);

  const fetchOverview = useCallback(async () => {
    try {
      const [health, overview] = await Promise.all([
        apiFetch('/api/health').catch(() => null),
        apiFetch('/api/system/overview').catch(() => null),
      ]);
      setInfo({
        status: (health as any)?.status || 'ok',
        version: (health as any)?.version || (overview as any)?.version || '—',
        database: (overview as any)?.database || 'SQLite',
        uptime: (overview as any)?.uptime || '—',
      });
    } catch { /* ignore */ }
  }, []);

  const fetchOrgans = useCallback(async () => {
    try {
      const data = await apiFetch('/api/config/organs');
      setOrgans(data.organs || []);
    } catch { /* ignore */ }
  }, []);

  const fetchConfig = useCallback(async () => {
    try {
      const data = await apiFetch('/api/config');
      setConfig(data);
      setConfigText(JSON.stringify(data, null, 2));
    } catch { /* ignore */ }
  }, []);

  const fetchData = useCallback(async () => {
    setLoading(true);
    setError('');
    await Promise.all([fetchOverview(), fetchOrgans(), fetchConfig()]);
    setLoading(false);
  }, [fetchOverview, fetchOrgans, fetchConfig]);

  useEffect(() => { fetchData(); }, [fetchData]);

  const toggleOrgan = async (organKey: string, currentEnabled: boolean) => {
    try {
      await apiFetch(`/api/config/organs/${organKey}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled: !currentEnabled }),
      });
      setOrgans((prev) =>
        prev.map((o) => o.key === organKey ? { ...o, enabled: !currentEnabled } : o)
      );
    } catch (e: any) {
      setError(e.message);
    }
  };

  const saveConfig = async () => {
    try {
      setSaving(true);
      const parsed = JSON.parse(configText);
      await apiFetch('/api/config', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ data: parsed }),
      });
      setConfig(parsed);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setSaving(false);
    }
  };

  const enabledCount = organs.filter((o) => o.enabled).length;

  if (loading) return <div className="text-sm text-muted-foreground p-4">加载中...</div>;

  return (
    <div className="space-y-4">
      {error && (
        <div className="rounded-lg border border-red-200 bg-red-50 dark:bg-red-950/20 dark:border-red-800 p-3 text-xs text-red-600 dark:text-red-400 flex items-center justify-between">
          <span>{error}</span>
          <button onClick={() => setError('')} className="p-0.5 hover:bg-red-100 dark:hover:bg-red-900/30 rounded"><X className="w-3.5 h-3.5" /></button>
        </div>
      )}

      {/* Stats */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="flex items-center gap-1.5 text-muted-foreground text-xs mb-1"><Server className="w-3.5 h-3.5" />状态</div>
          <div className="text-sm font-medium text-green-600">{info?.status || '—'}</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="flex items-center gap-1.5 text-muted-foreground text-xs mb-1"><Database className="w-3.5 h-3.5" />数据库</div>
          <div className="text-sm font-medium">{info?.database || '—'}</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="flex items-center gap-1.5 text-muted-foreground text-xs mb-1"><HardDrive className="w-3.5 h-3.5" />版本</div>
          <div className="text-sm font-medium">{info?.version || '—'}</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="flex items-center gap-1.5 text-muted-foreground text-xs mb-1"><Cpu className="w-3.5 h-3.5" />器官</div>
          <div className="text-sm font-medium">{enabledCount}/{organs.length} 启用</div>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex items-center gap-1 border-b border-border">
        {([
          { key: 'overview', label: '系统概览', icon: Server },
          { key: 'organs', label: '器官管理', icon: Cpu },
          { key: 'config', label: '配置编辑', icon: Settings },
        ] as { key: Tab; label: string; icon: any }[]).map((t) => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            className={`flex items-center gap-1.5 px-3 py-2 text-xs font-medium border-b-2 transition-colors ${
              tab === t.key
                ? 'border-primary text-primary'
                : 'border-transparent text-muted-foreground hover:text-foreground'
            }`}
          >
            <t.icon className="w-3.5 h-3.5" />
            {t.label}
          </button>
        ))}
        <div className="flex-1" />
        <button
          onClick={fetchData}
          className="flex items-center gap-1.5 px-3 py-1.5 text-xs bg-muted border border-border rounded-md hover:bg-muted/80 mb-1"
        >
          <RefreshCw className="w-3.5 h-3.5" />刷新
        </button>
      </div>

      {/* Overview Tab */}
      {tab === 'overview' && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div className="rounded-lg border border-border bg-card p-4">
            <h3 className="text-xs font-medium text-muted-foreground mb-3">系统信息</h3>
            <div className="space-y-2">
              {[
                { label: '状态', value: info?.status },
                { label: '版本', value: info?.version },
                { label: '数据库', value: info?.database },
                { label: '运行时间', value: info?.uptime },
              ].map((item) => (
                <div key={item.label} className="flex items-center justify-between text-xs">
                  <span className="text-muted-foreground">{item.label}</span>
                  <span className="font-medium">{item.value || '—'}</span>
                </div>
              ))}
            </div>
          </div>
          <div className="rounded-lg border border-border bg-card p-4">
            <h3 className="text-xs font-medium text-muted-foreground mb-3">器官状态</h3>
            <div className="space-y-1.5 max-h-[300px] overflow-auto">
              {organs.map((o) => (
                <div key={o.key} className="flex items-center justify-between text-xs py-1">
                  <span className="font-mono">{o.key}</span>
                  <span className={o.enabled ? 'text-green-600' : 'text-muted-foreground'}>
                    {o.enabled ? '启用' : '禁用'}
                  </span>
                </div>
              ))}
              {organs.length === 0 && <div className="text-xs text-muted-foreground">暂无器官</div>}
            </div>
          </div>
        </div>
      )}

      {/* Organs Tab */}
      {tab === 'organs' && (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
          {organs.map((organ) => (
            <div
              key={organ.key}
              className={`rounded-lg border bg-card p-4 transition-colors ${
                organ.enabled ? 'border-green-200 dark:border-green-900' : 'border-border opacity-60'
              }`}
            >
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <div className={`w-2 h-2 rounded-full ${organ.enabled ? 'bg-green-500' : 'bg-gray-400'}`} />
                  <span className="text-sm font-medium font-mono">{organ.key}</span>
                </div>
                <button
                  onClick={() => toggleOrgan(organ.key, organ.enabled)}
                  className="p-0.5"
                  title={organ.enabled ? '点击禁用' : '点击启用'}
                >
                  {organ.enabled ? (
                    <ToggleRight className="w-7 h-7 text-green-500" />
                  ) : (
                    <ToggleLeft className="w-7 h-7 text-muted-foreground" />
                  )}
                </button>
              </div>
              {Object.keys(organ.config).length > 0 && (
                <div className="mt-2 text-[10px] text-muted-foreground font-mono bg-muted rounded p-2 max-h-[60px] overflow-hidden">
                  {JSON.stringify(organ.config, null, 1).slice(0, 100)}
                </div>
              )}
            </div>
          ))}
          {organs.length === 0 && (
            <div className="col-span-full flex flex-col items-center justify-center py-12 text-muted-foreground">
              <Cpu className="w-10 h-10 mb-2 opacity-30" />
              <p className="text-xs">暂无器官配置</p>
            </div>
          )}
        </div>
      )}

      {/* Config Tab */}
      {tab === 'config' && (
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <p className="text-xs text-muted-foreground">直接编辑系统配置 JSON（谨慎操作）</p>
            <button
              onClick={saveConfig}
              disabled={saving}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs bg-primary text-primary-foreground rounded-md hover:opacity-90 disabled:opacity-50"
            >
              <Save className="w-3.5 h-3.5" />{saving ? '保存中...' : '保存配置'}
            </button>
          </div>
          <textarea
            className="w-full h-[500px] p-4 text-xs font-mono bg-muted border border-border rounded-lg focus:outline-none focus:ring-1 focus:ring-primary/50 resize-y"
            value={configText}
            onChange={(e) => setConfigText(e.target.value)}
            spellCheck={false}
          />
        </div>
      )}
    </div>
  );
}
