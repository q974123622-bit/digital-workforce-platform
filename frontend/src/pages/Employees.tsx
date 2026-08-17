import { useEffect, useMemo, useState } from 'react';
import { Select, Space, Table, Tag, Typography } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { Link } from 'react-router-dom';
import type { Employee } from '@dwp/shared-schema';
import { api } from '../api/client';

const typeLabel: Record<string, string> = { twin: '数字分身', virtual: '虚拟员工', rpa: 'RPA' };

export default function Employees() {
  const [employees, setEmployees] = useState<Employee[]>([]);
  const [type, setType] = useState<string>();
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    api
      .listEmployees({ type })
      .then(setEmployees)
      .finally(() => setLoading(false));
  }, [type]);

  const columns: ColumnsType<Employee> = useMemo(
    () => [
      { title: '工号', dataIndex: 'employee_no' },
      {
        title: '名称',
        dataIndex: 'name',
        render: (name: string, record: Employee) => <Link to={`/employees/${record.employee_no}`}>{name}</Link>,
      },
      {
        title: '类型',
        dataIndex: 'type',
        render: (value: string) => (
          <Tag color={value === 'twin' ? 'blue' : value === 'virtual' ? 'green' : 'orange'}>
            {typeLabel[value] ?? value}
          </Tag>
        ),
      },
      { title: '部门', dataIndex: 'department' },
      { title: 'Owner', dataIndex: 'owner_human_no' },
      { title: 'Runtime', dataIndex: 'runtime_type' },
      { title: '数据等级', dataIndex: 'max_data_level' },
      { title: 'Sandbox', render: (_: unknown, record: Employee) => `${record.location} / Internet ${record.internet}` },
      { title: '状态', dataIndex: 'status' },
    ],
    [],
  );

  return (
    <div>
      <Space style={{ marginBottom: 16 }}>
        <Typography.Title level={3} style={{ margin: 0 }}>
          数字员工
        </Typography.Title>
        <Select
          allowClear
          placeholder="按类型筛选"
          style={{ width: 160 }}
          value={type}
          onChange={setType}
          options={[
            { value: 'twin', label: '数字分身' },
            { value: 'virtual', label: '虚拟员工' },
            { value: 'rpa', label: 'RPA' },
          ]}
        />
      </Space>
      <Table rowKey="employee_no" loading={loading} columns={columns} dataSource={employees} pagination={false} />
    </div>
  );
}
