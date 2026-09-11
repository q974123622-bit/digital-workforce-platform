import { AppstoreOutlined, BulbOutlined, LogoutOutlined, MessageOutlined, TeamOutlined, UserOutlined } from '@ant-design/icons';
import { Avatar, Dropdown, Tooltip } from 'antd';
import { NavLink, Outlet } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import BrandMark from './ui/BrandMark';
import ThemeSwitcher from './ui/ThemeSwitcher';

const NAV_ITEMS = [
  { to: '/workplace', icon: <MessageOutlined />, label: '消息' },
  { to: '/directory', icon: <TeamOutlined />, label: '同事' },
  { to: '/plugins', icon: <BulbOutlined />, label: '插件' },
  { to: '/me', icon: <UserOutlined />, label: '我的' },
];

export default function PortalLayout() {
  const { account, logout } = useAuth();
  if (!account) return null;
  const canAdmin = account.roles.some((role) => ['agent_admin', 'security_admin', 'platform_admin'].includes(role));

  return (
    <div className="portal-shell flex min-h-[100dvh] overflow-hidden bg-page">
      {/* 左侧 64px 垂直导航栏（移动端隐藏） */}
      <aside className="portal-rail hidden w-[72px] shrink-0 flex-col items-center border-r border-line bg-surface py-4 md:flex">
        <NavLink to="/workplace" className="brand-home mb-6 flex flex-col items-center gap-1.5" title="数字员工工作台">
          <BrandMark size={40} />
          <span className="text-[10px] font-semibold tracking-wide text-ink-secondary">数字员工</span>
        </NavLink>
        <nav className="flex flex-1 flex-col items-center gap-1">
          {NAV_ITEMS.map(({ to, icon, label }) => (
            <NavLink
              key={to}
              to={to}
              title={label}
              aria-label={label}
              className={({ isActive }) =>
                `relative flex h-[52px] w-[58px] flex-col items-center justify-center gap-0.5 rounded-control text-lg transition-colors ${
                  isActive
                    ? 'bg-brand-soft text-brand'
                    : 'text-ink-secondary hover:bg-surface-subtle hover:text-brand'
                }`
              }
            >
              {({ isActive }) => (
                <>
                  {isActive && (
                    <span className="absolute -left-3 top-1/2 h-6 w-[3px] -translate-y-1/2 rounded-r bg-brand" />
                  )}
                  {icon}
                  <span className="text-[10px] font-medium leading-none">{label}</span>
                </>
              )}
            </NavLink>
          ))}
        </nav>
        <div className="flex flex-col items-center gap-1">
          <ThemeSwitcher />
          {canAdmin && (
            <Tooltip title="管理后台" placement="right">
              <a
                href="/admin/"
                className="flex h-[52px] w-[58px] flex-col items-center justify-center gap-0.5 rounded-control text-lg text-ink-secondary transition-colors hover:bg-surface-subtle hover:text-brand"
              >
                <AppstoreOutlined />
                <span className="text-[10px] font-medium leading-none">管理</span>
              </a>
            </Tooltip>
          )}
          <Dropdown
            placement="bottomRight"
            menu={{
              items: [
                { key: 'logout', icon: <LogoutOutlined />, label: '退出登录', onClick: () => void logout() },
              ],
            }}
          >
            <button
              type="button"
              className="grid h-11 w-11 cursor-pointer place-items-center rounded-control border-0 bg-transparent"
              aria-label="账号菜单"
            >
              <Avatar size={32} style={{ background: 'var(--dwp-brand)' }}>
                {account.name.slice(0, 1)}
              </Avatar>
            </button>
          </Dropdown>
        </div>
      </aside>

      {/* 主内容区（移动端底部留出导航高度） */}
      <main id="main-content" className="min-w-0 flex-1 pb-14 md:pb-0">
        <Outlet />
      </main>

      {/* 移动端底部导航（触控目标 ≥44px + safe-area） */}
      <nav className="fixed inset-x-0 bottom-0 z-50 flex items-stretch justify-around border-t border-line bg-surface pb-[env(safe-area-inset-bottom)] md:hidden">
        {NAV_ITEMS.map(({ to, icon, label }) => (
          <NavLink
            key={to}
            to={to}
            aria-label={label}
            className={({ isActive }) =>
              `flex min-h-[52px] flex-1 flex-col items-center justify-center gap-0.5 text-[11px] transition-colors ${
                isActive ? 'text-brand' : 'text-ink-secondary'
              }`
            }
          >
            {({ isActive }) => (
              <>
                <span className="grid h-6 w-10 place-items-center rounded-control text-lg" style={isActive ? { background: 'var(--dwp-brand-soft)' } : undefined}>
                  {icon}
                </span>
                {label}
              </>
            )}
          </NavLink>
        ))}
      </nav>
    </div>
  );
}
