import { AppstoreOutlined, LogoutOutlined, MessageOutlined } from '@ant-design/icons';
import { Avatar, Button, Dropdown, Typography } from 'antd';
import { Link, Outlet } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';

const { Text } = Typography;

export default function PortalLayout() {
  const { account, logout } = useAuth();
  if (!account) return null;
  const canAdmin = account.roles.some((role) => ['agent_admin', 'security_admin', 'platform_admin'].includes(role));
  return (
    <div className="min-h-screen bg-[#f2f3f5] text-[#1d2129]">
      <header className="h-14 border-b border-[#e5e6eb] bg-white">
        <div className="mx-auto flex h-full max-w-[1440px] items-center px-3 sm:px-6">
          <Link to="/workplace" className="flex items-center gap-2 text-[#1d2129]">
            <img src="/brand-logo.jpg" alt="" className="h-8 w-8 object-contain" />
            <span className="hidden text-[15px] font-medium sm:inline">数字员工工作台</span>
          </Link>
          <nav className="ml-6 flex h-full items-center">
            <Link to="/workplace" className="flex h-full items-center gap-2 border-b-2 border-[#165dff] px-3 text-sm text-[#165dff]">
              <MessageOutlined /> 工作消息
            </Link>
          </nav>
          <div className="flex-1" />
          {canAdmin && (
            <a href="/admin/" className="mr-2 hidden items-center gap-1 px-3 py-2 text-sm text-[#4e5969] hover:text-[#165dff] sm:flex">
              <AppstoreOutlined /> 管理后台
            </a>
          )}
          <Dropdown menu={{ items: [{ key: 'logout', icon: <LogoutOutlined />, label: '退出登录', onClick: logout }] }}>
            <Button type="text" className="!flex !h-10 !items-center !gap-2">
              <Avatar size={28}>{account.name.slice(0, 1)}</Avatar>
              <Text className="hidden sm:inline">{account.name}</Text>
            </Button>
          </Dropdown>
        </div>
      </header>
      <main className="mx-auto max-w-[1440px] p-0 sm:p-4">
        <Outlet />
      </main>
    </div>
  );
}

