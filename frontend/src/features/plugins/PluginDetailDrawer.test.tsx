import { afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import PluginDetailDrawer from './PluginDetailDrawer';

afterEach(() => vi.unstubAllGlobals());

describe('PluginDetailDrawer', () => {
  it('展示安全业务详情，并可独立触发启停操作', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({
      plugin_id: 'PLG-FUND', name: '基金资料查询', description: '查询获授权的基金公开资料',
      plugin_type: 'mcp', scope: 'shared', mcp_category: 'fund', current_version: '1.2.0',
      version: { version: '1.2.0', data_level: 'L2', deployment_mode: 'external', review_status: 'approved', publish_status: 'published', published_at: '2026-09-01T08:00:00' },
      tools: [{ name: 'query_fund_profile', description: '按基金代码查询基本资料' }],
      instruction_summary: null,
      authorization: { admin_enabled: true, employee_enabled: false, decision_mode: 'allow', effective: false },
      runtime: null,
    }), { status: 200, headers: { 'Content-Type': 'application/json' } })));
    const toggle = vi.fn();

    render(<PluginDetailDrawer pluginId="PLG-FUND" admin={false} enabled={false} onClose={vi.fn()} onToggle={toggle} />);

    expect(await screen.findByText('基金资料查询')).toBeInTheDocument();
    expect(screen.getByText('query_fund_profile')).toBeInTheDocument();
    expect(screen.getAllByText(/尚未检查/).length).toBeGreaterThan(0);
    expect(screen.queryByText(/endpoint|secret|authorization/i)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '启用插件' }));
    expect(toggle).toHaveBeenCalledWith('PLG-FUND', true);
  });
});
