'use client';

import { useEffect } from 'react';
import { useAuthStore } from '@/lib/store';

export default function AuthGate({ children }: { children: React.ReactNode }) {
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const hydrate = useAuthStore((s) => s.hydrate);

  useEffect(() => { hydrate(); }, [hydrate]);

  if (!isAuthenticated) return null; // layout.tsx handles login
  return <>{children}</>;
}
