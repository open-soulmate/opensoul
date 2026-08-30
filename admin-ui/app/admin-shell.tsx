'use client';

import { useEffect, useState, type ReactNode } from 'react';
import { useRouter, usePathname } from 'next/navigation';
import { AppShell, LeftPanel, WorkspacePanel, useAppStore, cn } from '@opensoulmate/openface';
import { LayoutDashboard, Users, BookOpen, Shield, Settings, Server, Bot, MessageSquare, Puzzle, Zap, Timer, Plug, Bell, Activity, Cpu, Mic, ScrollText, Network, Workflow, Store, ListChecks } from 'lucide-react';
import { apiFetch } from '@/lib/api';
import { useAuthStore } from '@/lib/store';
import { LoginPage } from '@/app/login-page';

// ── Nav ─────────────────────────────────────────────────────────

const NAV = [
  { path: '/', label: '总览', icon: <LayoutDashboard className="w-full h-full" /> },
  { path: '/users', label: '用户', icon: <Users className="w-full h-full" /> },
  { path: '/knowledge', label: '知识库', icon: <BookOpen className="w-full h-full" /> },
  { path: '/knowledge-graph', label: '知识图谱', icon: <Network className="w-full h-full" /> },
  { path: '/agents', label: 'Agent', icon: <Bot className="w-full h-full" /> },
  { path: '/plugins', label: '插件', icon: <Puzzle className="w-full h-full" /> },
  { path: '/marketplace', label: '市场', icon: <Store className="w-full h-full" /> },
  { path: '/skills', label: '技能', icon: <Zap className="w-full h-full" /> },
  { path: '/cron', label: '定时任务', icon: <Timer className="w-full h-full" /> },
  { path: '/mcp', label: 'MCP', icon: <Plug className="w-full h-full" /> },
  { path: '/pipeline', label: '流水线', icon: <Workflow className="w-full h-full" /> },
  { path: '/workflow', label: '工作流', icon: <ListChecks className="w-full h-full" /> },
  { path: '/notifications', label: '通知', icon: <Bell className="w-full h-full" /> },
  { path: '/monitoring', label: '监控', icon: <Activity className="w-full h-full" /> },
  { path: '/llm', label: 'LLM', icon: <Cpu className="w-full h-full" /> },
  { path: '/voice', label: '语音', icon: <Mic className="w-full h-full" /> },
  { path: '/logs', label: '日志', icon: <ScrollText className="w-full h-full" /> },
  { path: '/sessions', label: '会话', icon: <MessageSquare className="w-full h-full" /> },
  { path: '/permissions', label: '权限', icon: <Shield className="w-full h-full" /> },
  { path: '/system', label: '系统', icon: <Settings className="w-full h-full" /> },
];

// ── Sidebar Nav List ────────────────────────────────────────────

function SidebarNavList({ activePath, onNavigate }: { activePath: string; onNavigate: (path: string) => void }) {
  return (
    <div className="flex flex-col gap-1 p-3 pt-4">
      {NAV.map((item) => {
        const active = item.path === '/' ? activePath === '/' : activePath.startsWith(item.path);
        return (
          <button
            key={item.path}
            onClick={() => onNavigate(item.path)}
            className={cn(
              'flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium transition-colors',
              active ? 'bg-primary/10 text-primary' : 'text-muted-foreground hover:text-foreground hover:bg-muted/50'
            )}
          >
            <span className="w-4 h-4 shrink-0">{item.icon}</span>
            <span>{item.label}</span>
          </button>
        );
      })}
    </div>
  );
}

// ── Admin Shell ─────────────────────────────────────────────────

export function AdminShell({ children }: { children: ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const logout = useAuthStore((s) => s.logout);
  const hydrate = useAuthStore((s) => s.hydrate);
  const setRightPanelOpen = useAppStore((s) => s.setRightPanelOpen);
  const [ready, setReady] = useState(false);

  useEffect(() => { hydrate(); setReady(true); }, [hydrate]);
  useEffect(() => { setRightPanelOpen(false); }, [pathname, setRightPanelOpen]);

  if (!ready) return <div className="flex items-center justify-center min-h-screen bg-background"><div className="text-sm text-muted-foreground">加载中...</div></div>;
  if (!isAuthenticated) return <LoginPage />;

  return (
    <AppShell
      appName="OpenSoul"
      logo={<Server className="w-full h-full" />}
      nav={NAV}
      activePath={pathname}
      onNavigate={(path) => router.push(path)}
      onSettings={() => setRightPanelOpen(true)}
      userName="admin"
      onLogout={() => { logout(); router.push('/'); }}
      leftPanel={
        <LeftPanel
          renderContent={() => (
            <SidebarNavList activePath={pathname} onNavigate={(p) => router.push(p)} />
          )}
          placeholder="搜索..."
        />
      }
      rightPanel={
        <WorkspacePanel
          sessionId="admin-main"
          workspaceTitle="OpenSoul Workspace"
        />
      }
      bottomRight={<span className="text-[10px] text-muted-foreground px-2">v0.1.0</span>}
      mainPanelProps={{
        title: NAV.find((n) => n.path === '/' ? pathname === '/' : pathname.startsWith(n.path))?.label || '总览',
        icon: NAV.find((n) => n.path === '/' ? pathname === '/' : pathname.startsWith(n.path))?.icon,
      }}
    >
      <div className="flex-1 overflow-auto p-4 lg:p-6">
        {children}
      </div>
    </AppShell>
  );
}
