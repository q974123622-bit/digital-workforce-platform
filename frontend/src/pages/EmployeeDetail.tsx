import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { Avatar, Card, Descriptions, Empty, Input, Space, Table, Tabs, Tag, Typography } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import type { Employee, Grant, Workflow } from '@dwp/shared-schema';
import { CommentOutlined } from '@ant-design/icons';
import { api } from '../api/client';
import MemoryList from '../components/MemoryList';
import { DecisionTag, EmploymentTag, LevelTag, PluginTypeTag, StatusBadge, TypeTag } from '../components/tags';
import { ErrorState, LoadingState } from '../components/PageState';
import { useAsyncData } from '../hooks/useAsyncData';

export default function EmployeeDetail() {
  const { employeeNo } = useParams();
  const fetcher = useCallback(
    () => (employeeNo ? api.getEmployee(employeeNo) : Promise.reject(new Error('缺少员工编号'))),
    [employeeNo],
  );
  const { data: emp, loading, error, reload } = useAsyncData<Employee>(fetcher);
  const [workflows, setWorkflows] = useState<Workflow[]>([]);
  const [workflowsLoading, setWorkflowsLoading] = useState(true);
  const [capabilityKeyword, setCapabilityKeyword] = useState('');

  useEffect(() => {
    let cancelled = false;
    setWorkflowsLoading(true);
    api.listWorkflows()
      .then((rows) => {
        if (!cancelled) setWorkflows(rows);
      })
      .catch(() => {
        if (!cancelled) setWorkflows([]);
      })
      .finally(() => {
        if (!cancelled) setWorkflowsLoading(false);
      });
    return () => { cancelled = true; };
  }, []);

  const employeeWorkflows = useMemo(() => {
    if (!employeeNo) return [];
    return workflows.filter((workflow) =>
      workflow.owner_employee?.employee_no === employeeNo ||
      workflow.authorized_employees.some((item) => item.employee_no === employeeNo),
    );
  }, [employeeNo, workflows]);

  const availableGrants = useMemo(
    () => (emp?.grants ?? []).filter((grant) => grant.decision_mode !== 'deny'),
    [emp],
  );

  const visibleGrants = useMemo(() => {
    const keyword = capabilityKeyword.trim().toLowerCase();
    if (!keyword) return availableGrants;
    return availableGrants.filter((grant) =>
      [grant.name, grant.plugin_id, grant.type, grant.action, grant.data_level]
        .some((value) => value.toLowerCase().includes(keyword)),
    );
  }, [availableGrants, capabilityKeyword]);

  const visibleWorkflows = useMemo(() => {
    const keyword = capabilityKeyword.trim().toLowerCase();
    if (!keyword) return employeeWorkflows;
    return employeeWorkflows.filter((workflow) =>
      [workflow.name, workflow.plugin_id, workflow.description, ...workflow.steps]
        .some((value) => value.toLowerCase().includes(keyword)),
    );
  }, [capabilityKeyword, employeeWorkflows]);

  if (loading) return <LoadingState rows={8} />;
  if (error || !emp) return <ErrorState onRetry={reload} />;

  const grantColumns: ColumnsType<Grant> = [
    { title: '插件', dataIndex: 'name' },
    { title: 'ID', dataIndex: 'plugin_id', render: (value: string) => <span className="mono">{value}</span> },
    { title: '类型', dataIndex: 'type', render: (value: string) => <PluginTypeTag value={value} /> },
    { title: '动作', dataIndex: 'action' },
    {
      title: '模式',
      dataIndex: 'decision_mode',
      render: (value: string) => <DecisionTag value={value} />,
    },
    { title: '数据等级', dataIndex: 'data_level', render: (value: string) => <LevelTag value={value} /> },
  ];

  const workflowColumns: ColumnsType<Workflow> = [
    {
      title: '工作流',
      key: 'workflow',
      render: (_: unknown, workflow: Workflow) => (
        <div>
          <Typography.Text strong>{workflow.name}</Typography.Text>
          <div><Typography.Text type="secondary" className="mono">{workflow.plugin_id}</Typography.Text></div>
        </div>
      ),
    },
    { title: '类型', dataIndex: 'type', width: 120, render: (value: string) => <PluginTypeTag value={value} /> },
    { title: '说明', dataIndex: 'description' },
    {
      title: '执行步骤',
      dataIndex: 'steps',
      render: (steps: string[]) => steps.length ? steps.join(' → ') : '—',
    },
    { title: '数据等级', dataIndex: 'data_level', width: 110, render: (value: string) => <LevelTag value={value} /> },
  ];

  return (
    <div>
      <Card className="employee-hero" style={{ marginBottom: 16 }}>
        <div className="employee-hero-layout">
          <div className="employee-profile-main">
            <Avatar className="employee-profile-avatar" size={72}>
              {emp.name.slice(0, 1)}
            </Avatar>
            <div className="employee-profile-info">
              <Typography.Title level={3} className="employee-profile-name">
                {emp.name}
              </Typography.Title>
              <div className="employee-profile-meta">
                <span className="mono">{emp.employee_no}</span>
                <span className="employee-meta-dot">·</span>
                <span>{emp.department}</span>
              </div>
              <Space size={6} wrap className="employee-profile-tags">
                <TypeTag value={emp.type} />
                <EmploymentTag value={emp.employment_type} />
                <StatusBadge value={emp.status} />
              </Space>
              <Link to={`/employees/${emp.employee_no}/chat`} className="employee-workspace-link">
                <CommentOutlined />
                <span>进入工作台</span>
              </Link>
            </div>
          </div>

          <div className="employee-metrics">
            <div className="employee-metric">
              <span className="employee-metric-label">负责人</span>
              <strong>{emp.owner_name || emp.owner_human_no}</strong>
              <span className="employee-metric-note mono">{emp.owner_human_no}</span>
            </div>
            <div className="employee-metric">
              <span className="employee-metric-label">Runtime</span>
              <strong>{emp.runtime_type}</strong>
              <span className="employee-metric-note">执行环境</span>
            </div>
            <div className="employee-metric">
              <span className="employee-metric-label">最高数据等级</span>
              <div><LevelTag value={emp.max_data_level} /></div>
              <span className="employee-metric-note">权限上限</span>
            </div>
            <div className="employee-metric">
              <span className="employee-metric-label">来源真人</span>
              <strong className="mono">{emp.source_human_no ?? '—'}</strong>
              <span className="employee-metric-note">身份映射</span>
            </div>
          </div>
        </div>
      </Card>

      <Tabs
        items={[
          {
            key: 'identity',
            label: '身份信息',
            children: (
              <Card>
                <Descriptions column={{ xs: 1, sm: 2 }} size="small">
                  <Descriptions.Item label="类型">{emp.type}</Descriptions.Item>
                  <Descriptions.Item label="用工身份"><EmploymentTag value={emp.employment_type} /></Descriptions.Item>
                  <Descriptions.Item label="数字员工工号"><span className="mono">{emp.employee_no}</span></Descriptions.Item>
                  <Descriptions.Item label="来源真人">{emp.source_human_no ?? '—'}</Descriptions.Item>
                  <Descriptions.Item label="负责人">
                    {emp.owner_name || emp.owner_human_no}（<span className="mono">{emp.owner_human_no}</span>）
                  </Descriptions.Item>
                  <Descriptions.Item label="部门">{emp.department}</Descriptions.Item>
                  <Descriptions.Item label="状态">{emp.status}</Descriptions.Item>
                  <Descriptions.Item label="岗位说明">{emp.role_prompt || '—'}</Descriptions.Item>
                </Descriptions>
              </Card>
            ),
          },
          {
            key: 'runtime',
            label: 'Runtime 与沙箱',
            children: (
              <Card>
                <Descriptions column={{ xs: 1, sm: 2 }} size="small">
                  <Descriptions.Item label="Runtime">
                    {emp.runtime_type}
                    {emp.runtime_ref ? ` / ${emp.runtime_ref}` : ''}
                  </Descriptions.Item>
                  <Descriptions.Item label="运行位置">{emp.location}</Descriptions.Item>
                  <Descriptions.Item label="互联网">{emp.internet}</Descriptions.Item>
                  <Descriptions.Item label="最高数据等级">{emp.max_data_level}</Descriptions.Item>
                  <Descriptions.Item label="数据域" span={2}>
                    {emp.allowed_domains.join(', ') || '—'}
                  </Descriptions.Item>
                </Descriptions>
              </Card>
            ),
          },
          {
            key: 'capabilities',
            label: `功能与工作流（${availableGrants.length + employeeWorkflows.length}）`,
            children: (
              <Space direction="vertical" size={16} style={{ width: '100%' }}>
                <Card>
                  <Input.Search
                    allowClear
                    aria-label="检索功能与工作流"
                    placeholder="检索功能、插件、工作流或执行步骤"
                    value={capabilityKeyword}
                    onChange={(event) => setCapabilityKeyword(event.target.value)}
                    style={{ maxWidth: 420 }}
                  />
                </Card>
                <Card title={<Space><span>具备的功能</span><Tag>{visibleGrants.length}</Tag></Space>}>
                  {visibleGrants.length ? (
                    <Table<Grant>
                      rowKey={(record) => `${record.plugin_id}-${record.action}`}
                      size="small"
                      pagination={false}
                      dataSource={visibleGrants}
                      columns={grantColumns}
                      scroll={{ x: 760 }}
                    />
                  ) : <Empty description="没有匹配的功能" />}
                </Card>
                <Card title={<Space><span>可执行的工作流</span><Tag>{visibleWorkflows.length}</Tag></Space>}>
                  <Table<Workflow>
                    rowKey="plugin_id"
                    size="small"
                    loading={workflowsLoading}
                    pagination={false}
                    dataSource={visibleWorkflows}
                    columns={workflowColumns}
                    locale={{ emptyText: <Empty description="没有匹配的工作流" /> }}
                    scroll={{ x: 900 }}
                  />
                </Card>
              </Space>
            ),
          },
          {
            key: 'sessions',
            label: '会话记录',
            children: (
              <Card>
                <MemoryList employeeNo={emp.employee_no} />
              </Card>
            ),
          },
        ]}
      />
    </div>
  );
}
