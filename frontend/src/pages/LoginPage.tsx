import { LockOutlined, UserOutlined } from '@ant-design/icons';
import { Alert, Button, Form, Input, Typography } from 'antd';
import { useState } from 'react';
import { useAuth } from '../context/AuthContext';

const { Text, Title } = Typography;

export default function LoginPage() {
  const { login } = useAuth();
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  const submit = async (values: { username: string; password: string }) => {
    setLoading(true);
    setError('');
    try {
      await login(values.username, values.password);
    } catch (cause) {
      setError((cause as Error).message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <main className="min-h-screen bg-[#f2f3f5] px-4 py-10 sm:flex sm:items-center sm:justify-center">
      <section className="mx-auto w-full max-w-[420px] border border-[#e5e6eb] bg-white p-6 shadow-sm sm:p-8">
        <div className="mb-7 flex items-center gap-3 border-b border-[#f0f0f0] pb-5">
          <img src="/brand-logo.jpg" alt="" className="h-10 w-10 object-contain" />
          <div>
            <Title level={4} className="!mb-0 !text-[#1d2129]">数字员工工作台</Title>
            <Text type="secondary">使用公司账号进入</Text>
          </div>
        </div>
        {error && <Alert className="mb-4" type="error" showIcon message={error} />}
        <Form layout="vertical" onFinish={submit} initialValues={{ username: 'E10281', password: 'Demo@123456' }}>
          <Form.Item label="账号" name="username" rules={[{ required: true, message: '请输入账号' }]}>
            <Input size="large" prefix={<UserOutlined />} autoComplete="username" placeholder="员工工号" />
          </Form.Item>
          <Form.Item label="密码" name="password" rules={[{ required: true, message: '请输入密码' }]}>
            <Input.Password size="large" prefix={<LockOutlined />} autoComplete="current-password" />
          </Form.Item>
          <Button className="mt-2" type="primary" htmlType="submit" size="large" block loading={loading}>
            登录
          </Button>
        </Form>
        <Text type="secondary" className="mt-5 block text-center text-xs">
          当前为内部测试环境，所有知识访问均记录审计轨迹
        </Text>
      </section>
    </main>
  );
}

