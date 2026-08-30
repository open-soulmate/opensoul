'use client';

import { useEffect, useState, useCallback, useRef } from 'react';
import { apiFetch } from '@/lib/api';
import {
  Search, ScrollText, RefreshCw, X, Filter, Download,
  Loader2, AlertCircle, Info, AlertTriangle, CheckCircle,
  ChevronDown, ChevronUp, Terminal
} from 'lucide-react';

interface LogEntry {
  id: string;
  type: string;
  organ: string;
  emoji: string;
  summary: string;
  timestamp: number;
  metadata?: Record<string, any>;
}

interface EventSummary {
  total_events: number;
  by_type: Record<string, number>;
  by_organ: Record<string, number>;
  recent_count: number;
}

export default function LogsPage() {
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [summary, setSummary] = useState<EventSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [search, setSearch] = useState('');
  const [typeFilter, setTypeFilter] = useState('');
  const [organFilter, setOrganFilter] = useState('');
  const [autoRefresh, setAutoRefresh] = useState(false);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  const fetchLogs = useCallback(async () => {
    try {
      setLoading(true);
      const data = await apiFetch('/api/events/stream?limit=200');
      setLogs(Array.isArray(data.events) ? data.events : []);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  const fetchSummary = useCallback(async () => {
    try {
      const data = await apiFetch('/api/events/summary');
      setSummary(data);
    } catch {
      // silent
    }
  }, []);

  useEffect(() => { fetchLogs(); fetchSummary(); }, [fetchLogs, fetchSummary]);

  useEffect(() => {
    if (!autoRefresh) return;
    const iv = setInterval(() => { fetchLogs(); fetchSummary(); }, 5000);
    return () => clearInterval(iv);
  }, [autoRefresh, fetchLogs, fetchSummary]);

  const types = Array.from(new Set(logs.map((l) => l.type))).sort();
  const organs = Array.from(new Set(logs.map((l) => l.organ))).sort();

  const filtered = logs.filter((l) => {
    if (typeFilter && l.type !== typeFilter) return false;
    if (organFilter && l.organ !== organFilter) return false;
    if (search) {
      const q = search.toLowerCase();
      return l.summary.toLowerCase().includes(q) || l.organ.toLowerCase().includes(q) || l.type.toLowerCase().includes(q);
    }
    return true;
  });

  const formatTime = (ts: number) => {
    if (!ts) return '';
    const d = new Date(ts * 1000);
    return d.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' });
  };

  const getTypeColor = (type: string) => {
    if (['error', 'alert', 'ip_blacklisted', 'content_blocked'].includes(type)) return 'text-red-500';
    if (['warning', 'rate_limited'].includes(type)) return 'text-orange-500';
    if (['backup_created', 'file_uploaded', 'component_registered'].includes(type)) return 'text-green-500';
    return 'text-blue-500';
  };

  const getTypeBg = (type: string) => {
    if (['error', 'alert', 'ip_blacklisted', 'content_blocked'].includes(type)) return 'bg-red-500/10';
    if (['warning', 'rate_limited'].includes(type)) return 'bg-orange-500/10';
    if (['backup_created', 'file_uploaded', 'component_registered'].includes(type)) return 'bg-green-500/10';
    return 'bg-blue-500/10';
  };

  const handleExport = () => {
    const csv = filtered.map((l) => `"${formatTime(l.timestamp)}","${l.type}","${l.organ}","${l.summary}"`).join('\n');
    const header = '时间,类型,器官,摘要\n';
    const blob = new Blob([header + csv], { type: 'text/csv;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `opensoul-logs-${new Date().toISOString().slice(0, 10)}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="space-y-4">
      {/* Summary stats */}
      {summary && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          <div className="rounded-lg border border-border bg-card p-3">
            <div className="text-2xl font-bold">{summary.total_events}</div>
            <div className="text-xs text-muted-foreground">总事件数</div>
          </div>
          <div className="rounded-lg border border-border bg-card p-3">
            <div className="text-2xl font-bold text-blue-500">{summary.recent_count}</div>
            <div className="text-xs text-muted-foreground">最近事件</div>
          </div>
          <div className="rounded-lg border border-border bg-card p-3">
            <div className="text-2xl font-bold text-red-500">{(summary.by_type?.error || 0) + (summary.by_type?.alert || 0)}</div>
            <div className="text-xs text-muted-foreground">错误/告警</div>
          </div>
          <div className="rounded-lg border border-border bg-card p-3">
            <div className="text-2xl font-bold">{Object.keys(summary.by_organ || {}).length}</div>
            <div className="text-xs text-muted-foreground">活跃器官</div>
          </div>
        </div>
      )}

      {/* Toolbar */}
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative flex-1 min-w-[200px]">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-muted-foreground" />
          <input
            className="w-full pl-8 pr-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50"
            placeholder="搜索日志..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        <select
          className="px-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none"
          value={typeFilter}
          onChange={(e) => setTypeFilter(e.target.value)}
        >
          <option value="">全部类型</option>
          {types.map((t) => <option key={t} value={t}>{t}</option>)}
        </select>
        <select
          className="px-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none"
          value={organFilter}
          onChange={(e) => setOrganFilter(e.target.value)}
        >
          <option value="">全部器官</option>
          {organs.map((o) => <option key={o} value={o}>{o}</option>)}
        </select>
        <label className="flex items-center gap-1.5 text-xs cursor-pointer">
          <input type="checkbox" checked={autoRefresh} onChange={(e) => setAutoRefresh(e.target.checked)} className="rounded" />
          实时
        </label>
        <button onClick={() => { fetchLogs(); fetchSummary(); }} className="flex items-center gap-1.5 px-3 py-1.5 text-xs bg-muted border border-border rounded-md hover:bg-muted/80">
          <RefreshCw className="w-3.5 h-3.5" />刷新
        </button>
        <button onClick={handleExport} className="flex items-center gap-1.5 px-3 py-1.5 text-xs bg-muted border border-border rounded-md hover:bg-muted/80">
          <Download className="w-3.5 h-3.5" />导出CSV
        </button>
      </div>

      {/* Error */}
      {error && (
        <div className="flex items-center gap-2 p-2 text-xs text-red-500 bg-red-500/5 rounded-md border border-red-500/20">
          <AlertCircle className="w-3.5 h-3.5 shrink-0" />
          <span className="flex-1">{error}</span>
          <button onClick={() => setError('')}><X className="w-3 h-3" /></button>
        </div>
      )}

      {/* Log entries */}
      <div ref={containerRef} className="rounded-lg border border-border bg-card overflow-hidden">
        <div className="max-h-[600px] overflow-auto">
          {filtered.map((log) => (
            <div
              key={log.id}
              className={`border-b border-border hover:bg-muted/30 cursor-pointer transition-colors ${expandedId === log.id ? 'bg-muted/20' : ''}`}
              onClick={() => setExpandedId(expandedId === log.id ? null : log.id)}
            >
              <div className="flex items-start gap-2 px-3 py-2">
                <span className="text-lg shrink-0">{log.emoji || '📋'}</span>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className={`text-[10px] px-1.5 py-0.5 rounded font-mono ${getTypeBg(log.type)} ${getTypeColor(log.type)}`}>{log.type}</span>
                    <span className="text-[10px] px-1.5 py-0.5 rounded bg-muted text-muted-foreground">{log.organ}</span>
                    <span className="text-[10px] text-muted-foreground">{formatTime(log.timestamp)}</span>
                  </div>
                  <p className="text-xs mt-0.5 break-words">{log.summary}</p>
                </div>
                {expandedId === log.id ? <ChevronUp className="w-3 h-3 text-muted-foreground shrink-0" /> : <ChevronDown className="w-3 h-3 text-muted-foreground shrink-0" />}
              </div>
              {expandedId === log.id && log.metadata && Object.keys(log.metadata).length > 0 && (
                <div className="px-3 pb-2 ml-7">
                  <pre className="text-[10px] bg-muted p-2 rounded font-mono whitespace-pre-wrap overflow-x-auto">
                    {JSON.stringify(log.metadata, null, 2)}
                  </pre>
                </div>
              )}
            </div>
          ))}
          {filtered.length === 0 && (
            <div className="flex flex-col items-center justify-center py-12 text-muted-foreground">
              <Terminal className="w-10 h-10 mb-2 opacity-30" />
              <p className="text-xs">{search || typeFilter || organFilter ? '没有匹配的日志' : '暂无日志'}</p>
            </div>
          )}
        </div>
      </div>

      {/* Footer */}
      <div className="text-[10px] text-muted-foreground text-center">
        显示 {filtered.length}/{logs.length} 条日志 | 事件流: /api/events/stream
      </div>
    </div>
  );
}
