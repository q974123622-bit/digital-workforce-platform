import { Spin } from 'antd';
import type { ReactNode } from 'react';
import { useAuth } from '../context/AuthContext';
import LoginPage from '../pages/LoginPage';

export default function AuthGate({ children, admin = false }: { children: ReactNode; admin?: boolean }) {
  const { account, checking } = useAuth();
  if (checking) {
    return <div className="flex min-h-screen items-center justify-center bg-[#f2f3f5]"><Spin /></div>;
  }
  if (!account) return <LoginPage />;
  if (admin && !account.roles.some((role) => ['agent_admin', 'security_admin', 'platform_admin'].includes(role))) {
    return <div className="flex min-h-screen items-center justify-center bg-[#f2f3f5] text-[#4e5969]">当前账号没有管理后台权限</div>;
  }
  return children;
}

