import { useCallback, useEffect, useState } from 'react';
import { CrownOutlined, TeamOutlined } from '@ant-design/icons';
import { Alert, Avatar, Card, Col, Empty, Row, Space, Table, Tag, Typography } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import type { Employee, Team, TeamDetail, TeamMember } from '@dwp/shared-schema';
import { api } from '../api/client';
import { BRAND_PRIMARY } from '../theme';
import { EmptyState, ErrorState, LoadingState } from '../components/PageState';
import { useAsyncData } from '../hooks/useAsyncData';

const AVATAR_COLORS = ['#2f54eb', '#722ed1', '#13c2c2', '#fa8c16', '#52c41a'];

const avatarColor = (value: string) => {
  const sum = [...value].reduce((acc, char) => acc + char.charCodeAt(0), 0);
  return AVATAR_COLORS[sum % AVATAR_COLORS.length];
};

interface TeamsData {
  teams: Team[];
  employees: Employee[];
  details: Record<string, TeamDetail>;
}

export default function Teams() {
  const [selectedId, setSelectedId] = useState<string>();
  const fetcher = useCallback(async (): Promise<TeamsData> => {
    const [teamRows, empRows] = await Promise.all([api.listTeams(), api.listEmployees()]);
    const detailRows = await Promise.all(teamRows.map((team) => api.getTeam(team.id)));
    return {
      teams: teamRows,
      employees: empRows,
      details: Object.fromEntries(detailRows.map((row) => [row.id, row])) as Record<string, TeamDetail>,
    };
  }, []);
  const { data, loading, error, reload } = useAsyncData<TeamsData>(fetcher);

  useEffect(() => {
    if (data && data.teams.length > 0 && !selectedId) {
      setSelectedId(data.teams[0].id);
    }
  }, [data, selectedId]);

  if (loading) return <LoadingState rows={6} />;
  if (error) return <ErrorState onRetry={reload} />;

  const teams = data?.teams ?? [];
  const employees = data?.employees ?? [];
  const firstId = teams[0]?.id;
  const activeId = selectedId ?? firstId;
  const detail = activeId ? data?.details[activeId] ?? null : null;

  const nameOf = (employeeId: string) =>
    employees.find((employee) => employee.employee_no === employeeId)?.name ?? employeeId;

  const handleSelect = (teamId: string) => {
    setSelectedId(teamId);
  };

  const memberColumns: ColumnsType<TeamMember> = [
    {
      title: '成员',
      key: 'member',
      render: (_: unknown, member: TeamMember) => (
        <Space size={10}>
          <Avatar size={26} style={{ background: avatarColor(member.employee_id), flexShrink: 0 }}>
            {nameOf(member.employee_id).slice(0, 1)}
          </Avatar>
          <span>{nameOf(member.employee_id)}</span>
          <span className="mono" style={{ color: '#66748c' }}>
            {member.employee_id}
          </span>
        </Space>
      ),
    },
    {
      title: '角色',
      dataIndex: 'role',
      render: (value: string) =>
        value === 'leader' ? (
          <Tag icon={<CrownOutlined />} color="gold">
            Leader
          </Tag>
        ) : (
          <Tag color="blue">{value === 'worker' ? 'Worker' : value}</Tag>
        ),
    },
  ];

  if (teams.length === 0) return <EmptyState description="暂无团队" />;

  return (
    <div>
      <Typography.Title level={3}>协作团队</Typography.Title>
      <Alert
        type="info"
        showIcon
        message="团队任务协作（LLM 拆解、Worker 执行、审批、汇总）将在后续 Sprint 实现"
        style={{ marginBottom: 16 }}
      />
      <Row gutter={[16, 16]}>
        <Col xs={24} lg={10}>
          <Card title="团队列表" styles={{ body: { padding: 8 } }}>
            {teams.map((team) => {
              const active = team.id === activeId;
              const teamDetail = data?.details[team.id];
              return (
                <div
                  key={team.id}
                  role="button"
                  tabIndex={0}
                  aria-label={`查看团队 ${team.name}`}
                  onClick={() => handleSelect(team.id)}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter' || event.key === ' ') {
                      event.preventDefault();
                      handleSelect(team.id);
                    }
                  }}
                  style={{
                    padding: '12px 16px',
                    marginBottom: 8,
                    borderRadius: 10,
                    cursor: 'pointer',
                    border: `1px solid ${active ? BRAND_PRIMARY : 'transparent'}`,
                    background: active ? '#eef4ff' : 'transparent',
                    transition: 'all 0.2s ease',
                  }}
                >
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8 }}>
                    <Typography.Text strong>{team.name}</Typography.Text>
                    <Tag color={active ? 'blue' : 'default'} style={{ marginInlineEnd: 0 }}>
                      {team.id}
                    </Tag>
                  </div>
                  <Typography.Paragraph type="secondary" style={{ margin: '6px 0 0', fontSize: 13 }} ellipsis={{ rows: 1 }}>
                    {team.description}
                  </Typography.Paragraph>
                  <div style={{ marginTop: 10, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    {teamDetail ? (
                      <Avatar.Group size="small" max={{ count: 4 }}>
                        {teamDetail.members.map((member) => (
                          <Avatar key={member.employee_id} style={{ background: avatarColor(member.employee_id) }}>
                            {nameOf(member.employee_id).slice(0, 1)}
                          </Avatar>
                        ))}
                      </Avatar.Group>
                    ) : (
                      <span />
                    )}
                    <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                      {teamDetail ? `${teamDetail.members.length} 名成员` : '加载中…'}
                    </Typography.Text>
                  </div>
                </div>
              );
            })}
          </Card>
        </Col>
        <Col xs={24} lg={14}>
          {detail ? (
            <Card styles={{ body: { padding: 24 } }}>
              <Row gutter={[16, 16]} align="middle" style={{ marginBottom: 20 }}>
                <Col flex="auto">
                  <Space size={14} align="center">
                    <Avatar size={44} style={{ background: BRAND_PRIMARY }} icon={<TeamOutlined />} />
                    <div>
                      <Space size={8} wrap>
                        <Typography.Title level={4} style={{ margin: 0 }}>
                          {detail.name}
                        </Typography.Title>
                        <Tag>{detail.id}</Tag>
                      </Space>
                      <Typography.Text type="secondary" style={{ fontSize: 13 }}>
                        {detail.description}
                      </Typography.Text>
                    </div>
                  </Space>
                </Col>
                <Col>
                  <Space size={32}>
                    <div>
                      <Typography.Text type="secondary" style={{ fontSize: 12, display: 'block' }}>
                        成员
                      </Typography.Text>
                      <Typography.Text strong>{detail.members.length}</Typography.Text>
                    </div>
                    <div>
                      <Typography.Text type="secondary" style={{ fontSize: 12, display: 'block' }}>
                        Leader
                      </Typography.Text>
                      <Typography.Text strong>{nameOf(detail.leader_employee_id)}</Typography.Text>
                    </div>
                  </Space>
                </Col>
              </Row>
              <Table<TeamMember>
                rowKey="employee_id"
                size="small"
                pagination={false}
                dataSource={detail.members}
                columns={memberColumns}
              />
            </Card>
          ) : (
            <Card>
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="选择一个团队查看详情" />
            </Card>
          )}
        </Col>
      </Row>
    </div>
  );
}
