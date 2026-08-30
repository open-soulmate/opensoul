'use client';

const API_BASE = '';

export async function login(username: string, password: string): Promise<string> {
  const res = await fetch(`${API_BASE}/api/enterprise/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  });
  if (!res.ok) throw new Error('Login failed');
  const data = await res.json();
  const token = data.token || data.access_token;
  if (typeof window !== 'undefined') localStorage.setItem('soul_token', token);
  return token;
}

export function getToken(): string | null {
  if (typeof window === 'undefined') return null;
  return localStorage.getItem('soul_token');
}

export function clearToken() {
  if (typeof window !== 'undefined') localStorage.removeItem('soul_token');
}

export async function apiFetch(path: string, opts: RequestInit = {}) {
  const token = getToken();
  const headers: Record<string, string> = { ...(opts.headers as Record<string, string>) };
  if (token) headers['Authorization'] = `Bearer ${token}`;
  const res = await fetch(`${API_BASE}${path}`, { ...opts, headers });
  if (res.status === 401) { clearToken(); throw new Error('Unauthorized'); }
  if (!res.ok) throw new Error(`API error ${res.status}`);
  return res.json();
}
