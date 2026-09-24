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
import GuideButton from '../features/guide/GuideButton';
import GuideDrawer from '../features/guide/GuideDrawer';
import { useGuideLauncher } from '../features/guide/useGuideLauncher';
import BrandMark from './ui/BrandMark';
import ThemeSwitcher from './ui/ThemeSwitcher';

const { Sider, Header, Content } = Layout;

const items: MenuProps['items'] = [
  { key: '/', icon: <ApartmentOutlined />, label: <Link to="/">运行概览</Link> },
  { key: '/agents', icon: <RobotOutlined />, label: <Link to="/agents">AI员工</Link> },
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
  // 操作指南：管理侧内容与员工侧完全分开，各自记忆「是否已自动提示过」。
  const guide = useGuideLauncher('admin');
  const selected = SELECT_MATCH.find((path) => location.pathname.startsWith(path)) ?? '/';
  return (
    <Layout className="admin-shell min-h-[100dvh]">
      <Sider width={232} collapsedWidth={72} collapsed={collapsed} theme="light" className="admin-sider border-r border-line">
        <div className={`flex h-14 items-center gap-2 border-b border-line ${collapsed ? 'justify-center px-2' : 'px-4'}`}>
          <BrandMark size={34} />
          {!collapsed && <div className="text-sm font-semibold leading-tight text-ink">
            CISC AI员工
            <div className="text-xs font-normal text-ink-tertiary">平台管理</div>
          </div>}
        </div>
        <Menu mode="inline" selectedKeys={[selected]} items={items} className="border-0 pt-2" />
        {/* 底部固定操作指南入口：展开态为整行按钮，收起态为居中图标（见 .admin-sider-foot） */}
        <div className="admin-sider-foot">
          <GuideButton variant="sider" collapsed={collapsed} expanded={guide.open} onOpen={guide.openGuide} />
        </div>
      </Sider>
      <Layout>
        <Header className="!flex !h-14 !items-center !border-b !border-line !bg-surface !px-5">
          <Button type="text" icon={collapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />} onClick={() => setCollapsed((value) => !value)} aria-label={collapsed ? '展开导航' : '收起导航'} />
          <Typography.Text strong className="ml-1 text-ink">平台管理</Typography.Text>
          <div className="flex-1" />
          <ThemeSwitcher placement="bottomRight" />
          <Avatar size={26} src={account?.avatar_url ?? undefined} style={{ background: 'var(--dwp-brand)' }}>{account?.name.slice(0, 1)}</Avatar>
          <Typography.Text className="ml-2 text-ink">{account?.name}</Typography.Text>
          <Button type="text" icon={<LogoutOutlined />} onClick={() => void logout()} aria-label="退出登录" />
        </Header>
        <Content className="!bg-page !p-4 lg:!p-6"><div className="admin-content mx-auto w-full max-w-[1480px]"><Outlet /></div></Content>
      </Layout>

      <GuideDrawer
        open={guide.open}
        onClose={guide.closeGuide}
        audience="admin"
        notAutoPrompt={guide.notAutoPrompt}
        onNotAutoPromptChange={guide.setNotAutoPrompt}
      />
    </Layout>
  );
}
