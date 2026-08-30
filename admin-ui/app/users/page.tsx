'use client';

import { useEffect, useState } from 'react';
import { apiFetch } from '@/lib/api';
import { UserPlus, Search } from 'lucide-react';

interface User {
  id: number;
  username: string;
  role: string;
  created_at: string;
}

export default function UsersPage() {
  const [users, setUsers] = useState<User[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    apiFetch('/api/enterprise/users')
      .then((d) => setUsers(Array.isArray(d) ? d : d.users || []))
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <div className="text-sm text-muted-foreground p-4">加载中...</div>;
  if (error) return <div className="text-sm text-red-500 p-4">错误: {error}</div>;

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <div className="relative">
            <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-muted-foreground" />
            <input className="pl-8 pr-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50" placeholder="搜索用户..." />
          </div>
        </div>
        <button className="flex items-center gap-1.5 px-3 py-1.5 text-xs bg-primary text-primary-foreground rounded-md hover:opacity-90">
          <UserPlus className="w-3.5 h-3.5" />添加用户
        </button>
      </div>
      <div className="rounded-lg border border-border bg-card overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-muted/50">
            <tr>
              <th className="text-left px-4 py-2.5 text-xs font-medium text-muted-foreground">ID</th>
              <th className="text-left px-4 py-2.5 text-xs font-medium text-muted-foreground">用户名</th>
              <th className="text-left px-4 py-2.5 text-xs font-medium text-muted-foreground">角色</th>
              <th className="text-left px-4 py-2.5 text-xs font-medium text-muted-foreground">创建时间</th>
            </tr>
          </thead>
          <tbody>
            {users.map((u) => (
              <tr key={u.id} className="border-t border-border hover:bg-muted/30">
                <td className="px-4 py-2.5 text-xs">{u.id}</td>
                <td className="px-4 py-2.5 text-xs font-medium">{u.username}</td>
                <td className="px-4 py-2.5 text-xs"><span className="px-2 py-0.5 rounded-full bg-primary/10 text-primary text-[10px]">{u.role}</span></td>
                <td className="px-4 py-2.5 text-xs text-muted-foreground">{u.created_at}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
