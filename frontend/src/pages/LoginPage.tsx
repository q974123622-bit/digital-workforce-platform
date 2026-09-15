import { CheckCircleFilled, KeyOutlined, SafetyCertificateOutlined } from '@ant-design/icons';
import { Alert, Button, Form, Input, Typography } from 'antd';
import { useState } from 'react';
import { useAuth } from '../context/AuthContext';
import BrandMark from '../components/ui/BrandMark';
import ThemeSwitcher from '../components/ui/ThemeSwitcher';

const { Text, Title } = Typography;

export default function LoginPage() {
  const { login } = useAuth();
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  const submit = async (values: { accessKey: string }) => {
    setLoading(true);
    setError('');
    try {
      const account = await login(values.accessKey);
      const isAdmin = account.roles.some((role) => ['agent_admin', 'security_admin', 'platform_admin'].includes(role));
      const onAdminPage = window.location.pathname.startsWith('/admin');
      if (isAdmin && !onAdminPage) window.location.assign('/admin/');
      if (!isAdmin && onAdminPage) window.location.assign('/workplace');
    } catch (cause) {
      setError((cause as Error).message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <main className="login-shell relative min-h-screen px-4 py-8 lg:flex lg:items-center lg:justify-center">
      <div className="absolute right-4 top-4 z-10"><ThemeSwitcher placement="bottomRight" /></div>
      <section className="login-product-panel hidden lg:flex">
        <div className="max-w-[500px]">
          <div className="mb-8 flex items-center gap-3">
            <BrandMark size={48} />
            <span className="text-base font-semibold">数字员工工作台</span>
          </div>
          <h1 className="m-0 text-[38px] font-semibold leading-[1.25] tracking-[-0.03em] text-white">让每一位同事，都有一个<br />可信赖的数字搭档</h1>
          <p className="mt-5 max-w-[440px] text-base leading-7 text-white/70">连接企业知识、岗位经验和工作工具，在统一权限与审计边界内完成日常问答与协作。</p>
          <div className="mt-10 grid gap-3 text-sm text-white/85">
            <span><CheckCircleFilled className="mr-2 text-brand" />知识来源可追溯</span>
            <span><CheckCircleFilled className="mr-2 text-brand" />执行过程可核验</span>
            <span><CheckCircleFilled className="mr-2 text-brand" />数据访问受策略保护</span>
          </div>
        </div>
      </section>
      <section className="login-form-panel mx-auto flex w-full max-w-[480px] items-center lg:mx-0">
      <div className="w-full rounded-[16px] border border-line bg-surface p-6 sm:p-9">
        <div className="mb-7 flex items-center gap-3 border-b border-line-light pb-5">
          <span className="grid h-11 w-11 place-items-center rounded-[12px] bg-brand-soft text-xl text-brand"><SafetyCertificateOutlined /></span>
          <div>
            <Title level={3} className="!mb-0 !text-ink">欢迎回来</Title>
            <Text type="secondary">输入访问密钥，系统会自动识别你的身份</Text>
          </div>
        </div>
        {error && <Alert className="mb-4" type="error" showIcon message={error} />}
        <Form layout="vertical" onFinish={submit}>
          <Form.Item label="访问密钥" name="accessKey" rules={[{ required: true, message: '请输入访问密钥' }, { min: 12, message: '访问密钥至少 12 个字符' }]}>
            <Input.Password size="large" prefix={<KeyOutlined />} autoComplete="off" placeholder="请输入你的访问密钥" />
          </Form.Item>
          <Button className="mt-2" type="primary" htmlType="submit" size="large" block loading={loading}>
            进入工作台
          </Button>
        </Form>
        <Text type="secondary" className="mt-5 block text-center text-xs">
          密钥仅用于本次登录，不会保存在浏览器中
        </Text>
      </div>
      </section>
    </main>
  );
}
