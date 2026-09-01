import { afterEach, describe, expect, it, vi } from 'vitest';
import { api } from '../../api/client';

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('AgentTeams collaboration API client', () => {
  it('uses the collaboration endpoints, encodes task IDs, and preserves zero event cursors', async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => json({}));
    vi.stubGlobal('fetch', fetchMock);

    await api.agentTeamsHealth();
    await api.listAgentTeamsTasks();
    await api.getAgentTeamsTask('AT /1');
    await api.getAgentTeamsGraph('AT /1');
    await api.getAgentTeamsEvents('AT /1', 0);
    await api.getAgentTeamsMessages('AT /1');
    await api.getAgentTeamsResult('AT /1');
    await api.cancelAgentTeamsTask('AT /1');
    await api.retryAgentTeamsTask('AT /1');

    expect(fetchMock.mock.calls.map(([url]) => String(url))).toEqual([
      '/api/v1/agentteams-collaboration/health',
      '/api/v1/agentteams-collaboration/tasks',
      '/api/v1/agentteams-collaboration/tasks/AT%20%2F1',
      '/api/v1/agentteams-collaboration/tasks/AT%20%2F1/graph',
      '/api/v1/agentteams-collaboration/tasks/AT%20%2F1/events?after_event_id=0',
      '/api/v1/agentteams-collaboration/tasks/AT%20%2F1/messages',
      '/api/v1/agentteams-collaboration/tasks/AT%20%2F1/result',
      '/api/v1/agentteams-collaboration/tasks/AT%20%2F1/cancel',
      '/api/v1/agentteams-collaboration/tasks/AT%20%2F1/retry',
    ]);
    expect(fetchMock.mock.calls[7][1]).toMatchObject({ method: 'POST' });
    expect(fetchMock.mock.calls[8][1]).toMatchObject({ method: 'POST' });
  });

  it('submits the complete collaboration task request body', async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => json({}));
    vi.stubGlobal('fetch', fetchMock);

    await api.createAgentTeamsTask({
      team_id: 'TEAM-1',
      leader_agent_id: 'DT-E10281',
      title: '入职协作分析',
      instruction: '请拆解并完成分析',
      material: { policy_version: '2026.08' },
    });

    expect(String(fetchMock.mock.calls[0][0])).toBe('/api/v1/agentteams-collaboration/tasks');
    expect(fetchMock.mock.calls[0][1]).toMatchObject({ method: 'POST' });
    expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toEqual({
      team_id: 'TEAM-1',
      leader_agent_id: 'DT-E10281',
      title: '入职协作分析',
      instruction: '请拆解并完成分析',
      material: { policy_version: '2026.08' },
    });
  });

  it('surfaces the backend readiness message from a structured HTTP error', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => json({ detail: { message: 'AgentTeams 协作环境未就绪' } }, 503)),
    );

    await expect(
      api.createAgentTeamsTask({
        team_id: 'TEAM-1',
        leader_agent_id: 'DT-E10281',
        title: '入职协作分析',
        instruction: '请拆解并完成分析',
        material: {},
      }),
    ).rejects.toThrow('AgentTeams 协作环境未就绪');
  });
});
