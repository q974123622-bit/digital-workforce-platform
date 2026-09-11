import { theme as antdTheme } from 'antd';
import type { ThemeConfig } from 'antd';

export type ThemeId = 'warm-paper' | 'clear-blue' | 'sage-mist' | 'midnight';

export interface ThemeDefinition {
  id: ThemeId;
  name: string;
  description: string;
  dark: boolean;
  preview: [string, string, string];
  colors: {
    pageBg: string; surface: string; surfaceSubtle: string; surfaceElevated: string;
    brand: string; brandHover: string; brandSoft: string;
    text: string; textSecondary: string; textTertiary: string;
    border: string; borderLight: string; success: string; warning: string; danger: string;
  };
}

export const DEFAULT_THEME_ID: ThemeId = 'warm-paper';
export const THEME_STORAGE_KEY = 'dwp-ui-theme-v1';

export const themes: ThemeDefinition[] = [
  {
    id: 'warm-paper', name: '暖纸', description: '温和、克制的编辑感', dark: false,
    preview: ['#F7F6F1', '#FCFBF8', '#174F7C'],
    colors: {
      pageBg: '#F7F6F1', surface: '#FCFBF8', surfaceSubtle: '#F0EEE8', surfaceElevated: '#FFFFFF',
      brand: '#174F7C', brandHover: '#123F65', brandSoft: '#EAF0F3',
      text: '#2D2B27', textSecondary: '#6F6B63', textTertiary: '#969087',
      border: '#DEDBD3', borderLight: '#EBE8E1', success: '#067647', warning: '#B54708', danger: '#B42318',
    },
  },
  {
    id: 'clear-blue', name: '清透', description: '清爽、明晰的蓝白界面', dark: false,
    preview: ['#F4F7FB', '#FFFFFF', '#155A8A'],
    colors: {
      pageBg: '#F4F7FB', surface: '#FFFFFF', surfaceSubtle: '#EDF3F8', surfaceElevated: '#FFFFFF',
      brand: '#155A8A', brandHover: '#10486F', brandSoft: '#E7F0F7',
      text: '#1F2933', textSecondary: '#637083', textTertiary: '#8A96A5',
      border: '#D8E1E8', borderLight: '#E7EDF2', success: '#087A55', warning: '#A95110', danger: '#B42318',
    },
  },
  {
    id: 'sage-mist', name: '雾青', description: '安静、自然的青灰色调', dark: false,
    preview: ['#F3F6F2', '#FAFCF9', '#2F665D'],
    colors: {
      pageBg: '#F3F6F2', surface: '#FAFCF9', surfaceSubtle: '#E8EFEA', surfaceElevated: '#FFFFFF',
      brand: '#2F665D', brandHover: '#245149', brandSoft: '#DDEBE6',
      text: '#24312D', textSecondary: '#63716C', textTertiary: '#8A9691',
      border: '#D6E0DA', borderLight: '#E5ECE7', success: '#147653', warning: '#A95816', danger: '#B4382E',
    },
  },
  {
    id: 'midnight', name: '深海', description: '低眩光的夜间工作模式', dark: true,
    preview: ['#171B20', '#1E242A', '#83B8DD'],
    colors: {
      pageBg: '#171B20', surface: '#1E242A', surfaceSubtle: '#262D34', surfaceElevated: '#2B333B',
      brand: '#83B8DD', brandHover: '#A3CAE5', brandSoft: '#253B4A',
      text: '#F1F0EB', textSecondary: '#B5BAC0', textTertiary: '#858C93',
      border: '#394149', borderLight: '#303840', success: '#55B88A', warning: '#E0A05B', danger: '#E27676',
    },
  },
];

export function isThemeId(value: string | null | undefined): value is ThemeId {
  return themes.some((item) => item.id === value);
}

export function getTheme(themeId: ThemeId): ThemeDefinition {
  return themes.find((item) => item.id === themeId) ?? themes[0];
}

export function createThemeConfig(themeId: ThemeId): ThemeConfig {
  const selected = getTheme(themeId);
  const token = selected.colors;
  return {
    algorithm: selected.dark ? antdTheme.darkAlgorithm : antdTheme.defaultAlgorithm,
    token: {
      colorPrimary: token.brand, colorInfo: token.brand, colorLink: token.brand,
      colorBgBase: token.surface, colorBgLayout: token.pageBg, colorBgContainer: token.surface,
      colorBgElevated: token.surfaceElevated, colorTextBase: token.text,
      colorTextSecondary: token.textSecondary, colorBorder: token.border,
      colorBorderSecondary: token.borderLight, colorSuccess: token.success,
      colorWarning: token.warning, colorError: token.danger,
      borderRadius: 8, borderRadiusLG: 11, fontSize: 14,
      fontFamily: '"DWP Noto Sans SC","PingFang SC","Microsoft YaHei UI",sans-serif',
    },
    components: {
      Layout: { siderBg: token.surfaceSubtle, headerBg: token.surface, headerHeight: 56, headerPadding: '0 24px' },
      Menu: {
        itemBg: token.surfaceSubtle, itemColor: token.textSecondary,
        itemHoverBg: token.brandSoft, itemHoverColor: token.brand,
        itemSelectedBg: token.brandSoft, itemSelectedColor: token.brand,
        itemBorderRadius: 8, itemMarginInline: 12, itemHeight: 44,
      },
      Card: { borderRadiusLG: 11, boxShadowTertiary: 'none' },
      Table: { headerBg: token.surfaceSubtle, headerColor: token.textSecondary },
      Tabs: { inkBarColor: token.brand, itemSelectedColor: token.brand, itemHoverColor: token.brand },
    },
  };
}

// Compatibility exports for older components and tests.
export const tokens = getTheme(DEFAULT_THEME_ID).colors;
export const BRAND_PRIMARY = tokens.brand;
export const SIDER_BG = tokens.surface;
export const PAGE_BG = tokens.pageBg;
export const themeConfig = createThemeConfig(DEFAULT_THEME_ID);
