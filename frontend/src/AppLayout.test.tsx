import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import AppLayout from './components/AppLayout';
import { CurrentUserProvider } from './context/CurrentUserContext';

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
        <CurrentUserProvider>
          <Routes>
            <Route element={<AppLayout />}>
              <Route path="/" element={<div>页面内容</div>} />
            </Route>
          </Routes>
        </CurrentUserProvider>
      </MemoryRouter>,
    );

    expect(screen.getByText('数字员工平台')).toBeInTheDocument();
    for (const label of ['我的职场', '数据总览', '数字员工', '能力中心', '安全中心', '协作团队', 'Agent协作演示']) {
      expect(screen.getAllByText(label).length).toBeGreaterThan(0);
    }
    expect(screen.getByRole('link', { name: 'Agent协作演示' })).toHaveAttribute('href', '/agentteams-collaboration');
    expect(await screen.findByText('服务正常')).toBeInTheDocument();
    expect(screen.getByText(/所有数据均为虚构/)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '跳到主要内容' })).toHaveAttribute('href', '#main-content');
  });

  it('为 Agent 协作演示提供更宽的主内容区', async () => {
    render(
      <MemoryRouter initialEntries={['/agentteams-collaboration']}>
        <CurrentUserProvider>
          <Routes>
            <Route element={<AppLayout />}>
              <Route path="/agentteams-collaboration" element={<div>协作演示内容</div>} />
            </Route>
          </Routes>
        </CurrentUserProvider>
      </MemoryRouter>,
    );

    expect(await screen.findByText('服务正常')).toBeInTheDocument();
    expect(screen.getByRole('main')).toHaveStyle({
      maxWidth: '1920px',
      padding: '16px 12px 24px',
    });
  });
});
