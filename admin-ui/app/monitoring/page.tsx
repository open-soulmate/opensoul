'use client';

import { useEffect, useState, useCallback } from 'react';
import { apiFetch } from '@/lib/api';
import {
  Activity, RefreshCw, CheckCircle, XCircle, Clock, Server,
  Cpu, HardDrive, MemoryStick, Wifi, Loader2
} from 'lucide-react';

interface OrganStatus {
  key: string;
  label: string;
  category: string;
  status: string;
  status_code: number;
  response_time_ms: number;
  error?: string;
}

interface SystemInfo {
  hostname: string;
  os: string;
  arch: string;
  python: string;
  cpu_count: number;
  cpu_percent?: number;
  memory_total_gb?: number;
  memory_used_gb?: number;
  memory_percent?: number;
  disk_total_gb?: number;
  disk_used_gb?: number;
  disk_percent?: number;
  uptime_seconds?: number;
  load_avg_1m?: number;
  load_avg_5m?: number;
  load_avg_15m?: number;
}

interface CheckAllResult {
  summary: {
    total: number;
    healthy: number;
    unhealthy: number;
    avg_response_ms: number;
    max_response_ms: number;
    overall: string;
  };
  system: SystemInfo;
  organs: OrganStatus[];
}

export default function MonitoringPage() {
  const [data, setData] = useState<CheckAllResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [lastRefresh, setLastRefresh] = useState<Date | null>(null);
  const [categoryFilter, setCategoryFilter] = useState('all');
  const [statusFilter, setStatusFilter] = useState('all');
  const [autoRefresh, setAutoRefresh] = useState(false);

  const fetchData = useCallback(async () => {
    try {
      setLoading(true);
      const result = await apiFetch('/api/diagnostics/check-all');
      setData(result);
      setLastRefresh(new Date());
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchData(); }, [fetchData]);

  useEffect(() => {
    if (!autoRefresh) return;
    const iv = setInterval(fetchData, 15000);
    return () => clearInterval(iv);
  }, [autoRefresh, fetchData]);

  const organs = data?.organs || [];
  const categories = Array.from(new Set(organs.map((o) => o.category))).sort();

  const filtered = organs.filter((o) => {
    if (categoryFilter !== 'all' && o.category !== categoryFilter) return false;
    if (statusFilter === 'ok' && o.status !== 'ok') return false;
    if (statusFilter === 'error' && o.status !== 'error') return false;
    return true;
  });

  const formatUptime = (seconds?: number) => {
    if (!seconds) return '-';
    const days = Math.floor(seconds / 86400);
    const hours = Math.floor((seconds % 86400) / 3600);
    const mins = Math.floor((seconds % 3600) / 60);
    if (days > 0) return `${days}天${hours}小时`;
    if (hours > 0) return `${hours}小时${mins}分钟`;
    return `${mins}分钟`;
  };

  return (
    <div className="space-y-4">
      {/* Summary cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="flex items-center gap-2">
            <Activity className="w-4 h-4 text-primary" />
            <span className="text-xs text-muted-foreground">总体状态</span>
          </div>
          <div className={`text-lg font-bold mt-1 ${data?.summary?.overall === 'ok' ? 'text-green-600' : 'text-orange-500'}`}>
            {data?.summary?.overall === 'ok' ? '正常' : '降级'}
          </div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="flex items-center gap-2">
            <CheckCircle className="w-4 h-4 text-green-500" />
            <span className="text-xs text-muted-foreground">健康组件</span>
          </div>
          <div className="text-lg font-bold mt-1">{data?.summary?.healthy || 0}/{data?.summary?.total || 0}</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="flex items-center gap-2">
            <Clock className="w-4 h-4 text-blue-500" />
            <span className="text-xs text-muted-foreground">平均响应</span>
          </div>
          <div className="text-lg font-bold mt-1">{data?.summary?.avg_response_ms || 0}ms</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="flex items-center gap-2">
            <Server className="w-4 h-4 text-purple-500" />
            <span className="text-xs text-muted-foreground">系统运行</span>
          </div>
          <div className="text-lg font-bold mt-1">{formatUptime(data?.system?.uptime_seconds)}</div>
        </div>
      </div>

      {/* System resources */}
      {data?.system && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          {data.system.cpu_percent !== undefined && (
            <div className="rounded-lg border border-border bg-card p-3">
              <div className="flex items-center gap-2 text-xs text-muted-foreground">
                <Cpu className="w-3.5 h-3.5" />CPU ({data.system.cpu_count}核)
              </div>
              <div className="mt-2">
                <div className="flex items-center justify-between text-xs mb-1">
                  <span className="font-medium">{data.system.cpu_percent}%</span>
                </div>
                <div className="w-full bg-muted rounded-full h-1.5">
                  <div
                    className={`h-1.5 rounded-full transition-all ${data.system.cpu_percent > 80 ? 'bg-red-500' : data.system.cpu_percent > 60 ? 'bg-orange-500' : 'bg-green-500'}`}
                    style={{ width: `${data.system.cpu_percent}%` }}
                  />
                </div>
              </div>
            </div>
          )}
          {data.system.memory_percent !== undefined && (
            <div className="rounded-lg border border-border bg-card p-3">
              <div className="flex items-center gap-2 text-xs text-muted-foreground">
                <MemoryStick className="w-3.5 h-3.5" />内存
              </div>
              <div className="mt-2">
                <div className="flex items-center justify-between text-xs mb-1">
                  <span className="font-medium">{data.system.memory_percent}%</span>
                  <span className="text-muted-foreground">{data.system.memory_used_gb}/{data.system.memory_total_gb}GB</span>
                </div>
                <div className="w-full bg-muted rounded-full h-1.5">
                  <div
                    className={`h-1.5 rounded-full transition-all ${data.system.memory_percent > 80 ? 'bg-red-500' : data.system.memory_percent > 60 ? 'bg-orange-500' : 'bg-green-500'}`}
                    style={{ width: `${data.system.memory_percent}%` }}
                  />
                </div>
              </div>
            </div>
          )}
          {data.system.disk_percent !== undefined && (
            <div className="rounded-lg border border-border bg-card p-3">
              <div className="flex items-center gap-2 text-xs text-muted-foreground">
                <HardDrive className="w-3.5 h-3.5" />磁盘
              </div>
              <div className="mt-2">
                <div className="flex items-center justify-between text-xs mb-1">
                  <span className="font-medium">{data.system.disk_percent}%</span>
                  <span className="text-muted-foreground">{data.system.disk_used_gb}/{data.system.disk_total_gb}GB</span>
                </div>
                <div className="w-full bg-muted rounded-full h-1.5">
                  <div
                    className={`h-1.5 rounded-full transition-all ${data.system.disk_percent > 80 ? 'bg-red-500' : data.system.disk_percent > 60 ? 'bg-orange-500' : 'bg-green-500'}`}
                    style={{ width: `${data.system.disk_percent}%` }}
                  />
                </div>
              </div>
            </div>
          )}
          <div className="rounded-lg border border-border bg-card p-3">
            <div className="flex items-center gap-2 text-xs text-muted-foreground">
              <Wifi className="w-3.5 h-3.5" />系统信息
            </div>
            <div className="mt-1 text-[10px] space-y-0.5">
              <div>{data.system.hostname}</div>
              <div>{data.system.os}</div>
              <div>Python {data.system.python}</div>
            </div>
          </div>
        </div>
      )}

      {/* Toolbar */}
      <div className="flex flex-wrap items-center gap-2">
        <select
          className="px-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none"
          value={categoryFilter}
          onChange={(e) => setCategoryFilter(e.target.value)}
        >
          <option value="all">全部分类</option>
          {categories.map((c) => <option key={c} value={c}>{c}</option>)}
        </select>
        <select
          className="px-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none"
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value)}
        >
          <option value="all">全部状态</option>
          <option value="ok">健康</option>
          <option value="error">异常</option>
        </select>
        <label className="flex items-center gap-1.5 text-xs cursor-pointer">
          <input type="checkbox" checked={autoRefresh} onChange={(e) => setAutoRefresh(e.target.checked)} className="rounded" />
          自动刷新(15s)
        </label>
        <button onClick={fetchData} disabled={loading} className="flex items-center gap-1.5 px-3 py-1.5 text-xs bg-muted border border-border rounded-md hover:bg-muted/80 disabled:opacity-50">
          {loading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <RefreshCw className="w-3.5 h-3.5" />}刷新
        </button>
        {lastRefresh && <span className="text-[10px] text-muted-foreground">上次刷新: {lastRefresh.toLocaleTimeString('zh-CN')}</span>}
      </div>

      {/* Organ grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-2">
        {filtered.map((organ) => (
          <div key={organ.key} className={`rounded-lg border p-3 transition-colors ${organ.status === 'ok' ? 'border-border bg-card' : 'border-red-500/20 bg-red-500/5'}`}>
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2 min-w-0">
                {organ.status === 'ok' ? (
                  <CheckCircle className="w-4 h-4 text-green-500 shrink-0" />
                ) : (
                  <XCircle className="w-4 h-4 text-red-500 shrink-0" />
                )}
                <div className="min-w-0">
                  <h4 className="text-xs font-medium truncate">{organ.label}</h4>
                  <span className="text-[10px] text-muted-foreground">{organ.category}</span>
                </div>
              </div>
              <div className="text-right shrink-0">
                <div className="text-xs font-mono">{organ.response_time_ms}ms</div>
                {organ.status_code > 0 && <div className="text-[10px] text-muted-foreground">HTTP {organ.status_code}</div>}
              </div>
            </div>
            {organ.error && <p className="text-[10px] text-red-500 mt-1 truncate">{organ.error}</p>}
          </div>
        ))}
      </div>

      {filtered.length === 0 && (
        <div className="text-center py-8 text-xs text-muted-foreground">没有匹配的组件</div>
      )}
    </div>
  );
}
