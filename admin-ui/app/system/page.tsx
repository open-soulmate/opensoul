'use client';

import { useEffect, useState } from 'react';
import { apiFetch } from '@/lib/api';
import { Settings, Database, HardDrive, Cpu } from 'lucide-react';

interface SystemInfo {
  status: string;
  version: string;
  database: string;
  uptime: string;
}

export default function SystemPage() {
  const [info, setInfo] = useState<SystemInfo | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    apiFetch('/api/health')
      .then((d) => setInfo(d as SystemInfo))
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <div className="text-sm text-muted-foreground p-4">加载中...</div>;

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <Settings className="w-4 h-4 text-muted-foreground" />
        <h2 className="text-sm font-medium">系统设置</h2>
      </div>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div className="rounded-lg border border-border bg-card p-4">
          <div className="flex items-center gap-2 text-muted-foreground text-xs mb-3"><Database className="w-3.5 h-3.5" />数据库</div>
          <div className="text-sm">{info?.database || 'SQLite'}</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-4">
          <div className="flex items-center gap-2 text-muted-foreground text-xs mb-3"><HardDrive className="w-3.5 h-3.5" />版本</div>
          <div className="text-sm">{info?.version || '—'}</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-4">
          <div className="flex items-center gap-2 text-muted-foreground text-xs mb-3"><Cpu className="w-3.5 h-3.5" />状态</div>
          <div className="text-sm">{info?.status || '—'}</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-4">
          <div className="flex items-center gap-2 text-muted-foreground text-xs mb-3"><Settings className="w-3.5 h-3.5" />运行时间</div>
          <div className="text-sm">{info?.uptime || '—'}</div>
        </div>
      </div>
    </div>
  );
}
