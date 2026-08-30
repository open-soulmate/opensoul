'use client';

import { create } from 'zustand';

interface AuthState {
  token: string | null;
  isAuthenticated: boolean;
  setToken: (token: string) => void;
  logout: () => void;
  hydrate: () => void;
}

export const useAuthStore = create<AuthState>((set) => ({
  token: null,
  isAuthenticated: false,
  setToken: (token) => {
    if (typeof window !== 'undefined') localStorage.setItem('soul_token', token);
    set({ token, isAuthenticated: true });
  },
  logout: () => {
    if (typeof window !== 'undefined') localStorage.removeItem('soul_token');
    set({ token: null, isAuthenticated: false });
  },
  hydrate: () => {
    if (typeof window === 'undefined') return;
    const token = localStorage.getItem('soul_token');
    if (token) set({ token, isAuthenticated: true });
  },
}));
