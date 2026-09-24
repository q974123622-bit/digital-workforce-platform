import { BulbOutlined, LogoutOutlined, SafetyCertificateOutlined } from '@ant-design/icons';
import { Avatar, Button, Descriptions, Tag, Typography } from 'antd';
import { Link } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import ThemeSwitcher from '../components/ui/ThemeSwitcher';

const { Title, Text } = Typography;

const ROLE_LABEL: Record<string, string> = {
  user: '员工',
  agent_admin: 'AI员工管理员',
  security_admin: '安全管理员',
  platform_admin: '平台管理员',
};

export default function MePage() {
  const { account, logout } = useAuth();
  if (!account) return null;
  return (
    <div className="page-shell mx-auto max-w-[880px] px-4 py-6">
      <div className="profile-hero rounded-[16px] border border-line bg-surface p-6 sm:p-8">
        <div className="flex items-center gap-4">
          <Avatar
            size={56}
            src={account.avatar_url ?? undefined}
            style={{ background: 'var(--dwp-brand)', fontSize: 24 }}
          >
            {account.name.slice(0, 1)}
          </Avatar>
          <div className="min-w-0">
            <Title level={4} className="!mb-0.5">{account.name}</Title>
            <Text type="secondary">{account.department}</Text>
          </div>
        </div>
        <Descriptions column={1} className="mt-5" size="small">
          <Descriptions.Item label="工号">{account.employee_no}</Descriptions.Item>
          <Descriptions.Item label="用工类型">
            {account.employment_type === 'formal' ? '正式员工' : '实习生'}
          </Descriptions.Item>
          <Descriptions.Item label="角色">
            {account.roles.map((role) => (
              <Tag key={role} color="blue">{ROLE_LABEL[role] ?? role}</Tag>
            ))}
          </Descriptions.Item>
        </Descriptions>
      </div>

      <div className="mt-4 rounded-container border border-line bg-surface p-4 md:hidden">
        <div className="mb-3 text-xs font-medium text-ink-tertiary">显示偏好</div>
        <ThemeSwitcher placement="bottomLeft" showLabel />
      </div>

      <div className="mt-4 grid gap-3 sm:grid-cols-2">
        <Link
          to="/memories"
          className="flex items-center gap-4 rounded-container bg-surface p-5 transition-colors hover:bg-brand-soft"
        >
          <span className="grid h-11 w-11 place-items-center rounded-control bg-brand-soft text-brand">
            <SafetyCertificateOutlined className="text-xl" />
          </span>
          <div>
            <div className="font-medium text-ink">我的记忆</div>
            <div className="text-xs text-ink-secondary">查看与管理AI员工为我保存的长期记忆</div>
          </div>
        </Link>
        <Link
          to="/plugins"
          className="flex items-center gap-4 rounded-container bg-surface p-5 transition-colors hover:bg-brand-soft"
        >
          <span className="grid h-11 w-11 place-items-center rounded-control bg-brand-soft text-brand">
            <BulbOutlined className="text-xl" />
          </span>
          <div>
            <div className="font-medium text-ink">我的插件</div>
            <div className="text-xs text-ink-secondary">管理已启用的插件与我的提交</div>
          </div>
        </Link>
      </div>

      <div className="mt-4 flex items-center justify-between gap-4 rounded-container border border-line bg-surface p-5">
        <div className="min-w-0">
          <div className="font-medium text-ink">退出当前账号</div>
          <div className="text-xs text-ink-secondary">退出后需重新输入访问密钥登录</div>
        </div>
        <Button icon={<LogoutOutlined />} onClick={() => void logout()}>
          退出登录
        </Button>
      </div>
    </div>
  );
}
