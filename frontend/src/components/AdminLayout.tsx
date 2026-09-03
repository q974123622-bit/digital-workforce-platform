import { ApartmentOutlined, AuditOutlined, DatabaseOutlined, LogoutOutlined, RobotOutlined } from '@ant-design/icons';
import { Avatar, Button, Layout, Menu, Typography } from 'antd';
import type { MenuProps } from 'antd';
import { Link, Outlet, useLocation } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';

const { Sider, Header, Content } = Layout;

const items: MenuProps['items'] = [
  { key: '/', icon: <ApartmentOutlined />, label: <Link to="/">运行概览</Link> },
  { key: '/agents', icon: <RobotOutlined />, label: <Link to="/agents">数字员工</Link> },
  { key: '/plugins', icon: <DatabaseOutlined />, label: <Link to="/plugins">插件治理</Link> },
  { key: '/security', icon: <AuditOutlined />, label: <Link to="/security">权限与审计</Link> },
];

export default function AdminLayout() {
  const location = useLocation();
  const { account, logout } = useAuth();
  const selected = ['/agents', '/employees', '/plugins', '/security'].find((path) => location.pathname.startsWith(path)) ?? '/';
  return (
    <Layout className="min-h-screen">
      <Sider width={216} theme="light" className="border-r border-[#e5e6eb]">
        <div className="flex h-14 items-center gap-2 border-b border-[#e5e6eb] px-4">
          <img src="/brand-logo.jpg" alt="" className="h-8 w-8 object-contain" />
          <div className="text-sm font-medium leading-tight">数字员工工作台<div className="text-xs font-normal text-[#86909c]">管理后台</div></div>
        </div>
        <Menu mode="inline" selectedKeys={[selected]} items={items} className="border-0 pt-2" />
      </Sider>
      <Layout>
        <Header className="!flex !h-14 !items-center !border-b !border-[#e5e6eb] !bg-white !px-5">
          <Typography.Text strong>平台管理</Typography.Text>
          <div className="flex-1" />
          <Avatar size={26}>{account?.name.slice(0, 1)}</Avatar>
          <Typography.Text className="ml-2">{account?.name}</Typography.Text>
          <Button type="text" icon={<LogoutOutlined />} onClick={logout} aria-label="退出登录" />
        </Header>
        <Content className="!bg-[#f2f3f5] !p-5"><Outlet /></Content>
      </Layout>
    </Layout>
  );
}
