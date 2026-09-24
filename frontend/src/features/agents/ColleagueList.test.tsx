import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { Employee } from '@dwp/shared-schema';
import ColleagueList from './ColleagueList';

const employee = (overrides: Partial<Employee>): Employee => ({
  id: '', employee_no: '', name: '', type: 'virtual', employment_type: 'formal', source_human_no: null,
  owner_human_no: '', department: '', role_prompt: '', status: 'active', runtime_type: 'container', runtime_ref: null,
  location: 'local', internet: 'deny', max_data_level: 'L1', allowed_domains: [], grants: [],
  ...overrides,
});

const twin = employee({
  id: 'DT-E10281', employee_no: 'DT-E10281', name: '张三的数字分身', type: 'twin',
  department: '架构部', role_prompt: '沉淀张三的项目经验',
});

const employees: Employee[] = [
  employee({
    id: 'AI-GENERAL', employee_no: 'AI-GENERAL', name: 'AI员工平台', type: 'virtual',
    department: '企业服务部', role_prompt: '回答内规与 IT 服务问题',
  }),
  employee({
    id: 'AI-INVESTMENT', employee_no: 'AI-INVESTMENT', name: '投资分析AI员工', type: 'virtual',
    department: '研究发展部', role_prompt: '支持证券与投行业务研究',
  }),
];

const renderList = () =>
  render(<ColleagueList twin={twin} employees={employees} onOpenChat={vi.fn()} onOpenProfile={vi.fn()} />);

describe('ColleagueList', () => {
  it('分身与AI员工分为上下两段并各自计数', () => {
    renderList();

    const twinSection = screen.getByRole('region', { name: '我的分身' });
    expect(within(twinSection).getByText('张三的数字分身')).toBeInTheDocument();
    expect(within(twinSection).queryByText('AI员工平台')).not.toBeInTheDocument();

    const employeeSection = screen.getByRole('region', { name: 'AI员工' });
    expect(within(employeeSection).getByText('AI员工平台')).toBeInTheDocument();
    expect(within(employeeSection).getByText('投资分析AI员工')).toBeInTheDocument();
    expect(within(employeeSection).queryByText('张三的数字分身')).not.toBeInTheDocument();

    expect(screen.getByRole('heading', { name: /我的分身/ })).toHaveTextContent('1');
    expect(screen.getByRole('heading', { name: /AI员工/ })).toHaveTextContent('2');
  });

  it('搜索只收敛AI员工段，分身不匹配时随关键词隐藏', () => {
    renderList();
    fireEvent.change(screen.getByPlaceholderText('搜索姓名、工号或职责'), { target: { value: '投行业务' } });

    expect(screen.getByText('投资分析AI员工')).toBeInTheDocument();
    expect(screen.queryByText('AI员工平台')).not.toBeInTheDocument();
    expect(screen.queryByRole('region', { name: '我的分身' })).not.toBeInTheDocument();
  });

  it('部门筛选只作用于AI员工段，分身段保持展示', async () => {
    renderList();

    fireEvent.mouseDown(screen.getByRole('combobox'));
    fireEvent.click(await screen.findByTitle('企业服务部'));

    await waitFor(() => expect(screen.queryByText('投资分析AI员工')).not.toBeInTheDocument());
    expect(screen.getByText('AI员工平台')).toBeInTheDocument();
    expect(screen.getByText('张三的数字分身')).toBeInTheDocument();
  });
});
