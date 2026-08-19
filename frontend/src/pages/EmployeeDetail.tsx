import { useCallback } from 'react';
import { Link, useParams } from 'react-router-dom';
import { Avatar, Button, Card, Col, Descriptions, Row, Space, Table, Tabs, Typography } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import type { Employee, Grant } from '@dwp/shared-schema';
import { CommentOutlined } from '@ant-design/icons';
import { api } from '../api/client';
import MemoryList from '../components/MemoryList';
import { DecisionTag, LevelTag, StatusBadge, TypeTag } from '../components/tags';
import { ErrorState, LoadingState } from '../components/PageState';
import { useAsyncData } from '../hooks/useAsyncData';

export default function EmployeeDetail() {
  const { employeeNo } = useParams();
  const fetcher = useCallback(
    () => (employeeNo ? api.getEmployee(employeeNo) : Promise.reject(new Error('缺少员工编号'))),
    [employeeNo],
  );
  const { data: emp, loading, error, reload } = useAsyncData<Employee>(fetcher);

  if (loading) return <LoadingState rows={8} />;
  if (error || !emp) return <ErrorState onRetry={reload} />;

  const grantColumns: ColumnsType<Grant> = [
    { title: '插件', dataIndex: 'name' },
    { title: 'ID', dataIndex: 'plugin_id', render: (value: string) => <span className="mono">{value}</span> },
    { title: '类型', dataIndex: 'type' },
    { title: '动作', dataIndex: 'action' },
    {
      title: '模式',
      dataIndex: 'decision_mode',
      render: (value: string) => <DecisionTag value={value} />,
    },
    { title: '数据等级', dataIndex: 'data_level', render: (value: string) => <LevelTag value={value} /> },
  ];

  return (
    <div>
      <Card style={{ marginBottom: 16 }} styles={{ body: { padding: 24 } }}>
        <Row gutter={[24, 16]} align="middle">
          <Col flex="auto">
            <Space size={16} align="center">
              <Avatar size={64} style={{ background: '#2f54eb', fontSize: 26, flexShrink: 0 }}>
                {emp.name.slice(0, 1)}
              </Avatar>
              <div>
                <Space size={10} wrap>
                  <Typography.Title level={3} style={{ margin: 0 }}>
                    {emp.name}
                  </Typography.Title>
                  <TypeTag value={emp.type} />
                  <StatusBadge value={emp.status} />
                </Space>
                <Link to={`/employees/${emp.employee_no}/chat`}>
                  <Button type="primary" icon={<CommentOutlined />} style={{ marginTop: 10 }}>
                    进入工作台 · 对话
                  </Button>
                </Link>
                <div style={{ marginTop: 8, color: '#5c6b83' }}>
                  <span className="mono">{emp.employee_no}</span> · {emp.department}
                </div>
              </div>
            </Space>
          </Col>
          <Col>
            <Space size={36} wrap align="center">
              <div>
                <Typography.Text type="secondary" style={{ fontSize: 12, display: 'block' }}>
                  Owner
                </Typography.Text>
                <span className="mono">{emp.owner_human_no}</span>
              </div>
              <div>
                <Typography.Text type="secondary" style={{ fontSize: 12, display: 'block' }}>
                  Runtime
                </Typography.Text>
                <Typography.Text strong>{emp.runtime_type}</Typography.Text>
              </div>
              <div>
                <Typography.Text type="secondary" style={{ fontSize: 12, display: 'block' }}>
                  最高数据等级
                </Typography.Text>
                <LevelTag value={emp.max_data_level} />
              </div>
              <div>
                <Typography.Text type="secondary" style={{ fontSize: 12, display: 'block' }}>
                  来源真人
                </Typography.Text>
                <Typography.Text strong>{emp.source_human_no ?? '—'}</Typography.Text>
              </div>
              <Link to={`/employees/${emp.employee_no}/chat`}>
                <Button type="primary">开始对话</Button>
              </Link>
            </Space>
          </Col>
        </Row>
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
                  <Descriptions.Item label="来源真人">{emp.source_human_no ?? '—'}</Descriptions.Item>
                  <Descriptions.Item label="Owner">{emp.owner_human_no}</Descriptions.Item>
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
            key: 'grants',
            label: `插件授权（${emp.grants.length}）`,
            children: (
              <Card>
                <Table<Grant>
                  rowKey="plugin_id"
                  size="small"
                  pagination={false}
                  dataSource={emp.grants}
                  columns={grantColumns}
                />
              </Card>
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
