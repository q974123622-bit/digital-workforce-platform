import { Layout, Menu, Typography } from 'antd';
import { Link, Outlet, useLocation } from 'react-router-dom';

const { Sider, Header, Content } = Layout;

const menuItems = [
  { key: '/', label: <Link to="/">首页</Link> },
  { key: '/employees', label: <Link to="/employees">数字员工</Link> },
  { key: '/plugins', label: <Link to="/plugins">插件中心</Link> },
  { key: '/security', label: <Link to="/security">安全中心</Link> },
  { key: '/teams', label: <Link to="/teams">协作团队</Link> },
];

export default function AppLayout() {
  const location = useLocation();
  const selected = menuItems.find((m) => location.pathname.startsWith(m.key))?.key ?? '/';

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Sider theme="dark" width={220}>
        <div style={{ padding: 16, color: '#fff', fontSize: 16, fontWeight: 600 }}>数字员工平台</div>
        <Menu theme="dark" mode="inline" selectedKeys={[selected]} items={menuItems} />
      </Sider>
      <Layout>
        <Header style={{ background: '#fff', padding: '0 24px' }}>
          <Typography.Text strong>Sprint 1 · Platform Skeleton · 数据均为虚构</Typography.Text>
        </Header>
        <Content style={{ margin: 24 }}>
          <Outlet />
        </Content>
      </Layout>
    </Layout>
  );
}
