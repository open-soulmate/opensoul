'use client';

import { useEffect, useState } from 'react';
import { apiFetch } from '@/lib/api';
import { Users, BookOpen, Activity, Server } from 'lucide-react';

interface HealthData {
  status: string;
  version: string;
  knowledge_entries: number;
  uptime_check_ms: number;
  components: Record<string, { status: string }>;
}

export default function DashboardPage() {
  const [health, setHealth] = useState<HealthData | null>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    apiFetch('/api/health/all')
      .then((d) => setHealth(d as HealthData))
      .catch((e) => setError(e.message));
  }, []);

  if (error) return <div className="text-sm text-red-500 p-4">错误: {error}</div>;
  if (!health) return <div className="text-sm text-muted-foreground p-4">加载中...</div>;

  const components = health.components || {};
  const okCount = Object.values(components).filter((c) => c.status === 'ok').length;
  const totalCount = Object.keys(components).length;

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        <div className="rounded-lg border border-border bg-card p-4">
          <div className="flex items-center gap-2 text-muted-foreground text-xs mb-2"><Activity className="w-3.5 h-3.5" />系统状态</div>
          <div className="text-2xl font-bold">{health.status === 'ok' ? '正常' : '异常'}</div>
          <div className="text-xs text-muted-foreground mt-1">v{health.version}</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-4">
          <div className="flex items-center gap-2 text-muted-foreground text-xs mb-2"><Server className="w-3.5 h-3.5" />器官</div>
          <div className="text-2xl font-bold">{okCount}/{totalCount}</div>
          <div className="text-xs text-muted-foreground mt-1">正常运行</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-4">
          <div className="flex items-center gap-2 text-muted-foreground text-xs mb-2"><BookOpen className="w-3.5 h-3.5" />知识库</div>
          <div className="text-2xl font-bold">{health.knowledge_entries ?? 0}</div>
          <div className="text-xs text-muted-foreground mt-1">条目</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-4">
          <div className="flex items-center gap-2 text-muted-foreground text-xs mb-2"><Users className="w-3.5 h-3.5" />响应</div>
          <div className="text-2xl font-bold">{health.uptime_check_ms?.toFixed(0) ?? '—'}ms</div>
          <div className="text-xs text-muted-foreground mt-1">健康检查</div>
        </div>
      </div>

      <div className="rounded-lg border border-border bg-card p-4">
        <h3 className="text-sm font-medium mb-3">器官状态</h3>
        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-2">
          {Object.entries(components).map(([name, comp]) => (
            <div key={name} className="flex items-center gap-2 p-2 rounded bg-muted/30">
              <div className={`w-2 h-2 rounded-full ${comp.status === 'ok' ? 'bg-green-500' : 'bg-red-500'}`} />
              <span className="text-xs truncate">{name}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
