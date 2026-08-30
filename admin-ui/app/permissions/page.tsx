'use client';

import { Shield } from 'lucide-react';

export default function PermissionsPage() {
  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <Shield className="w-4 h-4 text-muted-foreground" />
        <h2 className="text-sm font-medium">权限管理</h2>
      </div>
      <div className="rounded-lg border border-border bg-card p-8 flex flex-col items-center justify-center text-muted-foreground">
        <Shield className="w-12 h-12 mb-3 opacity-20" />
        <p className="text-sm">权限管理功能开发中</p>
        <p className="text-xs mt-1">即将支持角色管理和细粒度权限控制</p>
      </div>
    </div>
  );
}
