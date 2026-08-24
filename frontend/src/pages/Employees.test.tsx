import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import Employees from './Employees';
import type { Employee } from '@dwp/shared-schema';

const json = (body: unknown) =>
  new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });

const employees: Employee[] = [
  {
    id: '1',
    employee_no: 'DT-E10281',
    name: '张三的数字分身',
    type: 'twin',
    employment_type: 'formal',
    source_human_no: 'E10281',
    owner_human_no: 'E10281',
    department: '架构部',
    role_prompt: '',
    status: 'active',
    runtime_type: 'demo',
    runtime_ref: null,
    location: 'remote',
    internet: 'deny',
    max_data_level: 'L2',
    allowed_domains: [],
    grants: [],
  },
  {
    id: '2',
    employee_no: 'VE-0001',
    name: '新员工入职助手',
    type: 'virtual',
    employment_type: 'formal',
    source_human_no: null,
    owner_human_no: 'E10021',
    department: '人力资源部',
    role_prompt: '',
    status: 'active',
    runtime_type: 'harness',
    runtime_ref: null,
    location: 'remote',
    internet: 'deny',
    max_data_level: 'L2',
    allowed_domains: [],
    grants: [],
  },
];

const renderEmployees = () =>
  render(
    <MemoryRouter initialEntries={['/employees']}>
      <Employees />
    </MemoryRouter>,
  );

describe('Employees', () => {
  beforeEach(() => {
    vi.stubGlobal(
      'fetch',
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes('/api/v1/employees')) return Promise.resolve(json(employees));
        return Promise.resolve(json({ status: 'ok' }));
      }),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('渲染员工列表与计数', async () => {
    renderEmployees();

    expect(await screen.findByText('张三的数字分身')).toBeInTheDocument();
    expect(screen.getByText('新员工入职助手')).toBeInTheDocument();
    expect(screen.getByText('共 2 名')).toBeInTheDocument();
  });

  it('输入关键字后客户端过滤', async () => {
    renderEmployees();
    await screen.findByText('张三的数字分身');

    const input = screen.getByRole('searchbox', { name: '搜索员工' });
    fireEvent.change(input, { target: { value: '张三' } });

    await waitFor(() => expect(screen.queryByText('新员工入职助手')).not.toBeInTheDocument());
    expect(screen.getByText('张三的数字分身')).toBeInTheDocument();
  });

  it('加载失败时展示错误态并支持重试', async () => {
    let fail = true;
    vi.stubGlobal(
      'fetch',
      vi.fn((input: RequestInfo | URL) => {
        if (fail) return Promise.reject(new Error('network down'));
        const url = String(input);
        if (url.includes('/api/v1/employees')) return Promise.resolve(json(employees));
        return Promise.resolve(json({ status: 'ok' }));
      }),
    );

    renderEmployees();
    expect(await screen.findByText('数据加载失败')).toBeInTheDocument();

    fail = false;
    fireEvent.click(screen.getByRole('button', { name: /重新加载/ }));
    expect(await screen.findByText('张三的数字分身')).toBeInTheDocument();
  });
});
