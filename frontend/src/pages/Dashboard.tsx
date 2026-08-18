import { useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  AppstoreOutlined,
  ArrowRightOutlined,
  AuditOutlined,
  RobotOutlined,
  SafetyCertificateOutlined,
  TeamOutlined,
  ThunderboltOutlined,
  UserSwitchOutlined,
} from '@ant-design/icons';
import { Card, Col, Empty, List, Row, Space, Statistic, Typography } from 'antd';
import type { AuditEvent, Employee, EmployeeType, Plugin, Team } from '@dwp/shared-schema';
import { api } from '../api/client';
import { DecisionTag, TYPE_META } from '../components/tags';
import { ErrorState, LoadingState } from '../components/PageState';
import { useAsyncData } from '../hooks/useAsyncData';

const formatTime = (ts?: string | null) => (ts ? new Date(ts).toLocaleString() : '—');

interface DashboardData {
  employees: Employee[];
  plugins: Plugin[];
  teams: Team[];
  audit: AuditEvent[];
}

export default function Dashboard() {
  const navigate = useNavigate();
  const fetcher = useCallback(
    () =>
      Promise.all([api.listEmployees(), api.listPlugins(), api.listTeams(), api.listAudit()]).then(
        ([employees, plugins, teams, audit]) => ({ employees, plugins, teams, audit }),
      ),
    [],
  );
  const { data, loading, error, reload } = useAsyncData<DashboardData>(fetcher);

  if (loading) return <LoadingState rows={8} />;
  if (error) return <ErrorState onRetry={reload} />;

  const employees = data?.employees ?? [];
  const plugins = data?.plugins ?? [];
  const teams = data?.teams ?? [];
  const audit = data?.audit ?? [];

  const count = (type: string) => employees.filter((employee) => employee.type === type).length;
  const total = employees.length;
  const types: EmployeeType[] = ['twin', 'virtual', 'rpa'];

  const stats = [
    { title: '数字员工总数', value: total, icon: <RobotOutlined />, hex: '#2f54eb', bg: '#eef4ff' },
    { title: '数字分身', value: count('twin'), icon: <UserSwitchOutlined />, hex: '#722ed1', bg: '#f3f0ff' },
    { title: '虚拟员工', value: count('virtual'), icon: <TeamOutlined />, hex: '#13c2c2', bg: '#e6fffb' },
    { title: 'RPA', value: count('rpa'), icon: <ThunderboltOutlined />, hex: '#fa8c16', bg: '#fff7e6' },
  ];

  const entries = [
    {
      key: '/employees',
      title: '数字员工',
      desc: `管理数字分身、虚拟员工与 RPA · 共 ${employees.length} 名`,
      icon: <RobotOutlined />,
      hex: '#2f54eb',
      bg: '#eef4ff',
    },
    {
      key: '/plugins',
      title: '插件中心',
      desc: `插件统一登记与授权管理 · 共 ${plugins.length} 个`,
      icon: <AppstoreOutlined />,
      hex: '#722ed1',
      bg: '#f9f0ff',
    },
    {
      key: '/security',
      title: '安全中心',
      desc: '权限策略与全链路审计日志',
      icon: <SafetyCertificateOutlined />,
      hex: '#cf1322',
      bg: '#fff1f0',
    },
    {
      key: '/teams',
      title: '协作团队',
      desc: `团队任务协作与审批 · 共 ${teams.length} 个`,
      icon: <TeamOutlined />,
      hex: '#13c2c2',
      bg: '#e6fffb',
    },
  ];

  const openEntry = (key: string) => {
    navigate(key);
  };

  return (
    <div>
      <Card
        style={{ marginBottom: 16, borderRadius: 16, border: 'none' }}
        styles={{
          body: {
            padding: '28px 32px',
            background: 'linear-gradient(120deg, #0d1b3e 0%, #1d3f9e 60%, #2f54eb 100%)',
            borderRadius: 16,
          },
        }}
      >
        <Row align="middle" justify="space-between" gutter={[16, 16]}>
          <Col>
            <Typography.Title level={2} style={{ color: '#fff', margin: 0 }}>
              数字员工平台
            </Typography.Title>
            <Typography.Paragraph style={{ color: 'rgba(255,255,255,0.72)', margin: '8px 0 0' }}>
              面向券商内部场景的数字员工 PoC · 统一管理数字分身、虚拟员工与 RPA
            </Typography.Paragraph>
          </Col>
          <Col>
            <div style={{ textAlign: 'right' }}>
              <img
                src="/brand-logo.jpg"
                alt="数字员工平台"
                style={{ height: 36, width: 'auto', objectFit: 'contain', marginBottom: 12 }}
              />
              <div style={{ color: 'rgba(255,255,255,0.6)', fontSize: 12 }}>当前在线</div>
              <div style={{ color: '#fff', fontSize: 30, fontWeight: 700, lineHeight: 1.2 }}>
                {total}
                <span style={{ fontSize: 14, fontWeight: 400, marginLeft: 4 }}>名</span>
              </div>
            </div>
          </Col>
        </Row>
      </Card>

      <Row gutter={[16, 16]}>
        {stats.map((stat) => (
          <Col xs={12} lg={6} key={stat.title}>
            <Card styles={{ body: { padding: 20 } }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
                <div
                  style={{
                    width: 44,
                    height: 44,
                    borderRadius: 12,
                    background: stat.bg,
                    color: stat.hex,
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    fontSize: 20,
                    flexShrink: 0,
                  }}
                >
                  {stat.icon}
                </div>
                <Statistic
                  title={stat.title}
                  value={stat.value}
                  className="tabular-nums"
                  valueStyle={{ fontSize: 24, fontWeight: 600 }}
                />
              </div>
            </Card>
          </Col>
        ))}
      </Row>

      <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
        {entries.map((entry) => (
          <Col xs={12} lg={6} key={entry.key}>
            <Card
              className="clickable-card"
              role="button"
              tabIndex={0}
              aria-label={`进入${entry.title}`}
              onClick={() => openEntry(entry.key)}
              onKeyDown={(event) => {
                if (event.key === 'Enter' || event.key === ' ') {
                  event.preventDefault();
                  openEntry(entry.key);
                }
              }}
              styles={{ body: { padding: 20 } }}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
                <div
                  style={{
                    width: 44,
                    height: 44,
                    borderRadius: 12,
                    background: entry.bg,
                    color: entry.hex,
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    fontSize: 20,
                    flexShrink: 0,
                  }}
                >
                  {entry.icon}
                </div>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                    <Typography.Text strong style={{ fontSize: 15 }}>
                      {entry.title}
                    </Typography.Text>
                    <ArrowRightOutlined style={{ color: '#7d8aa0' }} />
                  </div>
                  <Typography.Text type="secondary" style={{ fontSize: 13 }}>
                    {entry.desc}
                  </Typography.Text>
                </div>
              </div>
            </Card>
          </Col>
        ))}
      </Row>

      <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
        <Col xs={24} lg={10}>
          <Card title="数字员工构成" styles={{ body: { padding: '20px 24px' } }}>
            {total === 0 ? (
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无员工数据" />
            ) : (
              types.map((type) => {
                const meta = TYPE_META[type];
                const value = count(type);
                const pct = Math.round((value / total) * 100);
                return (
                  <div key={type} style={{ marginBottom: 16 }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 6 }}>
                      <Space size={8}>
                        <span style={{ color: meta.hex }}>{meta.icon}</span>
                        <Typography.Text>{meta.label}</Typography.Text>
                      </Space>
                      <Typography.Text type="secondary" className="tabular-nums">
                        {value} 名 · {pct}%
                      </Typography.Text>
                    </div>
                    <div style={{ height: 8, borderRadius: 4, background: '#f0f3f7', overflow: 'hidden' }}>
                      <div
                        style={{
                          width: `${pct}%`,
                          height: '100%',
                          borderRadius: 4,
                          background: `linear-gradient(90deg, ${meta.hex}88, ${meta.hex})`,
                        }}
                      />
                    </div>
                  </div>
                );
              })
            )}
          </Card>
        </Col>
        <Col xs={24} lg={14}>
          <Card
            title="最近审计动态"
            extra={
              <Typography.Link onClick={() => navigate('/security')} style={{ fontSize: 13 }}>
                查看全部
              </Typography.Link>
            }
          >
            {audit.length === 0 ? (
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无审计事件" />
            ) : (
              <List
                size="small"
                dataSource={audit.slice(0, 5)}
                renderItem={(item) => (
                  <List.Item style={{ padding: '10px 0' }}>
                    <List.Item.Meta
                      title={
                        <Space size={8}>
                          <AuditOutlined style={{ color: '#66748c' }} />
                          <span>
                            {item.actor} · {item.action}
                            {item.plugin_id ? ` · ${item.plugin_id}` : ''}
                          </span>
                        </Space>
                      }
                      description={<span style={{ fontSize: 13 }}>{item.reason ?? item.result_summary ?? '—'}</span>}
                    />
                    <div style={{ textAlign: 'right', marginLeft: 16 }}>
                      <DecisionTag value={item.decision} />
                      <div style={{ fontSize: 12, color: '#66748c', marginTop: 4 }}>{formatTime(item.ts)}</div>
                    </div>
                  </List.Item>
                )}
              />
            )}
          </Card>
        </Col>
      </Row>
    </div>
  );
}
