import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import App from './App';

beforeEach(() => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => new Response(JSON.stringify([]), { status: 200, headers: { 'Content-Type': 'application/json' } })),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('App', () => {
  it('渲染侧边栏与首页指标', async () => {
    render(
      <MemoryRouter initialEntries={['/']}>
        <App />
      </MemoryRouter>,
    );
    expect(screen.getByText('数字员工平台')).toBeInTheDocument();
    expect(await screen.findByText('数字员工总数')).toBeInTheDocument();
  });
});
