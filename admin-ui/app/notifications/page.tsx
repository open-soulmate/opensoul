'use client';

import { useEffect, useState, useCallback } from 'react';
import { apiFetch } from '@/lib/api';
import {
  Search, Bell, Trash2, RefreshCw, X, CheckCheck, AlertCircle,
  Info, AlertTriangle, CheckCircle, Filter, Beaker
} from 'lucide-react';

interface Notification {
  id: string;
  source: string;
  title: string;
  body: string;
  level: string;
  organ: string;
  emoji: string;
  action_url: string;
  metadata: Record<string, any>;
  timestamp: number;
  read: boolean;
}

const LEVEL_CONFIG: Record<string, { icon: any; color: string; bg: string }> = {
  info: { icon: Info, color: 'text-blue-500', bg: 'bg-blue-500/10' },
  warning: { icon: AlertTriangle, color: 'text-orange-500', bg: 'bg-orange-500/10' },
  error: { icon: AlertCircle, color: 'text-red-500', bg: 'bg-red-500/10' },
  success: { icon: CheckCircle, color: 'text-green-500', bg: 'bg-green-500/10' },
};

export default function NotificationsPage() {
  const [notifications, setNotifications] = useState<Notification[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [search, setSearch] = useState('');
  const [levelFilter, setLevelFilter] = useState('');
  const [unreadOnly, setUnreadOnly] = useState(false);
  const [unreadCount, setUnreadCount] = useState(0);

  const fetchNotifications = useCallback(async () => {
    try {
      setLoading(true);
      const params = new URLSearchParams({ limit: '100' });
      if (unreadOnly) params.set('unread_only', 'true');
      if (levelFilter) params.set('level', levelFilter);
      const data = await apiFetch(`/api/notifications/recent?${params}`);
      setNotifications(Array.isArray(data.notifications) ? data.notifications : []);
      setUnreadCount(data.unread_count || 0);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [unreadOnly, levelFilter]);

  useEffect(() => { fetchNotifications(); }, [fetchNotifications]);

  const handleMarkRead = async (notifId: string) => {
    try {
      await apiFetch(`/api/notifications/${notifId}/read`, { method: 'POST' });
      setNotifications((prev) => prev.map((n) => n.id === notifId ? { ...n, read: true } : n));
      setUnreadCount((c) => Math.max(0, c - 1));
    } catch (e: any) { setError(e.message); }
  };

  const handleMarkAllRead = async () => {
    try {
      await apiFetch('/api/notifications/read-all', { method: 'POST' });
      setNotifications((prev) => prev.map((n) => ({ ...n, read: true })));
      setUnreadCount(0);
    } catch (e: any) { setError(e.message); }
  };

  const handleDismiss = async (notifId: string) => {
    try {
      await apiFetch(`/api/notifications/${notifId}`, { method: 'DELETE' });
      setNotifications((prev) => prev.filter((n) => n.id !== notifId));
    } catch (e: any) { setError(e.message); }
  };

  const handleClearAll = async () => {
    if (!confirm('确定要清空所有通知？')) return;
    try {
      await apiFetch('/api/notifications/', { method: 'DELETE' });
      setNotifications([]);
      setUnreadCount(0);
    } catch (e: any) { setError(e.message); }
  };

  const handleTest = async () => {
    try {
      await apiFetch('/api/notifications/test', { method: 'POST' });
      fetchNotifications();
    } catch (e: any) { setError(e.message); }
  };

  const filtered = notifications.filter((n) => {
    if (!search) return true;
    const q = search.toLowerCase();
    return n.title.toLowerCase().includes(q) || n.body.toLowerCase().includes(q) || n.source.toLowerCase().includes(q);
  });

  const formatTime = (ts: number) => {
    if (!ts) return '';
    const d = new Date(ts * 1000);
    return d.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' });
  };

  if (loading) return <div className="text-sm text-muted-foreground p-4">加载中...</div>;

  return (
    <div className="space-y-4">
      {/* Stats */}
      <div className="grid grid-cols-4 gap-3">
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold">{notifications.length}</div>
          <div className="text-xs text-muted-foreground">总通知数</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold text-orange-500">{unreadCount}</div>
          <div className="text-xs text-muted-foreground">未读</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold text-red-500">{notifications.filter((n) => n.level === 'error').length}</div>
          <div className="text-xs text-muted-foreground">错误</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold text-orange-500">{notifications.filter((n) => n.level === 'warning').length}</div>
          <div className="text-xs text-muted-foreground">警告</div>
        </div>
      </div>

      {/* Toolbar */}
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative flex-1 min-w-[200px]">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-muted-foreground" />
          <input
            className="w-full pl-8 pr-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50"
            placeholder="搜索通知..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        <select
          className="px-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none"
          value={levelFilter}
          onChange={(e) => setLevelFilter(e.target.value)}
        >
          <option value="">全部级别</option>
          <option value="info">信息</option>
          <option value="warning">警告</option>
          <option value="error">错误</option>
          <option value="success">成功</option>
        </select>
        <label className="flex items-center gap-1.5 text-xs cursor-pointer">
          <input type="checkbox" checked={unreadOnly} onChange={(e) => setUnreadOnly(e.target.checked)} className="rounded" />
          仅未读
        </label>
        <button onClick={() => fetchNotifications()} className="flex items-center gap-1.5 px-3 py-1.5 text-xs bg-muted border border-border rounded-md hover:bg-muted/80">
          <RefreshCw className="w-3.5 h-3.5" />刷新
        </button>
        <button onClick={handleMarkAllRead} className="flex items-center gap-1.5 px-3 py-1.5 text-xs bg-muted border border-border rounded-md hover:bg-muted/80">
          <CheckCheck className="w-3.5 h-3.5" />全部已读
        </button>
        <button onClick={handleTest} className="flex items-center gap-1.5 px-3 py-1.5 text-xs bg-muted border border-border rounded-md hover:bg-muted/80">
          <Beaker className="w-3.5 h-3.5" />测试
        </button>
        <button onClick={handleClearAll} className="flex items-center gap-1.5 px-3 py-1.5 text-xs bg-red-500/10 text-red-500 border border-red-500/20 rounded-md hover:bg-red-500/20">
          <Trash2 className="w-3.5 h-3.5" />清空
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

      {/* Notifications list */}
      <div className="space-y-1">
        {filtered.map((notif) => {
          const config = LEVEL_CONFIG[notif.level] || LEVEL_CONFIG.info;
          const LevelIcon = config.icon;
          return (
            <div
              key={notif.id}
              className={`rounded-lg border p-3 transition-colors cursor-pointer ${notif.read ? 'border-border bg-card hover:bg-muted/30' : 'border-primary/20 bg-primary/5 hover:bg-primary/10'}`}
              onClick={() => !notif.read && handleMarkRead(notif.id)}
            >
              <div className="flex items-start gap-3">
                <div className={`w-8 h-8 rounded-lg flex items-center justify-center shrink-0 ${config.bg}`}>
                  <LevelIcon className={`w-4 h-4 ${config.color}`} />
                </div>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <h3 className="text-xs font-medium truncate">{notif.emoji} {notif.title}</h3>
                    {!notif.read && <span className="w-1.5 h-1.5 rounded-full bg-primary shrink-0" />}
                  </div>
                  <p className="text-[11px] text-muted-foreground mt-0.5 line-clamp-2">{notif.body}</p>
                  <div className="flex items-center gap-3 mt-1 text-[10px] text-muted-foreground">
                    <span>{notif.source}</span>
                    {notif.organ && <span>器官: {notif.organ}</span>}
                    <span>{formatTime(notif.timestamp)}</span>
                  </div>
                </div>
                <button
                  onClick={(e) => { e.stopPropagation(); handleDismiss(notif.id); }}
                  className="p-1 rounded hover:bg-muted shrink-0"
                  title="关闭"
                >
                  <X className="w-3 h-3 text-muted-foreground" />
                </button>
              </div>
            </div>
          );
        })}
        {filtered.length === 0 && (
          <div className="flex flex-col items-center justify-center py-12 text-muted-foreground">
            <Bell className="w-10 h-10 mb-2 opacity-30" />
            <p className="text-xs">{search ? '没有匹配的通知' : '暂无通知'}</p>
          </div>
        )}
      </div>
    </div>
  );
}
