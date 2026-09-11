import { ConfigProvider } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import { createContext, useCallback, useContext, useEffect, useLayoutEffect, useMemo, useState } from 'react';
import type { ReactNode } from 'react';
import { createThemeConfig, DEFAULT_THEME_ID, getTheme, isThemeId, themes, THEME_STORAGE_KEY } from '../theme';
import type { ThemeDefinition, ThemeId } from '../theme';

export interface ThemeContextValue {
  themeId: ThemeId;
  themes: ThemeDefinition[];
  setTheme: (themeId: ThemeId) => void;
}

const ThemeContext = createContext<ThemeContextValue | null>(null);

function readStoredTheme(): ThemeId {
  try {
    const stored = localStorage.getItem(THEME_STORAGE_KEY);
    return isThemeId(stored) ? stored : DEFAULT_THEME_ID;
  } catch {
    return DEFAULT_THEME_ID;
  }
}

function applyDocumentTheme(themeId: ThemeId) {
  const selected = getTheme(themeId);
  document.documentElement.dataset.theme = themeId;
  document.documentElement.style.colorScheme = selected.dark ? 'dark' : 'light';
  document.querySelector<HTMLMetaElement>('meta[name="theme-color"]')?.setAttribute('content', selected.colors.pageBg);
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [themeId, setThemeId] = useState<ThemeId>(readStoredTheme);
  useLayoutEffect(() => applyDocumentTheme(themeId), [themeId]);

  useEffect(() => {
    const synchronize = (event: StorageEvent) => {
      if (event.key === THEME_STORAGE_KEY) setThemeId(isThemeId(event.newValue) ? event.newValue : DEFAULT_THEME_ID);
    };
    window.addEventListener('storage', synchronize);
    return () => window.removeEventListener('storage', synchronize);
  }, []);

  const setTheme = useCallback((nextTheme: ThemeId) => {
    const safeTheme = isThemeId(nextTheme) ? nextTheme : DEFAULT_THEME_ID;
    setThemeId(safeTheme);
    try { localStorage.setItem(THEME_STORAGE_KEY, safeTheme); } catch { /* In-memory selection still applies. */ }
  }, []);

  const value = useMemo(() => ({ themeId, themes, setTheme }), [setTheme, themeId]);
  const config = useMemo(() => createThemeConfig(themeId), [themeId]);
  return (
    <ThemeContext.Provider value={value}>
      <ConfigProvider locale={zhCN} theme={config} button={{ autoInsertSpace: false }}>{children}</ConfigProvider>
    </ThemeContext.Provider>
  );
}

export function useTheme() {
  const value = useContext(ThemeContext);
  if (!value) throw new Error('useTheme 必须在 ThemeProvider 内使用');
  return value;
}
