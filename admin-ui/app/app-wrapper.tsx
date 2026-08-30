'use client';

import { useEffect, useState } from 'react';
import { useAuthStore } from '@/lib/store';
import { LoginPage } from '@/app/login-page';
import { AdminShell } from '@/app/admin-shell';

export function AppWrapper({ children }: { children: React.ReactNode }) {
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const hydrate = useAuthStore((s) => s.hydrate);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    hydrate();
    setReady(true);
  }, [hydrate]);

  if (!ready) {
    return <div className="flex items-center justify-center min-h-screen bg-background"><div className="text-sm text-muted-foreground">加载中...</div></div>;
  }

  if (!isAuthenticated) return <LoginPage />;

  return <AdminShell>{children}</AdminShell>;
}
