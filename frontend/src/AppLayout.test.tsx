import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import AppLayout from './components/AppLayout';

const json = (body: unknown) =>
  new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });

describe('AppLayout', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn(async () => json({ status: 'ok' })));
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('渲染导航、页脚免责声明与跳过链接', async () => {
    render(
      <MemoryRouter initialEntries={['/']}>
        <Routes>
          <Route element={<AppLayout />}>
            <Route path="/" element={<div>页面内容</div>} />
          </Route>
        </Routes>
      </MemoryRouter>,
    );

    expect(screen.getByText('数字员工平台')).toBeInTheDocument();
    for (const label of ['首页', '数字员工', '插件中心', '安全中心', '协作团队']) {
      expect(screen.getAllByText(label).length).toBeGreaterThan(0);
    }
    expect(await screen.findByText('服务正常')).toBeInTheDocument();
    expect(screen.getByText(/所有数据均为虚构/)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '跳到主要内容' })).toHaveAttribute('href', '#main-content');
  });
});
