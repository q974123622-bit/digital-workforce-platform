import {
  ApartmentOutlined,
  AuditOutlined,
  DatabaseOutlined,
  LogoutOutlined,
  ReadOutlined,
  RobotOutlined,
  SettingOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
} from '@ant-design/icons';
import { Avatar, Button, Layout, Menu, Typography } from 'antd';
import type { MenuProps } from 'antd';
import { Link, Outlet, useLocation } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import { useState } from 'react';
import BrandMark from './ui/BrandMark';
import ThemeSwitcher from './ui/ThemeSwitcher';

const { Sider, Header, Content } = Layout;

const items: MenuProps['items'] = [
  { key: '/', icon: <ApartmentOutlined />, label: <Link to="/">运行概览</Link> },
  { key: '/agents', icon: <RobotOutlined />, label: <Link to="/agents">数字员工</Link> },
  { key: '/plugins', icon: <DatabaseOutlined />, label: <Link to="/plugins">插件治理</Link> },
  { key: '/knowledge', icon: <ReadOutlined />, label: <Link to="/knowledge">知识资源</Link> },
  { key: '/security', icon: <AuditOutlined />, label: <Link to="/security">权限与审计</Link> },
  { key: '/settings', icon: <SettingOutlined />, label: <Link to="/settings">系统设置</Link> },
];

const SELECT_MATCH = ['/agents', '/employees', '/plugins', '/knowledge', '/security', '/settings'];

export default function AdminLayout() {
  const location = useLocation();
  const { account, logout } = useAuth();
  const [collapsed, setCollapsed] = useState(false);
  const selected = SELECT_MATCH.find((path) => location.pathname.startsWith(path)) ?? '/';
  return (
    <Layout className="admin-shell min-h-[100dvh]">
      <Sider width={232} collapsedWidth={72} collapsed={collapsed} theme="light" className="admin-sider border-r border-line">
        <div className={`flex h-14 items-center gap-2 border-b border-line ${collapsed ? 'justify-center px-2' : 'px-4'}`}>
          <BrandMark size={34} />
          {!collapsed && <div className="text-sm font-semibold leading-tight text-ink">
            CISC 数字员工
            <div className="text-xs font-normal text-ink-tertiary">平台管理</div>
          </div>}
        </div>
        <Menu mode="inline" selectedKeys={[selected]} items={items} className="border-0 pt-2" />
      </Sider>
      <Layout>
        <Header className="!flex !h-14 !items-center !border-b !border-line !bg-surface !px-5">
          <Button type="text" icon={collapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />} onClick={() => setCollapsed((value) => !value)} aria-label={collapsed ? '展开导航' : '收起导航'} />
          <Typography.Text strong className="ml-1 text-ink">平台管理</Typography.Text>
          <div className="flex-1" />
          <ThemeSwitcher placement="bottomRight" />
          <Avatar size={26} style={{ background: 'var(--dwp-brand)' }}>{account?.name.slice(0, 1)}</Avatar>
          <Typography.Text className="ml-2 text-ink">{account?.name}</Typography.Text>
          <Button type="text" icon={<LogoutOutlined />} onClick={() => void logout()} aria-label="退出登录" />
        </Header>
        <Content className="!bg-page !p-4 lg:!p-6"><div className="admin-content mx-auto w-full max-w-[1480px]"><Outlet /></div></Content>
      </Layout>
    </Layout>
  );
}
