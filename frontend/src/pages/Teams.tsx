import { useCallback, useEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import {
  CheckCircleOutlined,
  ClockCircleOutlined,
  CrownOutlined,
  ExperimentOutlined,
  RocketOutlined,
  StopOutlined,
  TeamOutlined,
} from '@ant-design/icons';
import { Alert, Avatar, Button, Card, Col, Empty, Input, Row, Space, Table, Tag, Typography } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import type { Employee, TaskRun, Team, TeamDetail, TeamMember } from '@dwp/shared-schema';
import { api } from '../api/client';
import { BRAND_PRIMARY } from '../theme';
import { EmptyState, ErrorState, LoadingState } from '../components/PageState';
import { useAsyncData } from '../hooks/useAsyncData';

const { Text, Paragraph } = Typography;
const AVATAR_COLORS = ['#2f54eb', '#722ed1', '#13c2c2', '#fa8c16', '#52c41a'];

const avatarColor = (value: string) => {
  const sum = [...value].reduce((acc, char) => acc + char.charCodeAt(0), 0);
  return AVATAR_COLORS[sum % AVATAR_COLORS.length];
};

const TASK_STATUS: Record<string, { label: string; color: string; icon: ReactNode }> = {
  pending: { label: '待执行', color: 'default', icon: <ClockCircleOutlined /> },
  running: { label: '执行中', color: 'processing', icon: <ExperimentOutlined /> },
  approval: { label: '待审批', color: 'warning', icon: <ClockCircleOutlined /> },
  completed: { label: '已完成', color: 'success', icon: <CheckCircleOutlined /> },
  denied: { label: '已拒绝', color: 'error', icon: <StopOutlined /> },
  failed: { label: '失败', color: 'error', icon: <StopOutlined /> },
};

interface TeamsData {
  teams: Team[];
  employees: Employee[];
  details: Record<string, TeamDetail>;
}

export default function Teams() {
  const [selectedId, setSelectedId] = useState<string>();
  const [activeTab, setActiveTab] = useState('members');
  const [taskText, setTaskText] = useState('');
  const [latestTask, setLatestTask] = useState<TaskRun | null>(null);
  const [creating, setCreating] = useState(false);
  const [acting, setActing] = useState(false);
  const [taskError, setTaskError] = useState<string>();
  const pollTimer = useRef<ReturnType<typeof setTimeout>>();

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
  const activeId = selectedId ?? data?.teams[0]?.id;

  useEffect(() => {
    if (data && data.teams.length > 0 && !selectedId) {
      setSelectedId(data.teams[0].id);
    }
  }, [data, selectedId]);

  // 任务运行中/待审批时轮询刷新
  useEffect(() => {
    if (!latestTask || !activeId) return;
    if (!['pending', 'running', 'approval'].includes(latestTask.status)) return;
    pollTimer.current = setTimeout(async () => {
      try {
        const fresh = await api.getTask(activeId, latestTask.id);
        setLatestTask(fresh);
      } catch {
        // 轮询失败保留当前状态，下次重试
      }
    }, 3000);
    return () => clearTimeout(pollTimer.current);
  }, [latestTask, activeId]);

  if (loading) return <LoadingState rows={6} />;
  if (error) return <ErrorState onRetry={reload} />;

  const teams = data?.teams ?? [];
  const employees = data?.employees ?? [];
  const detail = activeId ? data?.details[activeId] ?? null : null;

  const nameOf = (employeeId: string) =>
    employees.find((employee) => employee.employee_no === employeeId)?.name ?? employeeId;

  const handleSelect = (teamId: string) => {
    setSelectedId(teamId);
    setLatestTask(null);
    setTaskError(undefined);
  };

  const handleCreateTask = async () => {
    const requestText = taskText.trim();
    if (!requestText || !activeId || creating) return;
    setCreating(true);
    setTaskError(undefined);
    try {
      const task = await api.createTask(activeId, requestText);
      setLatestTask(task);
      setTaskText('');
      setActiveTab('tasks');
    } catch (err) {
      setTaskError((err as Error).message);
    } finally {
      setCreating(false);
    }
  };

  const handleApprove = async (approve: boolean) => {
    if (!latestTask || acting) return;
    setActing(true);
    setTaskError(undefined);
    try {
      const task = await api.approveTask(latestTask.id, approve, 'E10281');
      setLatestTask(task);
    } catch (err) {
      setTaskError((err as Error).message);
    } finally {
      setActing(false);
    }
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

  const taskMeta = latestTask ? TASK_STATUS[latestTask.status] ?? TASK_STATUS.pending : null;

  return (
    <div>
      <Typography.Title level={3}>协作团队</Typography.Title>
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
              <Row gutter={[16, 16]} align="middle" style={{ marginBottom: 16 }}>
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
              </Row>

              <div style={{ display: 'flex', gap: 8, marginBottom: 18 }}>
                <Button
                  type={activeTab === 'members' ? 'primary' : 'default'}
                  onClick={() => setActiveTab('members')}
                >
                  团队成员
                </Button>
                <Button type={activeTab === 'tasks' ? 'primary' : 'default'} onClick={() => setActiveTab('tasks')}>
                  任务协作
                </Button>
              </div>

              {activeTab === 'members' && (
                <Table<TeamMember>
                  rowKey="employee_id"
                  size="small"
                  pagination={false}
                  dataSource={detail.members}
                  columns={memberColumns}
                />
              )}

              {activeTab === 'tasks' && (
                <div className="task-panel">
                  {/* 发起任务 */}
                  <div className="task-create">
                    <Input.TextArea
                      value={taskText}
                      onChange={(e) => setTaskText(e.target.value)}
                      placeholder="例如：帮王小明完成入职准备"
                      autoSize={{ minRows: 2, maxRows: 4 }}
                    />
                    <Button type="primary" icon={<RocketOutlined />} loading={creating} onClick={() => void handleCreateTask()}>
                      发起任务
                    </Button>
                  </div>
                  {taskError && <Alert type="error" showIcon message={taskError} style={{ marginBottom: 12 }} />}

                  {/* 任务状态 */}
                  {latestTask ? (
                    <div className="task-detail">
                      <div className="task-head">
                        <div>
                          <Space size={8} wrap>
                            <Typography.Text strong style={{ fontSize: 15 }}>
                              {latestTask.request}
                            </Typography.Text>
                            {taskMeta && (
                              <Tag icon={taskMeta.icon} color={taskMeta.color}>
                                {taskMeta.label}
                              </Tag>
                            )}
                          </Space>
                          <div className="mono" style={{ color: '#66748c', fontSize: 12, marginTop: 4 }}>
                            {latestTask.id} · trace {latestTask.trace_id}
                          </div>
                        </div>
                      </div>

                      {/* 子任务进度 */}
                      <div className="subtask-list">
                        {latestTask.subtasks.map((sub, index) => {
                          const meta = TASK_STATUS[sub.status] ?? TASK_STATUS.pending;
                          return (
                            <div className={`subtask ${sub.status}`} key={`${sub.worker_no}-${index}`}>
                              <div className="subtask-top">
                                <Space size={8}>
                                  <Avatar size={24} style={{ background: avatarColor(sub.worker_id), fontSize: 12 }}>
                                    {nameOf(sub.worker_id).slice(0, 1)}
                                  </Avatar>
                                  <Text strong>{nameOf(sub.worker_id)}</Text>
                                  <Text type="secondary" style={{ fontSize: 12 }}>
                                    {sub.summary}
                                  </Text>
                                </Space>
                                <Tag icon={meta.icon} color={meta.color}>
                                  {meta.label}
                                </Tag>
                              </div>
                              {sub.result && (
                                <Paragraph type="secondary" ellipsis={{ rows: 2 }} style={{ margin: '6px 0 0', fontSize: 12 }}>
                                  {sub.result}
                                </Paragraph>
                              )}
                              {sub.approval && (
                                <Alert
                                  type="warning"
                                  showIcon
                                  style={{ marginTop: 8 }}
                                  message={`敏感操作需审批${sub.approval.policy_id ? `（${sub.approval.policy_id}）` : ''}`}
                                />
                              )}
                            </div>
                          );
                        })}
                      </div>

                      {/* 审批区 */}
                      {latestTask.status === 'approval' && (
                        <div className="approval-zone">
                          <Text strong style={{ marginRight: 12 }}>
                            等待审批：
                          </Text>
                          <Space>
                            <Button type="primary" loading={acting} onClick={() => void handleApprove(true)}>
                              批准
                            </Button>
                            <Button danger loading={acting} onClick={() => void handleApprove(false)}>
                              拒绝
                            </Button>
                          </Space>
                        </div>
                      )}

                      {/* Leader 汇总 */}
                      {latestTask.status === 'completed' && latestTask.summary && (
                        <div className="summary-card">
                          <Text strong>👑 Leader 汇总</Text>
                          <Paragraph style={{ margin: '8px 0 0' }}>{latestTask.summary}</Paragraph>
                        </div>
                      )}
                    </div>
                  ) : (
                    <Card size="small" styles={{ body: { padding: 20 } }}>
                      <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="还没有任务，输入任务描述开始协作" />
                    </Card>
                  )}
                </div>
              )}
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
