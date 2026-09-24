import type {
  AccessRequest,
  AuditEvent,
  Capability,
  ChatMessage,
  ChatReply,
  Conversation,
  ConversationAttachment,
  ConversationMessage,
  ConversationSearchHit,
  ConversationSummary,
  Employee,
  KnowledgeBase,
  KnowledgeGrantMatrix,
  KnowledgeSourceStatus,
  Plugin,
  Policy,
  Skill,
  TaskRun,
  Team,
  TeamDetail,
  Workspace,
  WorkplaceHome,
  Account,
  AgentProfile,
  AgentExecution,
  AgentExecutionDetail,
  ConversationRunReply,
    DirectoryUser,
    EffectiveCapabilities,
  LoginReply,
  RuntimeInfo,
} from '@dwp/shared-schema';

const BASE = '/api/v1';
export const AUTH_EXPIRED_EVENT = 'dwp:auth-expired';
let authExpiredNotified = false;

type RequestOptions = RequestInit & { absolute?: boolean };

async function request<T>(path: string, init?: RequestOptions): Promise<T> {
  const url = init?.absolute ? path : `${BASE}${path}`;
  const { absolute: _absolute, ...fetchInit } = init ?? {};
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 30000);
  let res: Response;
  try {
    const multipart = fetchInit.body instanceof FormData;
    res = await fetch(url, {
      headers: { ...(multipart ? {} : { 'Content-Type': 'application/json' }), ...(fetchInit.headers ?? {}) },
      credentials: 'include',
      ...fetchInit,
      signal: controller.signal,
    });
  } catch (err) {
    clearTimeout(timer);
    if (controller.signal.aborted) {
      throw new Error('请求超时，请稍后重试');
    }
    throw err;
  }
  clearTimeout(timer);
  if (!res.ok) {
    if (res.status === 401 && !path.startsWith('/auth/login')) {
      if (!authExpiredNotified && typeof window !== 'undefined') {
        authExpiredNotified = true;
        window.dispatchEvent(new Event(AUTH_EXPIRED_EVENT));
      }
    }
    let message = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      const detail = body?.error?.message ?? body?.detail;
      message = typeof detail === 'string' ? detail : detail?.message ?? message;
    } catch {
      // 保留默认错误信息
    }
    throw new Error(message);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

function qs(params?: Record<string, string | undefined>): string {
  if (!params) return '';
  const parts = Object.entries(params).filter(([, v]) => v !== undefined && v !== '');
  return parts.length ? `?${parts.map(([k, v]) => `${k}=${encodeURIComponent(v!)}`).join('&')}` : '';
}

export const api = {
  health: () => request<{ status: string }>('/health', { absolute: true }),
  login: (accessKey: string) =>
    request<LoginReply>('/auth/key-login', { method: 'POST', body: JSON.stringify({ access_key: accessKey }) })
      .then((reply) => { authExpiredNotified = false; return reply; }),
  me: () => request<Account>('/auth/me'),
  logout: () => request<void>('/auth/logout', { method: 'POST' }),
  /**
   * 员工侧插件目录。`pluginType` 用于把货架拆成两个市场（skill / mcp）；
   * 不传则返回全部，六态与 `market_counts` 始终是全量口径（用于页签计数徽标）。
   */
  getMyPlugins: (pluginType?: 'skill' | 'mcp') =>
    request<MyPluginsReply>(`/my/plugins${qs({ plugin_type: pluginType })}`),
  /** 知识库市场货架：全部知识库 + 本人分身的状态（可申请/申请中/已授权/被占用）。 */
  listMyKnowledgeBases: () => request<{ items: KnowledgeMarketItem[] }>('/my/knowledge-bases'),
  /** 申请使用某个知识库；审批通过后由管理员侧写入独占授权。 */
  requestMyKnowledgeBase: (knowledgeBaseId: string, reason: string) =>
    request<AccessRequest>(`/my/knowledge-bases/${encodeURIComponent(knowledgeBaseId)}/request`, {
      method: 'POST',
      body: JSON.stringify({ reason }),
    }),
  getMyPluginDetail: (pluginId: string) => request<PluginSafeDetail>(`/my/plugins/${encodeURIComponent(pluginId)}`),
  /** 员工自助申请插件使用：申请人为登录账号自己的分身，审批通过后自动授权。 */
  requestMyPlugin: (pluginId: string, reason: string) =>
    request<AccessRequest>(`/my/plugins/${encodeURIComponent(pluginId)}/request`, {
      method: 'POST',
      body: JSON.stringify({ reason }),
    }),
  getMyPluginSubmissions: () => request<PluginSubmission[]>('/my/plugins/submissions'),
  submitPlugin: (form: FormData) => request<PluginSubmission>('/my/plugins/submissions', { method: 'POST', body: form }),
  /** 被驳回的提交重新上传：复用同一版本号，不产生孤立版本。 */
  resubmitPlugin: (versionId: number, form: FormData) =>
    request<PluginSubmission>(`/my/plugins/submissions/${versionId}/resubmit`, { method: 'POST', body: form }),
  enableMyPlugin: (pluginId: string, enabled: boolean) => request<{ ok: boolean }>(`/my/plugins/${encodeURIComponent(pluginId)}/${enabled ? 'enable' : 'disable'}`, { method: 'POST', body: '{}' }),
  listMyMemories: (agentId: string) => request<MemoryItem[]>(`/my/agents/${encodeURIComponent(agentId)}/memories`),
  updateMyMemory: (agentId: string, memoryId: string, content: string) => request<MemoryItem>(`/my/agents/${encodeURIComponent(agentId)}/memories/${encodeURIComponent(memoryId)}`, { method: 'PUT', body: JSON.stringify({ content }) }),
  deleteMyMemory: (agentId: string, memoryId: string) => request<void>(`/my/agents/${encodeURIComponent(agentId)}/memories/${encodeURIComponent(memoryId)}`, { method: 'DELETE' }),
  retainMyMemory: (agentId: string, memoryId: string) => request<MemoryItem>(`/my/agents/${encodeURIComponent(agentId)}/memories/${encodeURIComponent(memoryId)}/retain`, { method: 'POST' }),
  listAdminPlugins: () => request<UnifiedPlugin[]>('/admin/plugins'),
  getAdminPluginDetail: (pluginId: string) => request<PluginSafeDetail>(`/admin/plugins/${encodeURIComponent(pluginId)}`),
  listPluginSubmissions: () => request<PluginSubmission[]>('/admin/plugin-submissions'),
  reviewPlugin: (id: number, approve: boolean, note = '') => request<PluginSubmission>(`/admin/plugin-submissions/${id}/${approve ? 'approve' : 'reject'}`, { method: 'POST', body: JSON.stringify({ note }) }),
  publishPlugin: (pluginId: string, version: string) => request<PluginSubmission>(`/admin/plugins/${encodeURIComponent(pluginId)}/versions/${encodeURIComponent(version)}/publish`, { method: 'POST' }),
  rollbackPlugin: (pluginId: string, version: string) => request<PluginSubmission>(`/admin/plugins/${encodeURIComponent(pluginId)}/versions/${encodeURIComponent(version)}/rollback`, { method: 'POST' }),
  /** 下架插件：停用全部绑定，员工端/提示词/执行判定同时失效。 */
  withdrawPlugin: (pluginId: string) => request<PluginSafeDetail>(`/admin/plugins/${encodeURIComponent(pluginId)}/withdraw`, { method: 'POST' }),
  /** 技能治理台：改业务分类 / 数据等级 / 推荐位，以及下架与恢复上架。 */
  updatePluginGovernance: (pluginId: string, payload: PluginGovernanceInput) =>
    request<PluginGovernanceResult>(`/admin/plugins/${encodeURIComponent(pluginId)}/governance`, {
      method: 'PUT',
      body: JSON.stringify(payload),
    }),
  /** 为技能重新生成中文简介与业务分类；模型不可用时降级为原文首句，不报错。 */
  regeneratePluginIntro: (pluginId: string, prompt = '') =>
    request<PluginIntroResult>(`/admin/plugins/${encodeURIComponent(pluginId)}/intro`, {
      method: 'POST',
      body: JSON.stringify({ prompt }),
    }),
  listAgentPluginBindings: () => request<PluginBinding[]>('/admin/agent-plugin-bindings'),
  /** MCP 自助接入（管理端）：测试连接并解析工具清单，不落库。 */
  probeAdminMcp: (payload: McpProbeInput) =>
    request<McpProbeResult>('/admin/mcp/probe', { method: 'POST', body: JSON.stringify(payload) }),
  /** 用平台大模型补写中文名/简介/工具说明；传 prompt 可重新生成一版。不落库。 */
  draftAdminMcp: (payload: McpDraftInput) =>
    request<McpIntroDraft>('/admin/mcp/draft', { method: 'POST', body: JSON.stringify(payload) }),
  listAdminMcpServices: () => request<McpServiceList>('/admin/mcp/plugins'),
  /** 管理端登记即发布：登记后到「AI员工插件配置」授权给指定AI员工。 */
  createAdminMcpService: (payload: McpCreateInput) =>
    request<McpRegisteredService>('/admin/mcp/plugins', { method: 'POST', body: JSON.stringify(payload) }),
  /** 重新测试连接：刷新连通状态与工具数，不影响已发布版本。 */
  reprobeAdminMcpService: (pluginId: string) =>
    request<McpProbeResult>(`/admin/mcp/plugins/${encodeURIComponent(pluginId)}/probe`, { method: 'POST' }),
  /** 编辑已登记 MCP 服务：改地址/密钥会立即重测，工具清单变化会自动发布新版本。 */
  updateAdminMcpService: (pluginId: string, payload: McpServiceUpdateInput) =>
    request<McpRegisteredService>(`/admin/mcp/plugins/${encodeURIComponent(pluginId)}`, {
      method: 'PUT',
      body: JSON.stringify(payload),
    }),
  /** 用户级 MCP 令牌到期提醒（按人查，含企业级共享 MCP）。 */
  listMyMcpCredentialAlerts: () => request<McpCredentialAlerts>('/my/mcp/credential-alerts'),
  probeMyMcp: (payload: McpProbeInput) =>
    request<McpProbeResult>('/my/mcp/probe', { method: 'POST', body: JSON.stringify(payload) }),
  draftMyMcp: (payload: McpDraftInput) =>
    request<McpIntroDraft>('/my/mcp/draft', { method: 'POST', body: JSON.stringify(payload) }),
  listMyMcpServices: () => request<McpServiceList>('/my/mcp/plugins'),
  /** 员工自助登记个人 MCP：提交后等待管理员审核与发布。 */
  createMyMcpService: (payload: McpCreateInput) =>
    request<McpRegisteredService>('/my/mcp/plugins', { method: 'POST', body: JSON.stringify(payload) }),
  reprobeMyMcpService: (pluginId: string) =>
    request<McpProbeResult>(`/my/mcp/plugins/${encodeURIComponent(pluginId)}/probe`, { method: 'POST' }),
  createAgentPluginBinding: (payload: {
    plugin_id: string;
    target_agent_id: string;
    pinned_version?: string | null;
    admin_enabled?: boolean;
    decision_mode?: string;
    priority?: number;
  }) => request<PluginBinding>('/admin/agent-plugin-bindings', { method: 'POST', body: JSON.stringify(payload) }),
  /** 局部更新绑定：只提交要改的字段（后端 exclude_unset），未提交字段保持原值。 */
  updateAgentPluginBinding: (bindingId: number, payload: Partial<Omit<PluginBinding, 'id'>>) =>
    request<PluginBinding>(`/admin/agent-plugin-bindings/${bindingId}`, { method: 'PUT', body: JSON.stringify(payload) }),
  deleteAgentPluginBinding: (bindingId: number) =>
    request<void>(`/admin/agent-plugin-bindings/${bindingId}`, { method: 'DELETE' }),
  memoryHealth: () => request<{ mode: string; status: string; memories: number; pending_jobs: number; failed_jobs: number }>('/admin/memory/health'),
  listAgents: () => request<AgentProfile[]>('/agents'),
  /** 平台自身的镜像与交付版本（运维页顶部汇总）。 */
  getRuntimeInfo: () => request<RuntimeInfo>('/agents/runtime-info'),
  getEffectiveCapabilities: (employeeId: string) =>
    request<EffectiveCapabilities>(`/agents/${encodeURIComponent(employeeId)}/effective-capabilities`),
  startAgentRuntime: (employeeId: string) =>
    request(`/agents/${encodeURIComponent(employeeId)}/runtime/start`, { method: 'POST' }),
  stopAgentRuntime: (employeeId: string) =>
    request(`/agents/${encodeURIComponent(employeeId)}/runtime/stop`, { method: 'POST' }),
  listDirectoryUsers: () => request<DirectoryUser[]>('/directory/users'),
  syncDirectory: () => request<DirectoryUser[]>('/directory/sync', { method: 'POST' }),
  listEmployees: (params?: { type?: string }) => request<Employee[]>(`/employees${qs(params)}`),
  getEmployee: (employeeNo: string) => request<Employee>(`/employees/${encodeURIComponent(employeeNo)}`),
  listPlugins: () => request<Plugin[]>('/plugins'),
  listCapabilities: (actorNo: string) =>
    request<Capability[]>(`/capabilities?actor_no=${encodeURIComponent(actorNo)}`),
  listPolicies: () => request<Policy[]>('/policies'),
  listAudit: (params?: { decision?: string }) => request<AuditEvent[]>(`/audit${qs(params)}`),
  listAccessRequests: (params?: { applicant_no?: string; status?: string }) =>
    request<AccessRequest[]>(`/access-requests${qs(params)}`),
  createAccessRequest: (applicantNo: string, resourceType: 'knowledge' | 'plugin', resourceId: string, reason: string) =>
    request<AccessRequest>(`/access-requests?applicant_no=${encodeURIComponent(applicantNo)}`, {
      method: 'POST',
      body: JSON.stringify({ resource_type: resourceType, resource_id: resourceId, reason }),
    }),
  approveAccessRequest: (requestId: number, approve: boolean, actorNo: string) =>
    request<AccessRequest>(`/access-requests/${requestId}/approve`, {
      method: 'POST',
      body: JSON.stringify({ approve, actor_no: actorNo }),
    }),
  /** 管理端审批通道：管理员账号没有分身，走不了老接口的「正式员工分身」校验。 */
  approveAdminAccessRequest: (requestId: number, approve: boolean, note = '') =>
    request<AccessRequest>(`/admin/access-requests/${requestId}/approve`, {
      method: 'POST',
      body: JSON.stringify({ approve, note }),
    }),
  listTeams: () => request<Team[]>('/teams'),
  getTeam: (teamId: string) => request<TeamDetail>(`/teams/${encodeURIComponent(teamId)}`),
  createTask: (teamId: string, requestText: string) =>
    request<TaskRun>(`/teams/${encodeURIComponent(teamId)}/tasks`, {
      method: 'POST',
      body: JSON.stringify({ request: requestText }),
    }),
  getTask: (teamId: string, taskId: string) =>
    request<TaskRun>(`/teams/${encodeURIComponent(teamId)}/tasks/${encodeURIComponent(taskId)}`),
  approveTask: (taskId: string, approve: boolean, actorNo: string) =>
    request<TaskRun>(`/tasks/${encodeURIComponent(taskId)}/approve`, {
      method: 'POST',
      body: JSON.stringify({ approve, actor_no: actorNo }),
    }),
  listKnowledgeBases: () => request<KnowledgeBase[]>('/knowledge-bases'),
  /** 知识源连通状态（内部知识引擎 REST / VeRAG MCP / 已登记外部 MCP）。 */
  listKnowledgeSources: () => request<KnowledgeSourceStatus[]>('/admin/knowledge-sources'),
  /** 手动重测：不传 source 时按 scope 重测一批（knowledge 只测知识通道，all 连 MCP 一起）。 */
  probeKnowledgeSources: (payload: { source?: string; scope?: 'knowledge' | 'all' } = {}) =>
    request<KnowledgeSourceStatus[]>('/admin/knowledge-sources/probe', {
      method: 'POST',
      body: JSON.stringify({ scope: 'knowledge', ...payload }),
    }),
  /** 知识库独占分配矩阵（员工 × 知识库 + 占用关系）。 */
  getKnowledgeGrants: () => request<KnowledgeGrantMatrix>('/admin/knowledge-grants'),
  /** SkillHub 技能市场：接入状态 + 17 个分类（未接入时 enabled=false 并给出原因）。 */
  getSkillHubOverview: () => request<SkillHubOverview>('/admin/skillhub/overview'),
  /** SkillHub 目录（平台侧代理 + TTL 缓存）；`category` 由平台侧筛选。 */
  listSkillHubSkills: (params: { page?: number; pageSize?: number; keyword?: string; category?: string } = {}) =>
    request<SkillHubSkillPage>(`/admin/skillhub/skills${qs({
      page: params.page === undefined ? undefined : String(params.page),
      page_size: params.pageSize === undefined ? undefined : String(params.pageSize),
      keyword: params.keyword,
      category: params.category,
    })}`),
  /** 批量导入到平台货架：立即返回 batch_id，进度查 getSkillHubImportBatch（避免网关 300s 超时）。 */
  importSkillHubSkills: (slugs: string[], generateIntro = true) =>
    request<SkillHubImportBatchStart>('/admin/skillhub/import', {
      method: 'POST',
      body: JSON.stringify({ slugs, generate_intro: generateIntro }),
    }),
  /** 批次进度：轮询到 status !== 'running' 即可停止。 */
  getSkillHubImportBatch: (batchId: string) =>
    request<SkillHubImportProgress>(`/admin/skillhub/imports/${encodeURIComponent(batchId)}`),
  /** 导入记录（可按状态筛选），用于「导入记录」抽屉与失败重试。 */
  listSkillHubImports: (status?: string) =>
    request<{ items: SkillHubImportRecord[] }>(`/admin/skillhub/imports${qs({ status })}`),
  /** 清空目录/分类缓存，让市场页立刻看到远端最新内容。 */
  refreshSkillHubCache: () =>
    request<{ refreshed: boolean }>('/admin/skillhub/cache/refresh', { method: 'POST', body: '{}' }),
  /** 整体替换某个 AI 员工的知识库授权集合；与其他员工冲突时后端返回 409。 */
  replaceKnowledgeGrants: (employeeId: string, knowledgeBaseIds: string[]) =>
    request<KnowledgeGrantMatrix>(`/admin/knowledge-grants/${encodeURIComponent(employeeId)}`, {
      method: 'PUT',
      body: JSON.stringify({ knowledge_base_ids: knowledgeBaseIds }),
    }),
  getWorkspace: (employeeNo: string) => request<Workspace>(`/employees/${encodeURIComponent(employeeNo)}/workspace`),
  chat: (employeeNo: string, message: string, sessionId?: string) =>
    request<ChatReply>(`/employees/${encodeURIComponent(employeeNo)}/chat`, {
      method: 'POST',
      body: JSON.stringify({ message, session_id: sessionId ?? null }),
    }),
  listMessages: (sessionId: string) => request<ChatMessage[]>(`/chat/sessions/${encodeURIComponent(sessionId)}/messages`),
  getWorkplace: (actorNo: string) => request<WorkplaceHome>(`/workplace?actor_no=${encodeURIComponent(actorNo)}`),
  listSkills: (actorNo: string) => request<Skill[]>(`/skills?actor_no=${encodeURIComponent(actorNo)}`),
  createSkill: (payload: { actor_no: string; name: string; description?: string; content?: string }) =>
    request<Skill>('/skills', { method: 'POST', body: JSON.stringify(payload) }),
  updateSkill: (
    skillId: string,
    actorNo: string,
    patch: Partial<Pick<Skill, 'name' | 'description' | 'content' | 'status'>>,
  ) => request<Skill>(`/skills/${encodeURIComponent(skillId)}?actor_no=${encodeURIComponent(actorNo)}`, {
    method: 'PUT',
    body: JSON.stringify(patch),
  }),
  deleteSkill: (skillId: string, actorNo: string) =>
    request<void>(`/skills/${encodeURIComponent(skillId)}?actor_no=${encodeURIComponent(actorNo)}`, { method: 'DELETE' }),
  listConversations: (actorNo: string) =>
    request<ConversationSummary[]>(`/conversations?actor_no=${encodeURIComponent(actorNo)}`),
  searchConversations: (actorNo: string, q: string, limit = 20) =>
    request<ConversationSearchHit[]>(
      `/conversations/search?actor_no=${encodeURIComponent(actorNo)}&q=${encodeURIComponent(q)}&limit=${limit}`,
    ),
  createConversation: (payload: {
    actor_no: string;
    kind: 'direct' | 'group';
    title?: string;
    participant_employee_nos: string[];
  }) => request<Conversation>('/conversations', { method: 'POST', body: JSON.stringify(payload) }),
  getConversation: (conversationId: string) =>
    request<Conversation>(`/conversations/${encodeURIComponent(conversationId)}`),
  sendConversationMessage: (conversationId: string, actorNo: string, content: string, attachmentIds: string[] = []) =>
    request<Conversation>(`/conversations/${encodeURIComponent(conversationId)}/messages`, {
      method: 'POST',
      body: JSON.stringify({ actor_no: actorNo, content, ...(attachmentIds.length ? { attachment_ids: attachmentIds } : {}) }),
    }),
  startConversationRun: (conversationId: string, actorNo: string, content: string, attachmentIds: string[] = []) =>
    request<ConversationRunReply>(`/conversations/${encodeURIComponent(conversationId)}/runs`, {
      method: 'POST',
      body: JSON.stringify({ actor_no: actorNo, content, ...(attachmentIds.length ? { attachment_ids: attachmentIds } : {}) }),
    }),
  uploadConversationAttachment: (conversationId: string, file: File) => {
    const form = new FormData();
    form.append('file', file);
    return request<ConversationAttachment>(`/conversations/${encodeURIComponent(conversationId)}/attachments`, {
      method: 'POST', body: form,
    });
  },
  listConversationAttachments: (conversationId: string) =>
    request<ConversationAttachment[]>(`/conversations/${encodeURIComponent(conversationId)}/attachments`),
  getConversationAttachment: (conversationId: string, attachmentId: string) =>
    request<ConversationAttachment>(`/conversations/${encodeURIComponent(conversationId)}/attachments/${encodeURIComponent(attachmentId)}`),
  deleteConversationAttachment: (conversationId: string, attachmentId: string) =>
    request<void>(`/conversations/${encodeURIComponent(conversationId)}/attachments/${encodeURIComponent(attachmentId)}`, { method: 'DELETE' }),
  retryConversationAttachment: (conversationId: string, attachmentId: string) =>
    request<ConversationAttachment>(`/conversations/${encodeURIComponent(conversationId)}/attachments/${encodeURIComponent(attachmentId)}/retry`, { method: 'POST' }),
  getActiveConversationRun: (conversationId: string) =>
    request<AgentExecution | null>(`/conversations/${encodeURIComponent(conversationId)}/runs/active`),
  getLatestConversationRun: (conversationId: string) =>
    request<AgentExecutionDetail | null>(`/conversations/${encodeURIComponent(conversationId)}/runs/latest`),
  getConversationRunHistory: (conversationId: string) =>
    request<AgentExecutionDetail[]>(`/conversations/${encodeURIComponent(conversationId)}/runs/history`),
  getOlderConversationRunHistory: (conversationId: string, beforeExecutionId: string) =>
    request<AgentExecutionDetail[]>(`/conversations/${encodeURIComponent(conversationId)}/runs/history?limit=20&before_execution_id=${encodeURIComponent(beforeExecutionId)}`),
  retryConversationRun: (conversationId: string, executionId: string) =>
    request<ConversationRunReply>(`/conversations/${encodeURIComponent(conversationId)}/runs/${encodeURIComponent(executionId)}/retry`, { method: 'POST' }),
  editConversationMessage: (conversationId: string, messageSeq: number, actorNo: string, content: string) =>
    request<ConversationMessage>(`/conversations/${encodeURIComponent(conversationId)}/messages/${messageSeq}`, {
      method: 'PATCH',
      body: JSON.stringify({ actor_no: actorNo, content }),
    }),
  rerunConversationMessage: (conversationId: string, messageSeq: number, actorNo: string, content: string) =>
    request<ConversationRunReply>(`/conversations/${encodeURIComponent(conversationId)}/messages/${messageSeq}/runs`, {
      method: 'POST',
      body: JSON.stringify({ actor_no: actorNo, content }),
    }),
  cancelConversationRun: (conversationId: string, executionId: string) =>
    request<AgentExecution>(`/conversations/${encodeURIComponent(conversationId)}/runs/${encodeURIComponent(executionId)}/cancel`, { method: 'POST' }),
  /** 会话内全部「待补全」记录：前端按 execution_id 分组渲染补全气泡。 */
  listConversationInputRequests: (conversationId: string) =>
    request<ToolInputRequest[]>(`/conversations/${encodeURIComponent(conversationId)}/input-requests`),
  /** 提交缺失信息并自动续跑一轮（凭据加密存本人，明文不回显）。 */
  submitRunInput: (
    conversationId: string,
    executionId: string,
    actorNo: string,
    requestId: string,
    values: Record<string, string>,
  ) =>
    request<ConversationRunReply>(`/conversations/${encodeURIComponent(conversationId)}/runs/${encodeURIComponent(executionId)}/input`, {
      method: 'POST',
      body: JSON.stringify({ actor_no: actorNo, request_id: requestId, values }),
    }),
  /** 忽略一条待补全（落库为 cancelled，刷新后不再重复弹出）。 */
  ignoreRunInput: (
    conversationId: string,
    executionId: string,
    requestId: string,
    actorNo: string,
  ) =>
    request<ToolInputRequest>(
      `/conversations/${encodeURIComponent(conversationId)}/runs/${encodeURIComponent(executionId)}`
      + `/input-requests/${encodeURIComponent(requestId)}/ignore`,
      { method: 'POST', body: JSON.stringify({ actor_no: actorNo }) },
    ),
  conversationRunEventUrl: (conversationId: string, executionId: string, afterEventId?: string) =>
    `${BASE}/conversations/${encodeURIComponent(conversationId)}/runs/${encodeURIComponent(executionId)}/events${
      afterEventId ? `?after_event_id=${encodeURIComponent(afterEventId)}` : ''
    }`,
  addConversationParticipant: (conversationId: string, employeeNo: string) =>
    request<Conversation>(`/conversations/${encodeURIComponent(conversationId)}/participants`, {
      method: 'POST',
      body: JSON.stringify({ employee_no: employeeNo }),
    }),
  clearConversation: (conversationId: string, actorNo: string) =>
    request<{ ok: boolean }>(
      `/conversations/${encodeURIComponent(conversationId)}/clear?actor_no=${encodeURIComponent(actorNo)}`,
      { method: 'POST' },
    ),
  deleteConversation: (conversationId: string, actorNo: string) =>
    request<{ ok: boolean }>(
      `/conversations/${encodeURIComponent(conversationId)}?actor_no=${encodeURIComponent(actorNo)}`,
      { method: 'DELETE' },
    ),
};

/**
 * 插件市场货架的六态（后端 `market_plugins` 的唯一契约，逐字一致）：
 * - `own_draft`   本人尚未发布的个人插件（只能提交新版本）
 * - `enabled`     已启用（对本人分身生效）
 * - `granted`     管理员已授权、尚未启用
 * - `blocked`     管理员已禁止（不可申请）
 * - `pending`     已提交申请、等待审批
 * - `requestable` 可申请使用
 */
export type PluginMarketState = 'own_draft' | 'enabled' | 'granted' | 'blocked' | 'pending' | 'requestable';

export interface UnifiedPlugin {
  id?: string | number;
  plugin_id?: string;
  name: string;
  plugin_type: 'skill' | 'mcp';
  scope?: string;
  mcp_category?: string;
  /** `effective_plugins` 对 MCP 返回的字段名（与 `mcp_category` 同义，仅历史口径差异）。 */
  category?: string | null;
  current_version?: string;
  version?: string;
  status?: string;
  tools?: unknown[];
  owner_human_no?: string | null;
  description?: string;
  /** 仅市场货架返回：对该员工分身的当前状态。 */
  state?: PluginMarketState;
  /** 仅 `state === 'pending'` 时非空：待审批的申请单号。 */
  request_id?: number | null;
  data_level?: string;
  /** 技能业务分类（19 类词表之一）；MCP 用 `mcp_category`。 */
  skill_category?: string | null;
  /** 运营推荐位：员工货架上置顶并打「精选」标。 */
  featured?: boolean;
  /** 简介 provenance（仅管理端列表返回）：区分 AI 生成与原文透传。 */
  intro?: { generated?: boolean; degraded_reason?: string | null; generated_at?: string } | null;
  /** 卡片上的来源徽标：skillhub（SkillHub 导入）/ platform（平台共享）/ personal（本人）。 */
  source?: 'skillhub' | 'platform' | 'personal';
}

/** 员工侧插件目录：market 为当前市场的货架，market_counts 始终是全量计数。 */
export interface MyPluginsReply {
  effective: UnifiedPlugin[];
  available: UnifiedPlugin[];
  market: UnifiedPlugin[];
  market_counts?: Record<string, number>;
}

/** 知识库市场货架的四种状态（后端 `knowledge_market` 的唯一契约，逐字一致）。 */
export type KnowledgeMarketState = 'enabled' | 'pending' | 'occupied' | 'requestable';

export interface KnowledgeMarketItem {
  id: string;
  name: string;
  domain?: string;
  data_level?: string;
  description?: string;
  status?: string;
  state: KnowledgeMarketState;
  request_id?: number | null;
  /** 被他人独占时的占用方（AI员工工号）与名称。 */
  occupant?: string | null;
  occupant_name?: string | null;
  /** 当前生效的数据通道（rest / verag），null 表示都不可达。 */
  active_source?: string | null;
  source_available?: boolean;
}

export interface SkillHubCategory {
  category: string;
  desc?: string;
}

export interface SkillHubOverview {
  enabled: boolean;
  base_url?: string;
  categories: SkillHubCategory[];
  message?: string;
}

export interface SkillHubSkill {
  slug: string;
  display_name: string;
  summary?: string;
  version?: string;
  source?: string;
  category?: string;
  downloads?: number;
  stars?: number;
  updated_at?: number | null;
  namespace?: string;
  author?: string;
  department?: string;
  tags?: string[];
  /** tags 命中「推荐/精选/热门」即视为运营推荐位。 */
  featured?: boolean;
  download_disabled?: boolean;
  /** 是否已导入平台货架（已导入的卡片不允许重复勾选）。 */
  imported?: boolean;
  imported_version?: string | null;
  imported_plugin_id?: string | null;
}

export interface SkillHubSkillPage {
  items: SkillHubSkill[];
  total: number;
  page: number;
  page_size: number;
  imported_total: number;
}

export type SkillHubImportStatus = 'imported' | 'skipped' | 'failed' | 'pending';

export interface SkillHubImportRecord {
  id: number;
  /** 批量导入批次号；单条导入也为批次（每次提交一个批次）。 */
  batch_id?: string | null;
  slug: string;
  version?: string;
  source?: string;
  display_name?: string;
  category?: string;
  plugin_id?: string | null;
  sha256?: string;
  status: SkillHubImportStatus;
  message?: string;
  file_count?: number;
  instruction_chars?: number;
  imported_by?: string;
  created_at: string;
  updated_at: string;
}

export interface SkillHubImportBatch {
  results: SkillHubImportRecord[];
  summary: { imported: number; skipped: number; failed: number };
}

/** 批量导入已受理：接口立即返回批次号，实际导入在后台串行执行。 */
export interface SkillHubImportBatchStart {
  batch_id: string;
  total: number;
  status: string;
}

/** 批次进度：`status` 为 running / completed / unknown（进程重启后批次登记丢失）。 */
export interface SkillHubImportProgress extends SkillHubImportBatch {
  batch_id: string;
  status: string;
  total: number;
  done: number;
}

/** 管理端技能治理台的行内编辑入参（只提交需要改的字段）。 */
export interface PluginGovernanceInput {
  skill_category?: string | null;
  data_level?: string;
  featured?: boolean;
  status?: 'active' | 'withdrawn';
}

export interface PluginGovernanceResult {
  plugin_id: string;
  status: string;
  skill_category: string | null;
  data_level: string;
  featured: boolean;
}

export interface PluginIntroResult {
  plugin_id: string;
  description: string;
  skill_category: string | null;
  generated: boolean;
  degraded_reason: string | null;
}
export interface PluginSafeDetail {
  plugin_id: string;
  name: string;
  description: string;
  plugin_type: 'skill' | 'mcp';
  scope: 'personal' | 'shared';
  mcp_category: string | null;
  /** 技能业务分类（19 类词表之一）。 */
  skill_category: string | null;
  /** 运营推荐位：员工货架置顶并打「精选」标。 */
  featured: boolean;
  /** 来源徽标：skillhub / platform / personal。 */
  source: 'skillhub' | 'platform' | 'personal';
  current_version: string | null;
  version: {
    version: string;
    data_level: 'L1' | 'L2' | 'L3';
    deployment_mode: 'instruction' | 'external' | 'hosted';
    review_status: string;
    publish_status: string;
    published_at: string | null;
    /** 驳回原因（提交者需要据此修改后重提）。 */
    review_note?: string;
    submitted_by?: string;
  } | null;
  tools: Array<{ name: string; description: string }>;
  instruction_summary: string | null;
  authorization: {
    admin_enabled: boolean;
    employee_enabled: boolean;
    decision_mode: string;
    effective: boolean;
  };
  runtime: { state: string; health: string; updated_at: string | null } | null;
  versions?: Array<{
    version: string;
    data_level: string;
    deployment_mode: string;
    review_status: string;
    publish_status: string;
    submitted_by: string;
    reviewed_by: string | null;
    reviewed_at: string | null;
    published_at: string | null;
    created_at: string;
  }>;
  bindings?: Array<{
    id: number;
    target_agent_id: string;
    admin_enabled: boolean;
    employee_enabled: boolean;
    decision_mode: string;
    priority: number;
  }>;
}
export interface PluginSubmission extends UnifiedPlugin { id: number; data_level: string; deployment_mode: string; review_status: string; publish_status: string; submitted_by: string; review_note: string; created_at: string; owner_human_no?: string | null }
export interface MemoryItem { id: string; agent_id: string; memory_type: string; content: string; source: string; retained: boolean; expires_at?: string; sync_status: string; created_at: string }
export interface PluginBinding { id: number; plugin_id: string; target_agent_id: string; pinned_version?: string; admin_enabled: boolean; employee_enabled: boolean; decision_mode: string; priority: number }

/**
 * MCP 服务自助接入（`/admin/mcp/*` 与 `/my/mcp/*`）。
 *
 * 三段式：探测（`probe`，不落库）→ AI 简介草稿（`draft`，不落库）→ 登记（`create`）。
 * 所有返回体都不含明文密钥，只有掩码 `secret_hint`。
 */
export interface McpToolParameter { name: string; type: string; description: string }
export interface McpToolSummary {
  name: string;
  description: string;
  parameters: McpToolParameter[];
  required: string[];
  parameters_truncated?: boolean;
  /** 探测摘要携带的入参 Schema，登记时原样回传，避免落库丢参数定义。 */
  input_schema?: Record<string, unknown>;
}
export interface McpProbeResult {
  url: string;
  protocol_version: string;
  server_info: { name?: string; version?: string; title?: string } & Record<string, unknown>;
  capabilities: Record<string, unknown>;
  tools: McpToolSummary[];
  tools_count: number;
  tools_truncated: boolean;
  latency_ms: number;
  authenticated: boolean;
  auth_header_name: string;
}
export interface McpIntroDraft {
  name: string;
  description: string;
  tools: Array<{ name: string; description: string }>;
  mcp_category: string;
  data_level: string;
  /** 是否由大模型生成；`false` 表示降级为「原样透传 + 规整」。 */
  generated: boolean;
  degraded_reason: string | null;
}
export interface McpProbeInput {
  url: string;
  auth_header_name?: string | null;
  /** 不传 = 复用已存密钥；传空串 = 显式匿名接入。 */
  api_key?: string | null;
  timeout_seconds?: number | null;
  plugin_id?: string | null;
}
export interface McpDraftInput {
  url: string;
  tools: McpToolSummary[];
  server_info?: Record<string, unknown>;
  /** 自定义提示词：传了就覆盖默认指令重新生成一版。 */
  prompt?: string | null;
}
export interface McpCreateInput {
  url: string;
  name: string;
  description?: string;
  auth_header_name?: string | null;
  api_key?: string | null;
  timeout_seconds?: number | null;
  mcp_category?: string | null;
  data_level?: string;
  scope?: string;
  version?: string;
  tools: McpToolSummary[];
  server_info?: Record<string, unknown>;
  latency_ms?: number | null;
}
export interface McpRegisteredService {
  plugin_id: string;
  name: string;
  description: string;
  scope: 'personal' | 'shared';
  owner_human_no: string | null;
  mcp_category: string | null;
  data_level: string;
  status: string;
  current_version: string | null;
  url: string;
  timeout_seconds: number | null;
  tool_prefix: string | null;
  authenticated: boolean;
  auth_header_name: string | null;
  secret_hint: string | null;
  tools_count: number | null;
  latency_ms: number | null;
  checked_at: string | null;
  server_info: Record<string, unknown>;
  runtime_state: string | null;
  runtime_health: string | null;
  pending_version_id: number | null;
  /** 最近一次探测结论：reachable / unavailable（历史数据可能为 null）。 */
  probe_state: string | null;
  /** 最近一次探测失败的可读原因；成功时为空串。 */
  last_error: string;
  /** 最近一次探测时间（与 checked_at 同源，便于直接展示失败时间）。 */
  probe_checked_at: string | null;
  /** 编辑接口附加：本次是否触发了重新探测。 */
  reprobed?: boolean;
  /** 编辑接口附加：工具清单变化时自动发布的新版本号。 */
  new_version?: string | null;
  /** 本人在这条服务上的用户级凭据状态（仅员工端 `/my/mcp/plugins` 返回）。 */
  credential?: McpCredentialAlert;
}

/** 用户级 MCP 凭据（按人签发的令牌）到期状态；只有状态与到期时间，没有凭据内容。 */
export interface McpCredentialAlert {
  plugin_id: string;
  name: string;
  mcp_category: string | null;
  state: 'ok' | 'expiring' | 'expired' | 'unknown';
  days_left: number | null;
  /** 到期时间（后端 naive UTC，展示要走 formatServerTime）。 */
  expires_at: string | null;
}

export interface McpCredentialAlerts {
  warn_days: number;
  alerts: McpCredentialAlert[];
  expired_count: number;
  expiring_count: number;
}

/** 编辑已登记 MCP 服务：只提交要改的字段；api_key 不传 = 不动密钥。 */
export interface McpServiceUpdateInput {
  name?: string;
  description?: string;
  mcp_category?: string;
  data_level?: string;
  url?: string;
  timeout_seconds?: number;
  auth_header_name?: string;
  /** '' = 显式转匿名（删除凭据）；不传 = 保留既有密钥。 */
  api_key?: string;
}
export interface McpServiceList {
  services: McpRegisteredService[];
  /** 凭据加密密钥是否已配置；未配置时只允许接入无鉴权服务。 */
  credential_encryption_available: boolean;
}

/**
 * MCP 工具「缺失信息补全」契约（与后端 `schemas.ToolInputRequestOut` 逐字一致）。
 *
 * 工具缺鉴权（key / JWT / token）或缺必填业务参数时，后端落一条待补全记录并 emit
 * `input_required` 事件；前端在执行轨迹里渲染输入气泡，用户填完提交即自动续跑一轮。
 * 凭据类字段只回掩码 `masked`，明文永不回显。
 */
export interface ToolInputField {
  /** 字段名，与真实请求头/参数名对齐（如 `X-MCP-JWT`、`keyword`）。 */
  key: string;
  label: string;
  /** `secret` 用密码框并打码；`text` 为普通输入。 */
  kind: 'secret' | 'text' | string;
  required: boolean;
  hint: string;
  source: string;
  /** 已保存凭据的掩码（如 `****abcd`），为空表示尚无。 */
  masked: string;
  /** `missing` | `expired` | `tool_error`。 */
  reason: string;
}

export interface ToolInputRequest {
  id: string;
  conversation_id: string;
  execution_id: string;
  trace_id: string;
  plugin_id: string;
  tool_name: string;
  fields: ToolInputField[];
  reason: string;
  /** `pending` | `submitted` | `expired` | `cancelled`。 */
  status: string;
  submitted_values: Record<string, string>;
  resolved_at: string | null;
  resolved_by: string;
  created_at: string;
}
