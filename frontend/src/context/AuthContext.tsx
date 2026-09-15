import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import type { ReactNode } from 'react';
import type { Account } from '@dwp/shared-schema';
import { api, AUTH_EXPIRED_EVENT } from '../api/client';

interface AuthContextValue {
  account: Account | null;
  checking: boolean;
  login: (accessKey: string) => Promise<Account>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [account, setAccount] = useState<Account | null>(null);
  const [checking, setChecking] = useState(true);

  useEffect(() => {
    let active = true;
    api.me()
      .then((value) => active && setAccount(value))
      .catch(() => active && setAccount(null))
      .finally(() => active && setChecking(false));
    return () => { active = false; };
  }, []);

  useEffect(() => {
    const expire = () => setAccount(null);
    window.addEventListener(AUTH_EXPIRED_EVENT, expire);
    return () => window.removeEventListener(AUTH_EXPIRED_EVENT, expire);
  }, []);

  const login = useCallback(async (accessKey: string) => {
    const reply = await api.login(accessKey);
    setAccount(reply.account);
    return reply.account;
  }, []);

  const logout = useCallback(async () => {
    try { await api.logout(); }
    finally { setAccount(null); }
  }, []);

  const value = useMemo(() => ({ account, checking, login, logout }), [account, checking, login, logout]);
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const value = useContext(AuthContext);
  if (!value) throw new Error('useAuth 必须在 AuthProvider 内使用');
  return value;
}
