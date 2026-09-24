import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { Employee } from '@dwp/shared-schema';
import Directory from './Directory';

const employee = (overrides: Partial<Employee>): Employee => ({
  id: '', employee_no: '', name: '', type: 'virtual', employment_type: 'formal', source_human_no: null,
  owner_human_no: '', department: '', role_prompt: '', status: 'active', runtime_type: 'container', runtime_ref: null,
  location: 'local', internet: 'deny', max_data_level: 'L1', allowed_domains: [], grants: [],
  ...overrides,
});

const twin = employee({
  id: 'DT-E10281', employee_no: 'DT-E10281', name: '张三的数字分身', type: 'twin',
  department: '架构部', role_prompt: '沉淀张三的项目经验', owner_human_no: 'E10281', owner_name: '张三',
  source_human_no: 'E10281', status: 'active',
});

const employees: Employee[] = [
  employee({
    id: 'AI-GENERAL', employee_no: 'AI-GENERAL', name: 'AI员工平台', type: 'virtual',
    department: '企业服务部', role_prompt: '回答内规与 IT 服务问题', owner_human_no: 'E10281', owner_name: '平台团队',
    source_human_no: null, status: 'active',
  }),
  employee({
    id: 'AI-INVESTMENT', employee_no: 'AI-INVESTMENT', name: '投资分析AI员工', type: 'virtual',
    department: '研究发展部', role_prompt: '支持证券与投行业务研究', owner_human_no: 'E10281', owner_name: '投研团队',
    source_human_no: null, status: 'active',
  }),
];

describe('Directory', () => {
  it('按部门筛选、选择同事并从固定资料区发起消息', async () => {
    const openChat = vi.fn();
    render(<Directory twin={twin} employees={employees} onOpenChat={openChat} />);

    expect(screen.getByRole('button', { name: /我的分身/ })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /企业服务部/ }));
    await waitFor(() => expect(screen.getByRole('option', { name: /AI员工平台/ })).toBeInTheDocument());
    expect(screen.queryByRole('option', { name: /投资分析AI员工/ })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('option', { name: /AI员工平台/ }));
    expect(screen.getByRole('article', { name: '同事资料' })).toHaveTextContent('回答内规与 IT 服务问题');
    fireEvent.click(screen.getByRole('button', { name: /发消息/ }));
    expect(openChat).toHaveBeenCalledWith('AI-GENERAL');
  });

  it('搜索覆盖姓名、工号、部门和职责', () => {
    render(<Directory twin={twin} employees={employees} onOpenChat={vi.fn()} />);
    fireEvent.change(screen.getByPlaceholderText('搜索姓名、工号、部门或职责'), { target: { value: '投行业务' } });
    expect(screen.getByRole('option', { name: /投资分析AI员工/ })).toBeInTheDocument();
    expect(screen.queryByRole('option', { name: /AI员工平台/ })).not.toBeInTheDocument();
  });

  it('名单栏按「上：我的分身 / 下：AI员工」分区展示', () => {
    render(<Directory twin={twin} employees={employees} onOpenChat={vi.fn()} />);

    const twinSection = screen.getByRole('listbox', { name: '我的分身名单' });
    expect(within(twinSection).getByRole('option', { name: /张三的数字分身/ })).toBeInTheDocument();

    const employeeSection = screen.getByRole('listbox', { name: 'AI员工名单' });
    expect(within(employeeSection).getByRole('option', { name: /AI员工平台/ })).toBeInTheDocument();
    expect(within(employeeSection).queryByRole('option', { name: /张三的数字分身/ })).not.toBeInTheDocument();

    expect(screen.getByRole('heading', { name: /我的分身/ })).toHaveTextContent('1');
    expect(screen.getByRole('heading', { name: /AI员工/ })).toHaveTextContent('2');
  });

  it('搜索命中AI员工时分身段随关键词隐藏', () => {
    render(<Directory twin={twin} employees={employees} onOpenChat={vi.fn()} />);
    fireEvent.change(screen.getByPlaceholderText('搜索姓名、工号、部门或职责'), { target: { value: '投行业务' } });

    expect(screen.getByRole('listbox', { name: 'AI员工名单' })).toBeInTheDocument();
    expect(screen.queryByRole('listbox', { name: '我的分身名单' })).not.toBeInTheDocument();
  });
});
