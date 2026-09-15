import {
  ArrowLeftOutlined,
  ApartmentOutlined,
  IdcardOutlined,
  SearchOutlined,
  TeamOutlined,
} from '@ant-design/icons';
import { Button, Empty, Input } from 'antd';
import { useEffect, useMemo, useState } from 'react';
import type { Employee } from '@dwp/shared-schema';
import AgentProfile from './AgentProfile';
import AgentAvatar from '../../components/ui/AgentAvatar';

type DirectoryFilter = 'all' | 'twin' | `department:${string}`;
type MobilePane = 'departments' | 'people' | 'detail';

export interface DirectoryEntry {
  employeeNo: string;
  name: string;
  department: string;
  roleSummary: string;
  ownerName: string;
  kind: 'twin' | 'digital_employee' | 'automation';
  status: string;
  source: Employee;
  departmentId?: string;
  avatarUrl?: string;
  jobTitle?: string;
  onlineState?: string;
}

function normalizeEmployee(employee: Employee): DirectoryEntry {
  return {
    employeeNo: employee.employee_no,
    name: employee.name,
    department: employee.department || '未设置部门',
    roleSummary: employee.role_prompt || '尚未配置职责与工作范围',
    ownerName: employee.owner_name || employee.owner_human_no,
    kind: employee.type === 'twin' ? 'twin' : employee.type === 'rpa' ? 'automation' : 'digital_employee',
    status: employee.status,
    source: employee,
    avatarUrl: employee.avatar_url ?? undefined,
  };
}

const KIND_LABEL: Record<DirectoryEntry['kind'], string> = {
  twin: '我的数字分身',
  digital_employee: '数字员工',
  automation: '自动化助手',
};

function DirectoryAvatar({ entry, size = 42 }: { entry: DirectoryEntry; size?: number }) {
  return <AgentAvatar employee={entry.source} size={size} className={`directory-avatar ${entry.kind}`} />;
}

export default function Directory({
  twin,
  employees,
  onOpenChat,
}: {
  twin: Employee | null;
  employees: Employee[];
  onOpenChat: (employeeNo: string) => void;
  onOpenProfile?: (employeeNo: string) => void;
}) {
  const entries = useMemo(
    () => [...(twin ? [normalizeEmployee(twin)] : []), ...employees.map(normalizeEmployee)],
    [employees, twin],
  );
  const [keyword, setKeyword] = useState('');
  const [filter, setFilter] = useState<DirectoryFilter>('all');
  const [selectedNo, setSelectedNo] = useState<string | null>(twin?.employee_no ?? employees[0]?.employee_no ?? null);
  const [mobilePane, setMobilePane] = useState<MobilePane>('departments');

  const departments = useMemo(() => {
    const counts = new Map<string, number>();
    entries.filter((entry) => entry.kind !== 'twin').forEach((entry) => counts.set(entry.department, (counts.get(entry.department) ?? 0) + 1));
    return [...counts.entries()].sort(([left], [right]) => left.localeCompare(right, 'zh-CN'));
  }, [entries]);

  const visibleEntries = useMemo(() => entries.filter((entry) => {
    const filterMatch = filter === 'all'
      || (filter === 'twin' && entry.kind === 'twin')
      || (filter.startsWith('department:') && entry.kind !== 'twin' && entry.department === filter.slice('department:'.length));
    const search = keyword.trim().toLocaleLowerCase();
    const searchMatch = !search || [entry.name, entry.employeeNo, entry.department, entry.ownerName, entry.roleSummary]
      .some((value) => value.toLocaleLowerCase().includes(search));
    return filterMatch && searchMatch;
  }), [entries, filter, keyword]);

  useEffect(() => {
    if (!selectedNo || !entries.some((entry) => entry.employeeNo === selectedNo)) {
      setSelectedNo(entries[0]?.employeeNo ?? null);
    }
  }, [entries, selectedNo]);

  useEffect(() => {
    if (!visibleEntries.some((entry) => entry.employeeNo === selectedNo)) {
      setSelectedNo(visibleEntries[0]?.employeeNo ?? null);
    }
  }, [selectedNo, visibleEntries]);

  const selected = visibleEntries.find((entry) => entry.employeeNo === selectedNo) ?? visibleEntries[0] ?? null;
  const chooseFilter = (next: DirectoryFilter) => {
    setFilter(next);
    setMobilePane('people');
  };
  const choosePerson = (employeeNo: string) => {
    setSelectedNo(employeeNo);
    setMobilePane('detail');
  };

  return (
    <div className="address-book" data-mobile-pane={mobilePane}>
      <aside className="address-book-departments" aria-label="通讯录分组">
        <div className="address-book-panel-head">
          <div>
            <div className="address-book-eyebrow">组织通讯录</div>
            <strong>同事与数字搭档</strong>
          </div>
          <span>{entries.length}</span>
        </div>
        <nav className="address-book-tree">
          <button type="button" className={filter === 'all' ? 'active' : ''} onClick={() => chooseFilter('all')}>
            <TeamOutlined /><span>全部同事</span><b>{entries.length}</b>
          </button>
          {twin && (
            <button type="button" className={filter === 'twin' ? 'active' : ''} onClick={() => chooseFilter('twin')}>
              <IdcardOutlined /><span>我的数字分身</span><b>1</b>
            </button>
          )}
          <div className="address-book-tree-label">部门</div>
          {departments.map(([department, count]) => {
            const key: DirectoryFilter = `department:${department}`;
            return (
              <button type="button" key={department} className={filter === key ? 'active' : ''} onClick={() => chooseFilter(key)}>
                <ApartmentOutlined /><span>{department}</span><b>{count}</b>
              </button>
            );
          })}
        </nav>
      </aside>

      <section className="address-book-people" aria-label="同事名单">
        <div className="address-book-list-head">
          <Button className="address-book-mobile-back" type="text" icon={<ArrowLeftOutlined />} onClick={() => setMobilePane('departments')}>分组</Button>
          <div className="flex items-end justify-between gap-3">
            <div><strong>{filter === 'all' ? '全部同事' : filter === 'twin' ? '我的数字分身' : filter.slice('department:'.length)}</strong><span>{visibleEntries.length} 位</span></div>
          </div>
          <Input
            allowClear
            prefix={<SearchOutlined />}
            placeholder="搜索姓名、工号、部门或职责"
            value={keyword}
            onChange={(event) => setKeyword(event.target.value)}
          />
        </div>
        <div className="address-book-list" role="listbox" aria-label="同事名单">
          {!visibleEntries.length && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="没有找到相关同事" />}
          {visibleEntries.map((entry) => (
            <button
              type="button"
              role="option"
              aria-selected={selectedNo === entry.employeeNo}
              key={entry.employeeNo}
              className={`address-book-person ${selectedNo === entry.employeeNo ? 'selected' : ''}`}
              onClick={() => choosePerson(entry.employeeNo)}
            >
              <DirectoryAvatar entry={entry} />
              <span className="min-w-0 flex-1">
                <span className="address-book-person-name">{entry.name}</span>
                <span className="address-book-person-meta">{entry.department} · {entry.employeeNo}</span>
              </span>
              <span className={`address-book-presence ${entry.status === 'active' ? 'available' : ''}`} aria-label={entry.status === 'active' ? '可联系' : '暂不可用'} />
            </button>
          ))}
        </div>
      </section>

      <article className="address-book-detail" aria-label="同事资料">
        <Button className="address-book-mobile-back" type="text" icon={<ArrowLeftOutlined />} onClick={() => setMobilePane('people')}>返回名单</Button>
        {selected ? (
          <>
            <div className="address-book-detail-kicker">{KIND_LABEL[selected.kind]} · {selected.department}</div>
            <AgentProfile employee={selected.source} onOpenChat={onOpenChat} />
          </>
        ) : (
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="从名单中选择一位同事" />
        )}
      </article>
    </div>
  );
}
