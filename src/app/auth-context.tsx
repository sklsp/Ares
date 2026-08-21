"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
const STORAGE_KEY = "ops-agent-session";

export type SessionUser = { id: number; email: string; role: string; organization_id: number };

type AuthState = {
  user: SessionUser | null;
  token: string | null;
  loading: boolean;
  error: string | null;
  login: (email: string, password: string) => Promise<boolean>;
  logout: () => void;
  apiFetch: <T>(path: string, options?: RequestInit) => Promise<T>;
};

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<SessionUser | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Restore the session on first load so refresh/navigation keeps you logged in.
  // Hydrating persisted auth state is the effect's purpose; the rule's
  // lazy-init alternative cannot read localStorage during SSR render.
  /* eslint-disable react-hooks/set-state-in-effect */
  useEffect(() => {
    try {
      const raw = window.localStorage.getItem(STORAGE_KEY);
      if (raw) {
        const saved = JSON.parse(raw) as { token: string; user: SessionUser };
        setToken(saved.token);
        setUser(saved.user);
      }
    } catch {
      window.localStorage.removeItem(STORAGE_KEY);
    }
    setLoading(false);
  }, []);
  /* eslint-enable react-hooks/set-state-in-effect */

  const login = useCallback(async (email: string, password: string) => {
    setError(null);
    try {
      const response = await fetch(`${API}/auth/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
      });
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        setError(body.detail ?? "Login failed");
        return false;
      }
      const data = (await response.json()) as { access_token: string };
      const me = await fetch(`${API}/auth/me`, {
        headers: { Authorization: `Bearer ${data.access_token}` },
      });
      if (!me.ok) {
        setError("Session could not be established");
        return false;
      }
      const profile = (await me.json()) as SessionUser;
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify({ token: data.access_token, user: profile }));
      setToken(data.access_token);
      setUser(profile);
      return true;
    } catch {
      setError("Could not reach the authentication service");
      return false;
    }
  }, []);

  const logout = useCallback(() => {
    if (token) {
      void fetch(`${API}/auth/logout`, { method: "POST", headers: { Authorization: `Bearer ${token}` } });
    }
    window.localStorage.removeItem(STORAGE_KEY);
    setToken(null);
    setUser(null);
  }, [token]);

  const apiFetch = useCallback(async <T,>(path: string, options?: RequestInit): Promise<T> => {
    const headers = new Headers(options?.headers);
    if (!headers.has("Content-Type") && options?.body) headers.set("Content-Type", "application/json");
    if (token) headers.set("Authorization", `Bearer ${token}`);
    const response = await fetch(`${API}${path}`, { ...options, headers });
    if (response.status === 401) {
      // Expired or revoked session: clear local state so the UI returns to login.
      window.localStorage.removeItem(STORAGE_KEY);
      setToken(null);
      setUser(null);
      throw new Error("Your session has expired. Please sign in again.");
    }
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      throw new Error(body.detail ?? `Request failed (${response.status})`);
    }
    return response.json() as Promise<T>;
  }, [token]);

  const value = useMemo(
    () => ({ user, token, loading, error, login, logout, apiFetch }),
    [user, token, loading, error, login, logout, apiFetch],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth must be used inside AuthProvider");
  return context;
}
