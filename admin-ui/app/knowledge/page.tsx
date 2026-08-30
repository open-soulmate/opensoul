'use client';

import { useEffect, useState } from 'react';
import { apiFetch } from '@/lib/api';
import { Search, BookOpen, Plus } from 'lucide-react';

interface KnowledgeEntry {
  id: number;
  title: string;
  content: string;
  content_type: string;
  source: string;
  created_at: string;
}

export default function KnowledgePage() {
  const [entries, setEntries] = useState<KnowledgeEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    apiFetch('/api/knowledge/?limit=50')
      .then((d) => setEntries(Array.isArray(d) ? d : d.entries || []))
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <div className="text-sm text-muted-foreground p-4">加载中...</div>;
  if (error) return <div className="text-sm text-red-500 p-4">错误: {error}</div>;

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div className="relative">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-muted-foreground" />
          <input className="pl-8 pr-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50" placeholder="搜索知识库..." />
        </div>
        <button className="flex items-center gap-1.5 px-3 py-1.5 text-xs bg-primary text-primary-foreground rounded-md hover:opacity-90">
          <Plus className="w-3.5 h-3.5" />添加条目
        </button>
      </div>
      <div className="space-y-2">
        {entries.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-12 text-muted-foreground">
            <BookOpen className="w-10 h-10 mb-2 opacity-30" />
            <p className="text-xs">暂无知识条目</p>
          </div>
        ) : entries.map((entry) => (
          <div key={entry.id} className="rounded-lg border border-border bg-card p-4 hover:bg-muted/30 transition-colors">
            <div className="flex items-start justify-between">
              <div className="min-w-0 flex-1">
                <h3 className="text-sm font-medium truncate">{entry.title}</h3>
                <p className="text-xs text-muted-foreground mt-1 line-clamp-2">{entry.content}</p>
              </div>
              <span className="text-[10px] px-2 py-0.5 rounded bg-muted text-muted-foreground shrink-0 ml-2">{entry.content_type}</span>
            </div>
            <div className="flex items-center gap-3 mt-2 text-[10px] text-muted-foreground">
              <span>{entry.source}</span>
              <span>{entry.created_at}</span>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
