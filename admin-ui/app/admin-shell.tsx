'use client';

import { useEffect, useState, type ReactNode } from 'react';
import { useRouter, usePathname } from 'next/navigation';
import { AppShell, LeftPanel, WorkspacePanel, useAppStore, cn } from '@opensoulmate/openface';
import { LayoutDashboard, Users, BookOpen, Shield, Settings, Server, Bot, MessageSquare, Puzzle, Zap, Timer, Plug, Bell, Activity, Cpu, Mic, ScrollText, Network, Workflow, Store, ListChecks, Gauge, CalendarClock, Brain, NetworkIcon, UsersRound, Package, Heart, Sparkles, Database, ShieldCheck, CircleDot, Layers, Lightbulb, Radio, Link2, Bug, Hand, Dna, GraduationCap, Home, GitBranch, Droplets, Eye, Globe, Wrench, Camera, Download, FlaskConical } from 'lucide-react';
import { apiFetch } from '@/lib/api';
import { useAuthStore } from '@/lib/store';
import { LoginPage } from '@/app/login-page';

// ── Nav ─────────────────────────────────────────────────────────

const NAV = [
  { path: '/', label: '总览', icon: <LayoutDashboard className="w-full h-full" /> },
  { path: '/evolution', label: '进化流水线', icon: <GitBranch className="w-full h-full" /> },
  { path: '/traces', label: '执行轨迹', icon: <ScrollText className="w-full h-full" /> },
  { path: '/patterns', label: '模式发现', icon: <Lightbulb className="w-full h-full" /> },
  { path: '/users', label: '用户', icon: <Users className="w-full h-full" /> },
  { path: '/knowledge', label: '知识库', icon: <BookOpen className="w-full h-full" /> },
  { path: '/kb-requests', label: '知识审批', icon: <Shield className="w-full h-full" /> },
  { path: '/knowledge-graph', label: '知识图谱', icon: <Network className="w-full h-full" /> },
  { path: '/brain', label: '知识大脑', icon: <Brain className="w-full h-full" /> },
  { path: '/agents', label: 'Agent', icon: <Bot className="w-full h-full" /> },
  { path: '/collab', label: 'Agent协作', icon: <Users className="w-full h-full" /> },
  { path: '/ai-groups', label: 'AI群组', icon: <UsersRound className="w-full h-full" /> },
  { path: '/plugins', label: '插件', icon: <Puzzle className="w-full h-full" /> },
  { path: '/marketplace', label: '市场', icon: <Store className="w-full h-full" /> },
  { path: '/skills', label: '技能', icon: <Zap className="w-full h-full" /> },
  { path: '/cron', label: '定时任务', icon: <Timer className="w-full h-full" /> },
  { path: '/mcp', label: 'MCP', icon: <Plug className="w-full h-full" /> },
  { path: '/pipeline', label: '流水线', icon: <Workflow className="w-full h-full" /> },
  { path: '/mirror', label: '沙箱镜像', icon: <FlaskConical className="w-full h-full" /> },
  { path: '/workflow', label: '工作流', icon: <ListChecks className="w-full h-full" /> },
  { path: '/will', label: '意志系统', icon: <Workflow className="w-full h-full" /> },
  { path: '/notifications', label: '通知', icon: <Bell className="w-full h-full" /> },
  { path: '/monitoring', label: '监控', icon: <Activity className="w-full h-full" /> },
  { path: '/diagnostics', label: '诊断', icon: <Bug className="w-full h-full" /> },
  { path: '/soma', label: 'Soma感知', icon: <Eye className="w-full h-full" /> },
  { path: '/sense', label: '感官感知', icon: <Eye className="w-full h-full" /> },
  { path: '/limb', label: 'RPA', icon: <Hand className="w-full h-full" /> },
  { path: '/events', label: '事件流', icon: <Radio className="w-full h-full" /> },
  { path: '/healer', label: '自愈', icon: <Heart className="w-full h-full" /> },
  { path: '/admin-actions', label: '运维操作', icon: <Wrench className="w-full h-full" /> },
  { path: '/reflex', label: '条件反射', icon: <Zap className="w-full h-full" /> },
  { path: '/heredity', label: '遗传链', icon: <Dna className="w-full h-full" /> },
  { path: '/gene', label: '基因模板', icon: <Dna className="w-full h-full" /> },
  { path: '/registry', label: '注册中心', icon: <Package className="w-full h-full" /> },
  { path: '/benchmark', label: '基准测试', icon: <Gauge className="w-full h-full" /> },
  { path: '/intelligence', label: '智能分析', icon: <Brain className="w-full h-full" /> },
  { path: '/ai-engine', label: 'AI引擎', icon: <Cpu className="w-full h-full" /> },
  { path: '/learn', label: '学习', icon: <GraduationCap className="w-full h-full" /> },
  { path: '/mind', label: '心智', icon: <Sparkles className="w-full h-full" /> },
  { path: '/hippo', label: '海马体', icon: <Database className="w-full h-full" /> },
  { path: '/immune', label: '免疫', icon: <ShieldCheck className="w-full h-full" /> },
  { path: '/pulse', label: '脉搏', icon: <CircleDot className="w-full h-full" /> },
  { path: '/vital', label: '生命体征', icon: <Heart className="w-full h-full" /> },
  { path: '/marrow', label: '骨髓', icon: <Database className="w-full h-full" /> },
  { path: '/nerve', label: '神经', icon: <Zap className="w-full h-full" /> },
  { path: '/cortex', label: '皮层', icon: <Brain className="w-full h-full" /> },
  { path: '/vein', label: '血管', icon: <Droplets className="w-full h-full" /> },
  { path: '/entity', label: '实体', icon: <Layers className="w-full h-full" /> },
  { path: '/feedback', label: '反馈', icon: <Lightbulb className="w-full h-full" /> },
  { path: '/link', label: '连接器', icon: <Link2 className="w-full h-full" /> },
  { path: '/topology', label: '拓扑', icon: <NetworkIcon className="w-full h-full" /> },
  { path: '/gland', label: '模型路由', icon: <Cpu className="w-full h-full" /> },
  { path: '/vision', label: '视觉成像', icon: <Eye className="w-full h-full" /> },
  { path: '/capture', label: '网页捕获', icon: <Camera className="w-full h-full" /> },
  { path: '/export', label: '数据导出', icon: <Download className="w-full h-full" /> },
  { path: '/llm', label: 'LLM', icon: <Cpu className="w-full h-full" /> },
  { path: '/voice', label: '语音', icon: <Mic className="w-full h-full" /> },
  { path: '/hermes', label: 'Hermes桥接', icon: <Globe className="w-full h-full" /> },
  { path: '/logs', label: '日志', icon: <ScrollText className="w-full h-full" /> },
  { path: '/timeline', label: '时间线', icon: <CalendarClock className="w-full h-full" /> },
  { path: '/sessions', label: '会话', icon: <MessageSquare className="w-full h-full" /> },
  { path: '/nest', label: '租户', icon: <Home className="w-full h-full" /> },
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
