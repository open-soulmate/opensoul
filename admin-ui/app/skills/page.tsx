'use client';

import { useEffect, useState, useCallback } from 'react';
import { apiFetch } from '@/lib/api';
import {
  Search, Zap, Download, Trash2, RefreshCw, X, FolderInput,
  CheckCircle, AlertCircle, Package, BookOpen, Loader2
} from 'lucide-react';

interface Skill {
  name: string;
  description: string;
  category: string;
  version: string;
  installed: boolean;
  path: string;
  source: string;
}

export default function SkillsPage() {
  const [skills, setSkills] = useState<Skill[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [search, setSearch] = useState('');
  const [filterSource, setFilterSource] = useState('all');
  const [sharedDir, setSharedDir] = useState('');
  const [installedCount, setInstalledCount] = useState(0);

  // Install dialog
  const [showInstall, setShowInstall] = useState(false);
  const [installName, setInstallName] = useState('');
  const [installing, setInstalling] = useState(false);
  const [installResult, setInstallResult] = useState<{ success: boolean; message: string } | null>(null);

  // Migrating
  const [migrating, setMigrating] = useState(false);
  const [migrateResult, setMigrateResult] = useState<{ count: number; migrated: string[] } | null>(null);

  const fetchSkills = useCallback(async () => {
    try {
      setLoading(true);
      const data = await apiFetch('/api/skills');
      setSkills(Array.isArray(data.skills) ? data.skills : []);
      setSharedDir(data.shared_dir || '');
      setInstalledCount(data.installed_count || 0);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchSkills(); }, [fetchSkills]);

  const handleInstall = async () => {
    if (!installName.trim()) return;
    try {
      setInstalling(true);
      setInstallResult(null);
      const data = await apiFetch(`/api/skills/${encodeURIComponent(installName)}/install`, {
        method: 'POST',
      });
      setInstallResult({ success: data.success, message: data.output || data.error || '' });
      if (data.success) {
        fetchSkills();
      }
    } catch (e: any) {
      setInstallResult({ success: false, message: e.message });
    } finally {
      setInstalling(false);
    }
  };

  const handleUninstall = async (skillName: string) => {
    if (!confirm(`确定要卸载技能「${skillName}」？`)) return;
    try {
      await apiFetch(`/api/skills/${encodeURIComponent(skillName)}`, { method: 'DELETE' });
      fetchSkills();
    } catch (e: any) {
      setError(e.message);
    }
  };

  const handleMigrate = async () => {
    try {
      setMigrating(true);
      setMigrateResult(null);
      const data = await apiFetch('/api/skills/migrate', { method: 'POST' });
      setMigrateResult({ count: data.count || 0, migrated: data.migrated || [] });
      if (data.count > 0) fetchSkills();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setMigrating(false);
    }
  };

  const sources = Array.from(new Set(skills.map((s) => s.source))).sort();

  const filtered = skills.filter((s) => {
    if (filterSource !== 'all' && s.source !== filterSource) return false;
    if (search) {
      const q = search.toLowerCase();
      return s.name.toLowerCase().includes(q) || s.description.toLowerCase().includes(q) || s.category.toLowerCase().includes(q);
    }
    return true;
  });

  if (loading) return <div className="text-sm text-muted-foreground p-4">加载中...</div>;

  return (
    <div className="space-y-4">
      {/* Stats */}
      <div className="grid grid-cols-3 gap-3">
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold">{skills.length}</div>
          <div className="text-xs text-muted-foreground">检测到技能</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold text-green-600">{installedCount}</div>
          <div className="text-xs text-muted-foreground">共享目录中</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold text-orange-500">{skills.length - installedCount}</div>
          <div className="text-xs text-muted-foreground">待迁移</div>
        </div>
      </div>

      {/* Toolbar */}
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative flex-1 min-w-[200px]">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-muted-foreground" />
          <input
            className="w-full pl-8 pr-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50"
            placeholder="搜索技能名称、描述..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        <select
          className="px-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none"
          value={filterSource}
          onChange={(e) => setFilterSource(e.target.value)}
        >
          <option value="all">全部来源</option>
          {sources.map((s) => (
            <option key={s} value={s}>{s}</option>
          ))}
        </select>
        <button onClick={() => fetchSkills()} className="flex items-center gap-1.5 px-3 py-1.5 text-xs bg-muted border border-border rounded-md hover:bg-muted/80">
          <RefreshCw className="w-3.5 h-3.5" />刷新
        </button>
        <button
          onClick={handleMigrate}
          disabled={migrating}
          className="flex items-center gap-1.5 px-3 py-1.5 text-xs bg-orange-500/10 text-orange-600 border border-orange-500/20 rounded-md hover:bg-orange-500/20 disabled:opacity-50"
        >
          {migrating ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <FolderInput className="w-3.5 h-3.5" />}
          全部迁移
        </button>
        <button onClick={() => { setShowInstall(true); setInstallResult(null); }} className="flex items-center gap-1.5 px-3 py-1.5 text-xs bg-primary text-primary-foreground rounded-md hover:opacity-90">
          <Download className="w-3.5 h-3.5" />安装技能
        </button>
      </div>

      {/* Error banner */}
      {error && (
        <div className="flex items-center gap-2 p-2 text-xs text-red-500 bg-red-500/5 rounded-md border border-red-500/20">
          <AlertCircle className="w-3.5 h-3.5 shrink-0" />
          <span className="flex-1">{error}</span>
          <button onClick={() => setError('')}><X className="w-3 h-3" /></button>
        </div>
      )}

      {/* Migrate result */}
      {migrateResult && (
        <div className="flex items-center gap-2 p-2 text-xs bg-green-500/5 rounded-md border border-green-500/20">
          <CheckCircle className="w-3.5 h-3.5 text-green-600 shrink-0" />
          <span>迁移完成：{migrateResult.count > 0 ? `成功迁移 ${migrateResult.count} 个技能 (${migrateResult.migrated.join(', ')})` : '所有技能已在共享目录中'}</span>
          <button onClick={() => setMigrateResult(null)}><X className="w-3 h-3" /></button>
        </div>
      )}

      {/* Skills grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
        {filtered.map((skill) => (
          <div key={`${skill.source}-${skill.name}`} className="rounded-lg border border-border bg-card p-4 hover:bg-muted/30 transition-colors">
            <div className="flex items-start justify-between">
              <div className="flex items-start gap-2 min-w-0 flex-1">
                <div className={`w-8 h-8 rounded-lg flex items-center justify-center shrink-0 ${skill.installed ? 'bg-green-500/10' : 'bg-muted'}`}>
                  <Zap className={`w-4 h-4 ${skill.installed ? 'text-green-600' : 'text-muted-foreground'}`} />
                </div>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-1.5">
                    <h3 className="text-sm font-medium truncate">{skill.name}</h3>
                    {skill.installed && <CheckCircle className="w-3 h-3 text-green-600 shrink-0" />}
                  </div>
                  <p className="text-xs text-muted-foreground mt-0.5 line-clamp-2">{skill.description || '暂无描述'}</p>
                  <div className="flex items-center gap-2 mt-2">
                    <span className="text-[10px] px-1.5 py-0.5 rounded bg-primary/10 text-primary">{skill.source}</span>
                    {skill.category && <span className="text-[10px] px-1.5 py-0.5 rounded bg-muted text-muted-foreground">{skill.category}</span>}
                    {skill.version && <span className="text-[10px] text-muted-foreground">v{skill.version}</span>}
                  </div>
                </div>
              </div>
            </div>
            {/* Actions */}
            <div className="flex items-center gap-1.5 mt-3 pt-3 border-t border-border">
              {skill.installed ? (
                <button
                  onClick={() => handleUninstall(skill.name)}
                  className="flex items-center gap-1 px-2 py-1 text-[10px] bg-red-500/10 text-red-500 rounded hover:bg-red-500/20"
                >
                  <Trash2 className="w-3 h-3" />卸载
                </button>
              ) : (
                <span className="text-[10px] text-muted-foreground">来源: {skill.source}</span>
              )}
            </div>
          </div>
        ))}
      </div>

      {filtered.length === 0 && (
        <div className="flex flex-col items-center justify-center py-12 text-muted-foreground">
          <BookOpen className="w-10 h-10 mb-2 opacity-30" />
          <p className="text-xs">{search ? '没有匹配的技能' : '暂未检测到技能'}</p>
        </div>
      )}

      {/* Shared dir info */}
      {sharedDir && (
        <div className="text-[10px] text-muted-foreground text-center">
          共享目录: {sharedDir}
        </div>
      )}

      {/* Install Dialog */}
      {showInstall && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={() => setShowInstall(false)}>
          <div className="bg-card rounded-lg border border-border w-full max-w-md mx-4" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between p-4 border-b border-border">
              <h2 className="text-sm font-medium">安装技能</h2>
              <button onClick={() => setShowInstall(false)} className="p-1 rounded hover:bg-muted"><X className="w-4 h-4" /></button>
            </div>
            <div className="p-4 space-y-3">
              <div>
                <label className="text-xs text-muted-foreground">技能名称 / GitHub仓库</label>
                <input
                  className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md"
                  value={installName}
                  onChange={(e) => setInstallName(e.target.value)}
                  placeholder="skill-name 或 user/repo"
                  onKeyDown={(e) => e.key === 'Enter' && handleInstall()}
                />
              </div>
              <p className="text-[10px] text-muted-foreground">输入技能名称通过 hermes CLI 安装，或输入 GitHub 仓库路径（user/repo）直接克隆。</p>
              {installResult && (
                <div className={`p-2 rounded text-xs ${installResult.success ? 'bg-green-500/5 text-green-600 border border-green-500/20' : 'bg-red-500/5 text-red-500 border border-red-500/20'}`}>
                  {installResult.message}
                </div>
              )}
              <div className="flex justify-end gap-2 pt-2">
                <button onClick={() => setShowInstall(false)} className="px-3 py-1.5 text-xs border border-border rounded-md hover:bg-muted">取消</button>
                <button
                  onClick={handleInstall}
                  disabled={!installName.trim() || installing}
                  className="flex items-center gap-1 px-3 py-1.5 text-xs bg-primary text-primary-foreground rounded-md hover:opacity-90 disabled:opacity-50"
                >
                  {installing ? <Loader2 className="w-3 h-3 animate-spin" /> : <Download className="w-3 h-3" />}
                  {installing ? '安装中...' : '安装'}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
