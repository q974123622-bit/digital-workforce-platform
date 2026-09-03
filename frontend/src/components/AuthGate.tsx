import { Alert, Button, Form, Input, Modal, Spin } from 'antd';
import { useState } from 'react';
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
  if (!admin && !account.roles.includes('user')) {
    return <div className="flex min-h-screen flex-col items-center justify-center gap-3 bg-[#f2f3f5] text-[#4e5969]">管理员账号不进入员工工作台<a className="text-[#165dff]" href="/admin/">进入管理后台</a></div>;
  }
  return <>{children}{account.must_change_password && <RequiredPasswordChange />}</>;
}

function RequiredPasswordChange() {
  const { changePassword } = useAuth();
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const submit = async (values: { current: string; next: string; confirm: string }) => {
    setError(''); setLoading(true);
    try { await changePassword(values.current, values.next); }
    catch (cause) { setError((cause as Error).message); }
    finally { setLoading(false); }
  };
  return <Modal open closable={false} maskClosable={false} footer={null} title="首次登录，请修改初始密码">
    {error && <Alert className="mb-3" type="error" showIcon message={error} />}
    <Form layout="vertical" onFinish={submit}>
      <Form.Item name="current" label="当前密码" rules={[{ required: true }]}><Input.Password autoComplete="current-password" /></Form.Item>
      <Form.Item name="next" label="新密码" rules={[{ required: true }, { min: 10, message: '至少 10 个字符' }]}><Input.Password autoComplete="new-password" /></Form.Item>
      <Form.Item name="confirm" label="确认新密码" dependencies={['next']} rules={[{ required: true }, ({ getFieldValue }) => ({ validator: (_, value) => value === getFieldValue('next') ? Promise.resolve() : Promise.reject(new Error('两次密码不一致')) })]}><Input.Password autoComplete="new-password" /></Form.Item>
      <Button htmlType="submit" type="primary" block loading={loading}>保存新密码</Button>
    </Form>
  </Modal>;
}
