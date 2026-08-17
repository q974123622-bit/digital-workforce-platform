import { useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import { Card, Descriptions, Spin, Table, Tag, Typography } from 'antd';
import type { Grant } from '@dwp/shared-schema';
import { api } from '../api/client';
import type { Employee } from '@dwp/shared-schema';

export default function EmployeeDetail() {
  const { employeeNo } = useParams();
  const [emp, setEmp] = useState<Employee | null>(null);

  useEffect(() => {
    if (employeeNo) api.getEmployee(employeeNo).then(setEmp);
  }, [employeeNo]);

  if (!emp) return <Spin />;

  return (
    <div>
      <Typography.Title level={3}>
        {emp.name}（{emp.employee_no}）
      </Typography.Title>
      <Card title="身份信息" style={{ marginBottom: 16 }}>
        <Descriptions column={2} size="small">
          <Descriptions.Item label="类型">{emp.type}</Descriptions.Item>
          <Descriptions.Item label="来源真人">{emp.source_human_no ?? '—'}</Descriptions.Item>
          <Descriptions.Item label="Owner">{emp.owner_human_no}</Descriptions.Item>
          <Descriptions.Item label="部门">{emp.department}</Descriptions.Item>
          <Descriptions.Item label="状态">{emp.status}</Descriptions.Item>
          <Descriptions.Item label="岗位说明">{emp.role_prompt || '—'}</Descriptions.Item>
        </Descriptions>
      </Card>
      <Card title="Runtime 与 Sandbox" style={{ marginBottom: 16 }}>
        <Descriptions column={2} size="small">
          <Descriptions.Item label="Runtime">
            {emp.runtime_type}
            {emp.runtime_ref ? ` / ${emp.runtime_ref}` : ''}
          </Descriptions.Item>
          <Descriptions.Item label="运行位置">{emp.location}</Descriptions.Item>
          <Descriptions.Item label="互联网">{emp.internet}</Descriptions.Item>
          <Descriptions.Item label="最高数据等级">{emp.max_data_level}</Descriptions.Item>
          <Descriptions.Item label="数据域">{emp.allowed_domains.join(', ') || '—'}</Descriptions.Item>
        </Descriptions>
      </Card>
      <Card title="插件授权">
        <Table<Grant>
          rowKey="plugin_id"
          size="small"
          pagination={false}
          dataSource={emp.grants}
          columns={[
            { title: '插件', dataIndex: 'name' },
            { title: 'ID', dataIndex: 'plugin_id' },
            { title: '类型', dataIndex: 'type' },
            { title: '动作', dataIndex: 'action' },
            {
              title: '模式',
              dataIndex: 'decision_mode',
              render: (value: string) => (
                <Tag color={value === 'allow' ? 'green' : value === 'deny' ? 'red' : 'orange'}>{value}</Tag>
              ),
            },
            { title: '数据等级', dataIndex: 'data_level' },
          ]}
        />
      </Card>
    </div>
  );
}
