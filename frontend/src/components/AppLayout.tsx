import { useEffect, useState } from 'react';
import {
  AppstoreOutlined,
  HomeOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  RobotOutlined,
  SafetyCertificateOutlined,
  TeamOutlined,
  UserOutlined,
} from '@ant-design/icons';
import { Avatar, Badge, Button, Divider, Input, Layout, Menu, Space, Typography } from 'antd';
import { Link, Outlet, useLocation, useNavigate } from 'react-router-dom';
import { api } from '../api/client';
import { BRAND_PRIMARY } from '../theme';

const { Sider, Header, Content, Footer } = Layout;

const menuItems = [
  { key: '/', icon: <HomeOutlined />, label: <Link to="/">首页</Link> },
  { key: '/employees', icon: <RobotOutlined />, label: <Link to="/employees">数字员工</Link> },
  { key: '/plugins', icon: <AppstoreOutlined />, label: <Link to="/plugins">插件中心</Link> },
  { key: '/security', icon: <SafetyCertificateOutlined />, label: <Link to="/security">安全中心</Link> },
  { key: '/teams', icon: <TeamOutlined />, label: <Link to="/teams">协作团队</Link> },
];

const routeMeta: Record<string, string> = {
  '/': '首页',
  '/employees': '数字员工',
  '/plugins': '插件中心',
  '/security': '安全中心',
  '/teams': '协作团队',
};

type HealthState = 'checking' | 'ok' | 'error';

export default function AppLayout() {
  const location = useLocation();
  const navigate = useNavigate();
  const [collapsed, setCollapsed] = useState(false);
  const [keyword, setKeyword] = useState('');
  const [health, setHealth] = useState<HealthState>('checking');

  const selected = menuItems.find((m) => location.pathname.startsWith(m.key))?.key ?? '/';
  const title = routeMeta[selected] ?? '数字员工平台';

  useEffect(() => {
    let active = true;
    api
      .health()
      .then((res) => {
        if (active) setHealth(res.status === 'ok' ? 'ok' : 'error');
      })
      .catch(() => {
        if (active) setHealth('error');
      });
    return () => {
      active = false;
    };
  }, []);

  const healthMeta: Record<HealthState, { status: 'processing' | 'success' | 'error'; text: string }> = {
    checking: { status: 'processing', text: '服务检测中' },
    ok: { status: 'success', text: '服务正常' },
    error: { status: 'error', text: '服务离线' },
  };

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <a href="#main-content" className="skip-link">
        跳到主要内容
      </a>
      <Sider
        theme="dark"
        width={220}
        collapsedWidth={64}
        collapsible
        collapsed={collapsed}
        trigger={null}
        breakpoint="lg"
        onBreakpoint={setCollapsed}
      >
        <div
          style={{
            height: 64,
            display: 'flex',
            alignItems: 'center',
            justifyContent: collapsed ? 'center' : 'flex-start',
            gap: 10,
            padding: collapsed ? '0 16px' : '0 20px',
            overflow: 'hidden',
          }}
        >
          <img
            src="/brand-logo.jpg"
            alt="数字员工平台"
            style={{
              height: 34,
              width: 'auto',
              maxWidth: collapsed ? 48 : 120,
              flexShrink: 0,
              objectFit: 'contain',
            }}
          />
          {!collapsed && (
            <div style={{ minWidth: 0 }}>
              <div style={{ color: '#fff', fontSize: 16, fontWeight: 600, lineHeight: '20px', whiteSpace: 'nowrap' }}>
                数字员工平台
              </div>
              <div style={{ color: 'rgba(255,255,255,0.45)', fontSize: 10, lineHeight: '14px', whiteSpace: 'nowrap' }}>
                Digital Employee Platform
              </div>
            </div>
          )}
        </div>
        <Menu
          theme="dark"
          mode="inline"
          selectedKeys={[selected]}
          items={menuItems}
          style={{ borderInlineEnd: 'none' }}
        />
      </Sider>
      <Layout>
        <Header
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 16,
            borderBottom: '1px solid #eef1f6',
          }}
        >
          <Button
            type="text"
            aria-label={collapsed ? '展开菜单' : '收起菜单'}
            icon={collapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
            onClick={() => setCollapsed((value) => !value)}
          />
          <Typography.Text strong style={{ fontSize: 16, whiteSpace: 'nowrap' }}>
            {title}
          </Typography.Text>
          <div style={{ flex: 1 }} />
          <Input.Search
            allowClear
            aria-label="搜索员工"
            placeholder="搜索员工名称 / 工号 / 部门"
            style={{ width: 240 }}
            value={keyword}
            onChange={(event) => setKeyword(event.target.value)}
            onSearch={(value) => {
              const next = value.trim();
              navigate(next ? `/employees?keyword=${encodeURIComponent(next)}` : '/employees');
            }}
          />
          <Badge status={healthMeta[health].status} text={healthMeta[health].text} />
          <Divider type="vertical" />
          <Space size={8}>
            <Avatar size="small" style={{ background: BRAND_PRIMARY }} icon={<UserOutlined />} />
            <Typography.Text type="secondary" style={{ fontSize: 13, whiteSpace: 'nowrap' }}>
              演示用户
            </Typography.Text>
          </Space>
        </Header>
        <Content
          id="main-content"
          tabIndex={-1}
          style={{ padding: 24, width: '100%', maxWidth: 1600, margin: '0 auto', outline: 'none' }}
        >
          <div key={location.pathname} className="page-enter">
            <Outlet />
          </div>
        </Content>
        <Footer
          style={{
            textAlign: 'center',
            padding: '8px 24px 20px',
            background: 'transparent',
            color: '#66748c',
            fontSize: 12,
          }}
        >
          数字员工平台 Demo · 所有数据均为虚构，仅供内部演示
        </Footer>
      </Layout>
    </Layout>
  );
}
