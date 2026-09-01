import type { ThemeConfig } from 'antd';

/** 品牌主色：券商风格的深靛蓝 */
export const BRAND_PRIMARY = '#165dff';
/** 侧栏深海军蓝背景 */
export const SIDER_BG = '#0d1b3e';
/** 页面浅灰蓝背景 */
export const PAGE_BG = '#f2f3f5';

/** 全局设计 token，供 ConfigProvider 统一消费 */
export const themeConfig: ThemeConfig = {
  token: {
    colorPrimary: BRAND_PRIMARY,
    colorInfo: BRAND_PRIMARY,
    colorBgLayout: PAGE_BG,
    colorTextBase: '#1f2733',
    colorTextSecondary: '#5c6b83',
    colorBorderSecondary: '#eef1f6',
    borderRadius: 4,
    fontFamily: [
      '-apple-system',
      'BlinkMacSystemFont',
      '"Segoe UI"',
      'Roboto',
      '"PingFang SC"',
      '"Hiragino Sans GB"',
      '"Microsoft YaHei"',
      '"Helvetica Neue"',
      'Arial',
      'sans-serif',
    ].join(','),
  },
  components: {
    Layout: {
      siderBg: SIDER_BG,
      headerBg: '#ffffff',
      headerHeight: 56,
      headerPadding: '0 24px',
    },
    Menu: {
      darkItemBg: SIDER_BG,
      darkSubMenuItemBg: SIDER_BG,
      darkItemColor: 'rgba(255, 255, 255, 0.68)',
      darkItemHoverBg: 'rgba(255, 255, 255, 0.08)',
      darkItemSelectedBg: BRAND_PRIMARY,
      darkItemSelectedColor: '#ffffff',
      itemBorderRadius: 8,
      itemMarginInline: 12,
      itemHeight: 44,
    },
    Card: {
      borderRadiusLG: 6,
      boxShadowTertiary: 'none',
    },
    Table: {
      headerBg: '#fafbfd',
      headerColor: '#5c6b83',
    },
    Tabs: {
      inkBarColor: BRAND_PRIMARY,
    },
  },
};
