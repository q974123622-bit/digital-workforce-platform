import { ApartmentOutlined, SearchOutlined } from '@ant-design/icons';
import { Empty, Input, Select } from 'antd';
import { useMemo, useState } from 'react';
import type { Employee } from '@dwp/shared-schema';
import AgentAvatar from '../../components/ui/AgentAvatar';

export default function ColleagueList({
  twin,
  employees,
  onOpenChat,
  onOpenProfile,
}: {
  twin: Employee | null;
  employees: Employee[];
  onOpenChat: (employeeNo: string) => void;
  onOpenProfile: (employeeNo: string) => void;
}) {
  const entries = useMemo(() => [...(twin ? [twin] : []), ...employees], [employees, twin]);
  const departments = useMemo(() => [...new Set(employees.map((item) => item.department).filter(Boolean))], [employees]);
  const [keyword, setKeyword] = useState('');
  const [department, setDepartment] = useState('all');
  // 分身段只受搜索词影响：部门筛选面向AI员工，避免筛部门时分身莫名消失。
  const twinVisible = useMemo(() => {
    if (!twin) return false;
    const query = keyword.trim().toLocaleLowerCase();
    return !query || [twin.name, twin.employee_no, twin.department, twin.role_prompt]
      .some((value) => value.toLocaleLowerCase().includes(query));
  }, [keyword, twin]);
  const visibleEmployees = useMemo(() => {
    const query = keyword.trim().toLocaleLowerCase();
    return employees.filter((item) => {
      const departmentMatch = department === 'all' || item.department === department;
      return departmentMatch && (!query || [item.name, item.employee_no, item.department, item.role_prompt]
        .some((value) => value.toLocaleLowerCase().includes(query)));
    });
  }, [department, employees, keyword]);

  return (
    <section className="flex min-h-0 flex-1 flex-col" aria-label="同事目录">
      <div className="space-y-2 border-b border-line-light px-3 pb-3 pt-3">
        <Input allowClear prefix={<SearchOutlined />} value={keyword} onChange={(event) => setKeyword(event.target.value)} placeholder="搜索姓名、工号或职责" />
        <Select
          className="w-full"
          value={department}
          onChange={setDepartment}
          suffixIcon={<ApartmentOutlined />}
          options={[{ value: 'all', label: `全部部门 · ${entries.length}` }, ...departments.map((value) => ({ value, label: value }))]}
        />
      </div>
      <div className="colleague-list min-h-0 flex-1 overflow-y-auto px-3 py-2">
        {!twinVisible && !visibleEmployees.length && <Empty className="mt-12" image={Empty.PRESENTED_IMAGE_SIMPLE} description="没有找到相关同事" />}
        {twin && twinVisible && (
          <section className="list-section list-section--twin" aria-label="我的分身">
            <div className="list-section-head" role="heading" aria-level={3}><span>我的分身</span><b>1</b></div>
            <div className="list-section-body">
              <button
                type="button"
                className="colleague-row group flex w-full items-center gap-3 px-2.5 py-2.5 text-left is-twin"
                onClick={() => onOpenChat(twin.employee_no)}
              >
                <span
                  role="button"
                  tabIndex={0}
                  aria-label={`查看${twin.name}的资料`}
                  className="avatar-button"
                  onClick={(event) => { event.stopPropagation(); onOpenProfile(twin.employee_no); }}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); event.stopPropagation(); onOpenProfile(twin.employee_no); }
                  }}
                >
                  <AgentAvatar employee={twin} size={42} />
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm font-medium text-ink">{twin.name}</span>
                  <span className="block truncate text-xs text-ink-secondary">我的分身 · {twin.employee_no}</span>
                </span>
                <span className={`address-book-presence ${twin.status === 'active' ? 'available' : ''}`} />
              </button>
            </div>
          </section>
        )}
        {!!visibleEmployees.length && (
          <section className="list-section list-section--employees" aria-label="AI员工">
            <div className="list-section-head" role="heading" aria-level={3}><span>AI员工</span><b>{visibleEmployees.length}</b></div>
            <div className="list-section-body">
              {visibleEmployees.map((employee) => (
                <button
                  key={employee.employee_no}
                  type="button"
                  className="colleague-row group flex w-full items-center gap-3 px-2.5 py-2.5 text-left"
                  onClick={() => onOpenChat(employee.employee_no)}
                >
                  <span
                    role="button"
                    tabIndex={0}
                    aria-label={`查看${employee.name}的资料`}
                    className="avatar-button"
                    onClick={(event) => { event.stopPropagation(); onOpenProfile(employee.employee_no); }}
                    onKeyDown={(event) => {
                      if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); event.stopPropagation(); onOpenProfile(employee.employee_no); }
                    }}
                  >
                    <AgentAvatar employee={employee} size={42} />
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-sm font-medium text-ink">{employee.name}</span>
                    <span className="block truncate text-xs text-ink-secondary">{employee.department} · {employee.employee_no}</span>
                  </span>
                  <span className={`address-book-presence ${employee.status === 'active' ? 'available' : ''}`} />
                </button>
              ))}
            </div>
          </section>
        )}
      </div>
    </section>
  );
}
