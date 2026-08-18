// 临时前端 Mock 数据（后端未启动时使用）
// 内容与 mock-data/seed.json 保持一致，均为虚构数据。
// TODO: 后端跑起来后删除本文件，并移除 client.ts 里的回退逻辑。
import type { AuditEvent, Employee, KnowledgeBase, Plugin, Policy, Team, TeamDetail } from '@dwp/shared-schema';

export const mockEmployees: Employee[] = [
  {
    id: 'DT-E10281',
    employee_no: 'DT-E10281',
    name: '张三的数字分身',
    type: 'twin',
    source_human_no: 'E10281',
    owner_human_no: 'E10281',
    department: '架构部',
    role_prompt: '你是架构部员工张三的数字分身，可回答部门制度与流程问题。',
    status: 'active',
    runtime_type: 'demo',
    runtime_ref: null,
    location: 'remote',
    internet: 'deny',
    max_data_level: 'L2',
    allowed_domains: ['PUB', 'HR_L2_DEMO'],
    grants: [
      { plugin_id: 'knowledge-l1', name: '公开制度知识库', type: 'knowledge', action: 'read', decision_mode: 'allow', data_level: 'L1' },
      { plugin_id: 'knowledge-l2', name: '内部流程知识库', type: 'knowledge', action: 'read', decision_mode: 'allow', data_level: 'L2' },
      { plugin_id: 'rpa-report', name: '报表机器人', type: 'rpa', action: 'execute', decision_mode: 'deny', data_level: 'L3' },
      { plugin_id: 'internet-search', name: '公网搜索', type: 'http', action: 'search', decision_mode: 'allow', data_level: 'L1' },
    ],
  },
  {
    id: 'DT-E20999',
    employee_no: 'DT-E20999',
    name: '陈晓萌的数字分身',
    type: 'twin',
    source_human_no: 'E20999',
    owner_human_no: 'E20999',
    department: '研发部',
    role_prompt: '你是实习生陈晓萌的数字分身，仅可访问公开知识。',
    status: 'active',
    runtime_type: 'demo',
    runtime_ref: null,
    location: 'remote',
    internet: 'deny',
    max_data_level: 'L1',
    allowed_domains: ['PUB'],
    grants: [
      { plugin_id: 'knowledge-l1', name: '公开制度知识库', type: 'knowledge', action: 'read', decision_mode: 'allow', data_level: 'L1' },
      { plugin_id: 'knowledge-l2', name: '内部流程知识库', type: 'knowledge', action: 'read', decision_mode: 'deny', data_level: 'L2' },
      { plugin_id: 'internet-search', name: '公网搜索', type: 'http', action: 'search', decision_mode: 'allow', data_level: 'L1' },
    ],
  },
  {
    id: 'VE-0001',
    employee_no: 'VE-0001',
    name: '新员工入职助手',
    type: 'virtual',
    source_human_no: null,
    owner_human_no: 'E10021',
    department: '人力资源部',
    role_prompt: '你是新员工入职助手，只回答入职、制度与 IT 准备相关问题。',
    status: 'active',
    runtime_type: 'harness',
    runtime_ref: 'headless',
    location: 'remote',
    internet: 'deny',
    max_data_level: 'L2',
    allowed_domains: ['PUB', 'HR_L2_DEMO'],
    grants: [
      { plugin_id: 'knowledge-l1', name: '公开制度知识库', type: 'knowledge', action: 'read', decision_mode: 'allow', data_level: 'L1' },
      { plugin_id: 'adp-onboarding', name: '入职流程 Workflow', type: 'workflow', action: 'execute', decision_mode: 'allow', data_level: 'L2' },
    ],
  },
  {
    id: 'VE-0002',
    employee_no: 'VE-0002',
    name: 'HR 助理',
    type: 'virtual',
    source_human_no: null,
    owner_human_no: 'E10021',
    department: '人力资源部',
    role_prompt: '你是 HR 助理，负责员工信息查询与入职制度咨询。',
    status: 'active',
    runtime_type: 'openclaw',
    runtime_ref: 'openclaw-worker-hr',
    location: 'remote',
    internet: 'deny',
    max_data_level: 'L2',
    allowed_domains: ['PUB', 'HR_L2_DEMO'],
    grants: [
      { plugin_id: 'hr-employee-mcp', name: '员工查询 MCP', type: 'mcp', action: 'execute', decision_mode: 'allow', data_level: 'L2' },
    ],
  },
  {
    id: 'VE-0003',
    employee_no: 'VE-0003',
    name: 'IT 助理',
    type: 'virtual',
    source_human_no: null,
    owner_human_no: 'E10281',
    department: 'IT 服务部',
    role_prompt: '你是 IT 助理，负责账号开通与 IT 流程咨询。',
    status: 'active',
    runtime_type: 'openclaw',
    runtime_ref: 'openclaw-worker-it',
    location: 'remote',
    internet: 'deny',
    max_data_level: 'L2',
    allowed_domains: ['PUB', 'IT_L2_DEMO'],
    grants: [
      { plugin_id: 'adp-onboarding', name: '入职流程 Workflow', type: 'workflow', action: 'execute', decision_mode: 'allow', data_level: 'L2' },
      { plugin_id: 'rpa-report', name: '报表机器人', type: 'rpa', action: 'execute', decision_mode: 'approval', data_level: 'L3' },
    ],
  },
];

export const mockPlugins: Plugin[] = [
  { id: 'knowledge-l1', name: '公开制度知识库', type: 'knowledge', endpoint_ref: 'mock://kb/l1', data_level: 'L1', status: 'active', description: '公开制度与 FAQ（虚构内容）' },
  { id: 'knowledge-l2', name: '内部流程知识库', type: 'knowledge', endpoint_ref: 'mock://kb/l2', data_level: 'L2', status: 'active', description: '部门内部流程文档（虚构内容）' },
  { id: 'hr-employee-mcp', name: '员工查询 MCP', type: 'mcp', endpoint_ref: 'mock://mcp/hr-employee', data_level: 'L2', status: 'active', description: '查询员工基本信息（Mock）' },
  { id: 'adp-onboarding', name: '入职流程 Workflow', type: 'workflow', endpoint_ref: 'mock://adp/onboarding', data_level: 'L2', status: 'active', description: 'ADP 入职流程（Mock）' },
  { id: 'internet-search', name: '公网搜索', type: 'http', endpoint_ref: 'mock://http/internet-search', data_level: 'L1', status: 'active', description: '公网搜索插件（Mock，演示互联网禁止策略）' },
  { id: 'rpa-report', name: '报表机器人', type: 'rpa', endpoint_ref: 'mock://rpa/report', data_level: 'L3', status: 'active', description: '自动生成报表（Mock，敏感）' },
];

export const mockPolicies: Policy[] = [
  { id: 'POLICY-003', name: '禁网员工禁止公网插件', effect: 'deny', description: 'subject.internet=deny 且 resource.type=http 时拒绝', enabled: true, priority: 100 },
  { id: 'P-DATA-003', name: '敏感数据禁止', effect: 'deny', description: 'resource.data_level=L3 且 action=read 时拒绝', enabled: true, priority: 90 },
  { id: 'POLICY-004', name: '仅远程 Sandbox', effect: 'deny', description: 'subject.location=remote 且请求本地执行时拒绝', enabled: true, priority: 80 },
  { id: 'POLICY-002', name: '实习生禁止内部知识', effect: 'deny', description: '实习生数字分身访问内部知识库时拒绝', enabled: true, priority: 70 },
  { id: 'POLICY-001', name: '正式分身可读内部知识', effect: 'allow', description: '正式员工数字分身可访问内部知识库', enabled: true, priority: 60 },
  { id: 'POLICY-005', name: '敏感操作需审批', effect: 'approval', description: 'resource.data_level=L3 且动作属 execute/export/delete/approve 时需人工审批', enabled: true, priority: 50 },
  { id: 'P-PLUGIN-007', name: '入职助手可用 ADP', effect: 'allow', description: 'VE-0001 可执行 adp-onboarding', enabled: true, priority: 10 },
  { id: 'P-DEFAULT-001', name: '默认 L1 可读', effect: 'allow', description: '所有数字员工可读取 L1 知识', enabled: true, priority: 1 },
];

export const mockAudit: AuditEvent[] = [
  { id: 1, trace_id: 'T-DEMO-001', ts: '2026-08-17T10:00:00Z', actor: 'DT-E10281', employee_id: 'DT-E10281', team_id: null, plugin_id: 'knowledge-l2', knowledge_base_id: 'KB-INTERNAL', action: 'read', decision: 'allow', reason: 'POLICY-001：正式员工分身 L2 已授权', result_summary: '返回 2 条制度摘要（虚构）' },
  { id: 2, trace_id: 'T-DEMO-001', ts: '2026-08-17T10:00:01Z', actor: 'DT-E10281', employee_id: 'DT-E10281', team_id: null, plugin_id: 'rpa-report', knowledge_base_id: null, action: 'execute', decision: 'deny', reason: '插件授权为 deny：rpa-report', result_summary: null },
  { id: 3, trace_id: 'T-DEMO-002', ts: '2026-08-17T10:05:00Z', actor: 'DT-E20999', employee_id: 'DT-E20999', team_id: null, plugin_id: 'knowledge-l2', knowledge_base_id: 'KB-INTERNAL', action: 'read', decision: 'deny', reason: 'POLICY-002：实习生不可访问内部知识库', result_summary: null },
];

export const mockTeams: Team[] = [
  { id: 'TEAM-ONBOARD', name: '新员工入职团队', leader_employee_id: 'VE-0001', description: 'P0-lite 团队：处理新员工入职准备任务' },
];

export const mockTeamDetail: TeamDetail = {
  id: 'TEAM-ONBOARD',
  name: '新员工入职团队',
  leader_employee_id: 'VE-0001',
  description: 'P0-lite 团队：处理新员工入职准备任务',
  members: [
    { employee_id: 'VE-0001', role: 'leader' },
    { employee_id: 'VE-0002', role: 'worker' },
    { employee_id: 'VE-0003', role: 'worker' },
  ],
};

export const mockKnowledgeBases: KnowledgeBase[] = [
  { id: 'KB-PUBLIC', name: '公共知识', level: 'L1', data_level: 'L1', resource_type: 'knowledge', allowed_employment_type: ['formal', 'intern'], department_scope: ['*'], domain: '通用', description: '公开制度、FAQ、培训材料（虚构）', status: 'active', doc_path: 'mock-data/kb/kb-l1-pub.md' },
  { id: 'KB-ONBOARD', name: '入职 Demo 知识库', level: 'L1', data_level: 'L1', resource_type: 'knowledge', allowed_employment_type: ['formal', 'intern'], department_scope: ['*'], domain: '入职', description: '新员工入职流程（虚构）', status: 'active', doc_path: 'mock-data/kb/kb-onboarding.md' },
  { id: 'KB-INTERNAL', name: '正式员工内部知识库', level: 'L2', data_level: 'L2', resource_type: 'knowledge', allowed_employment_type: ['formal'], department_scope: ['*'], domain: '综合', description: '正式员工内部制度与流程（虚构）', status: 'active', doc_path: 'mock-data/kb/kb-l2-hr.md' },
  { id: 'KB-FINTECH', name: '金融科技部门知识库', level: 'L2', data_level: 'L2', resource_type: 'knowledge', allowed_employment_type: ['formal'], department_scope: ['金融科技部'], domain: '金融科技', description: '金融科技部门专属流程（虚构）', status: 'active', doc_path: 'mock-data/kb/kb-l2-fin.md' },
];
