import { Spin } from 'antd';
import type { ReactNode } from 'react';
import { useAuth } from '../context/AuthContext';
import LoginPage from '../pages/LoginPage';

export default function AuthGate({ children, admin = false }: { children: ReactNode; admin?: boolean }) {
  const { account, checking } = useAuth();
  if (checking) {
    return <div className="flex min-h-screen flex-col items-center justify-center gap-3 bg-page text-ink-secondary"><Spin /><span className="text-sm">正在加载数字员工工作台</span></div>;
  }
  if (!account) return <LoginPage />;
  if (admin && !account.roles.some((role) => ['agent_admin', 'security_admin', 'platform_admin'].includes(role))) {
    return <div className="flex min-h-screen items-center justify-center bg-page text-ink-secondary">当前账号没有管理后台权限</div>;
  }
  if (!admin && !account.roles.includes('user')) {
    return <div className="flex min-h-screen flex-col items-center justify-center gap-3 bg-page text-ink-secondary">管理员账号不进入员工工作台<a className="text-brand" href="/admin/">进入管理后台</a></div>;
  }
  return <>{children}</>;
}
