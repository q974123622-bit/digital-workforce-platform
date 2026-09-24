import { useCallback, useEffect, useMemo, useState } from 'react';
import { Avatar, Button, Empty, Input, Select, Space, Table, Typography } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { Link, useSearchParams } from 'react-router-dom';
import type { Employee } from '@dwp/shared-schema';
import { api } from '../api/client';
import { EMPLOYEE_TYPE_LABEL, EmploymentTag, LevelTag, StatusBadge, TypeTag } from '../components/tags';
import { ErrorState } from '../components/PageState';
import { useAsyncData } from '../hooks/useAsyncData';

const AVATAR_COLORS = ['var(--dwp-brand)', 'var(--dwp-success)', 'var(--dwp-warning)', 'var(--dwp-accent)', 'var(--dwp-text-secondary)'];

const avatarColor = (value: string) => {
  const sum = [...value].reduce((acc, char) => acc + char.charCodeAt(0), 0);
  return AVATAR_COLORS[sum % AVATAR_COLORS.length];
};

export default function Employees() {
  const [params, setParams] = useSearchParams();
  const [type, setType] = useState<string>();
  const [status, setStatus] = useState<string>();
  const [keyword, setKeyword] = useState(params.get('keyword') ?? '');

  const fetcher = useCallback(() => api.listEmployees({ type }), [type]);
  const { data: employees, loading, error, reload } = useAsyncData<Employee[]>(fetcher);

  // 顶栏全局搜索跳转过来时，同步关键字
  useEffect(() => {
    setKeyword(params.get('keyword') ?? '');
  }, [params]);

  const filtered = useMemo(() => {
    const rows = employees ?? [];
    const kw = keyword.trim().toLowerCase();
    return rows.filter((employee) => {
      const matchKeyword =
        !kw ||
        [employee.name, employee.employee_no, employee.department, EMPLOYEE_TYPE_LABEL[employee.type]].some((value) =>
          (value ?? '').toLowerCase().includes(kw),
        );
      const matchStatus = !status || employee.status === status;
      return matchKeyword && matchStatus;
    });
  }, [employees, keyword, status]);

  const columns: ColumnsType<Employee> = useMemo(
    () => [
      {
        title: '成员',
        key: 'member',
        render: (_: unknown, record: Employee) => (
          <Space size={10}>
            <Avatar size={30} style={{ background: avatarColor(record.employee_no), flexShrink: 0 }}>
              {record.name.slice(0, 1)}
            </Avatar>
            <Link to={`/employees/${record.employee_no}`}>
              <Typography.Text strong>{record.name}</Typography.Text>
            </Link>
          </Space>
        ),
      },
      { title: '工号', dataIndex: 'employee_no', render: (value: string) => <span className="mono">{value}</span> },
      { title: '类型', dataIndex: 'type', render: (value: string) => <TypeTag value={value} /> },
      { title: '身份', dataIndex: 'employment_type', render: (value: string) => <EmploymentTag value={value} /> },
      { title: '部门', dataIndex: 'department' },
      { title: 'Owner', dataIndex: 'owner_human_no', render: (value: string) => <span className="mono">{value}</span> },
      { title: '数据等级', dataIndex: 'max_data_level', render: (value: string) => <LevelTag value={value} /> },
      { title: '状态', dataIndex: 'status', render: (value: string) => <StatusBadge value={value} /> },
      {
        title: '操作',
        key: 'actions',
        render: (_: unknown, record: Employee) => (
          <Link to={`/employees/${record.employee_no}`}>
            <Button type="link" size="small">
              查看详情
            </Button>
          </Link>
        ),
      },
    ],
    [],
  );

  const twinRows = useMemo(() => filtered.filter((employee) => employee.type === 'twin'), [filtered]);
  const employeeRows = useMemo(() => filtered.filter((employee) => employee.type !== 'twin'), [filtered]);
  const showTwinSection = type === undefined || type === 'twin';
  const showEmployeeSection = type === undefined || type !== 'twin';

  const renderTable = (rows: Employee[]) => (
    <Table rowKey="employee_no" loading={loading} columns={columns} dataSource={rows} pagination={false} />
  );

  if (error) return <ErrorState onRetry={reload} />;

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, marginBottom: 16, flexWrap: 'wrap' }}>
        <Typography.Title level={3} style={{ margin: 0 }}>
          AI员工
        </Typography.Title>
        <Space wrap>
          <Input.Search
            allowClear
            aria-label="搜索员工"
            placeholder="搜索名称 / 工号 / 部门"
            style={{ width: 220 }}
            value={keyword}
            onChange={(event) => setKeyword(event.target.value)}
            onSearch={(value) => setParams(value.trim() ? { keyword: value.trim() } : {}, { replace: true })}
          />
          <Select
            allowClear
            aria-label="按状态筛选"
            placeholder="按状态筛选"
            style={{ width: 120 }}
            value={status}
            onChange={setStatus}
            options={[
              { value: 'active', label: '启用' },
              { value: 'inactive', label: '停用' },
              { value: 'disabled', label: '禁用' },
            ]}
          />
          <Select
            allowClear
            aria-label="按类型筛选"
            placeholder="按类型筛选"
            style={{ width: 130 }}
            value={type}
            onChange={setType}
            options={[
              { value: 'twin', label: EMPLOYEE_TYPE_LABEL.twin },
              { value: 'virtual', label: EMPLOYEE_TYPE_LABEL.virtual },
              { value: 'rpa', label: EMPLOYEE_TYPE_LABEL.rpa },
            ]}
          />
          <Typography.Text type="secondary" className="tabular-nums">
            共 {filtered.length} 名
          </Typography.Text>
        </Space>
      </div>
      {filtered.length ? (
        <>
          {showTwinSection && !!twinRows.length && (
            <section className="list-section list-section--twin" aria-label="我的分身">
              <div className="list-section-head" role="heading" aria-level={3}><span>我的分身</span><b>{twinRows.length}</b></div>
              <div className="list-section-body">{renderTable(twinRows)}</div>
            </section>
          )}
          {showEmployeeSection && !!employeeRows.length && (
            <section className="list-section list-section--employees" aria-label="AI员工">
              <div className="list-section-head" role="heading" aria-level={3}><span>AI员工</span><b>{employeeRows.length}</b></div>
              <div className="list-section-body">{renderTable(employeeRows)}</div>
            </section>
          )}
        </>
      ) : (
        <section className="list-section list-section--employees">
          <div className="list-section-body" style={{ padding: '26px 0' }}>
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="没有找到相关员工" />
          </div>
        </section>
      )}
    </div>
  );
}
