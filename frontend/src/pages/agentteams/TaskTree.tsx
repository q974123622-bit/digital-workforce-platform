import { RobotOutlined, ToolOutlined } from '@ant-design/icons';
import { Empty, Progress, Tag, Typography } from 'antd';
import type { AgentMessage, AgentTaskEvent, AgentTaskGraph, AgentTaskNode } from '@dwp/shared-schema';

const STATUS_META: Record<string, { label: string; color: string }> = {
  planning: { label: '规划中', color: 'blue' },
  blocked: { label: '已阻塞', color: 'orange' },
  assigned: { label: '已分派', color: 'cyan' },
  accepted: { label: '已接单', color: 'processing' },
  running: { label: '执行中', color: 'processing' },
  submitted: { label: '结果已提交', color: 'geekblue' },
  reviewing: { label: '待 Leader 验收', color: 'purple' },
  revision_requested: { label: '已要求修订', color: 'volcano' },
  waiting_children: { label: '等待子任务', color: 'gold' },
  summarizing: { label: '汇总中', color: 'purple' },
  completed: { label: '已完成', color: 'success' },
  failed: { label: '失败', color: 'error' },
  timed_out: { label: '已超时', color: 'warning' },
  cancelled: { label: '已取消', color: 'default' },
};

const NODE_KIND_LABEL: Record<string, string> = {
  mission: 'Manager 任务',
  team_assignment: 'Team Leader 派单',
  worker_task: 'Worker 任务',
};

interface Props {
  nodes: AgentTaskNode[];
  graph?: AgentTaskGraph | null;
  events?: AgentTaskEvent[];
  messages?: AgentMessage[];
  selectedId?: string | null;
  onSelect: (taskId: string) => void;
}

const AGENT_LABELS: Record<string, string> = {
  platform: '平台Gateway',
  'VE-0001': 'Manager Agent',
  'VE-0002': 'HR Agent',
  'VE-0003': 'IT Agent',
  'VE-0005': '审计Agent',
};

const TOOL_LABELS: Record<string, string> = {
  query_onboarding_employee: '查询员工入职信息',
  query_it_service_status: '查询IT账号与设备状态',
  query_audit_evidence: '查询审计证据',
  'AgentTeams HR 入职材料': 'HR入职材料查询Tool',
  'AgentTeams IT 就绪检查': 'IT账号与设备就绪检查Tool',
  'AgentTeams 入职审计证据': '入职审计证据复核Tool',
  'agentteams.hr.lookup_role_change': '岗位调整HR记录查询Tool',
  'agentteams.it.lookup_permission_plan': '岗位调整IT权限计划查询Tool',
  'agentteams.audit.review_role_change': '岗位调整权限审计复核Tool',
  'agentteams.it.activate_role_change': '岗位调整IT权限激活Tool',
  '岗位调整HR记录查询': '岗位调整HR记录查询Tool',
  '岗位调整IT权限计划查询': '岗位调整IT权限计划查询Tool',
  '岗位调整权限审计复核': '岗位调整权限审计复核Tool',
  '岗位调整IT权限激活': '岗位调整IT权限激活Tool',
};

const payloadText = (message: AgentMessage | undefined, key: string) => {
  const value = message?.payload[key];
  return typeof value === 'string' ? value : '';
};

const agentLabel = (agentId: string) => {
  const name = AGENT_LABELS[agentId];
  return name ? `${name}（${agentId}）` : agentId;
};

const toolLabel = (toolName: string) => TOOL_LABELS[toolName] ?? (toolName.endsWith('Tool') ? toolName : `${toolName} Tool`);

const resultLabel = (message: AgentMessage | undefined) => {
  if (!message) return '等待Gateway返回';
  const decision = payloadText(message, 'decision');
  const decisionText = decision === 'allow' ? 'Gateway通过' : decision === 'deny' ? 'Gateway拒绝' : decision === 'failed' ? 'Gateway执行失败' : 'Gateway已返回';
  const auditIds = Array.isArray(message.payload.audit_ids)
    ? message.payload.audit_ids.filter((id): id is number => typeof id === 'number' && id > 0)
    : [];
  return auditIds.length > 0 ? `${decisionText} · 审计#${auditIds.join('、')}` : decisionText;
};

const displayProgress = (node: AgentTaskNode) => node.status === 'completed'
  ? 100
  : Math.max(0, Math.min(100, node.progress));

export function TaskTree({ nodes, graph, messages = [], selectedId, onSelect }: Props) {
  const nodeIds = new Set(nodes.map((node) => node.id));
  const children = new Map<string | null, AgentTaskNode[]>();
  const dependencyIdsByNode = new Map<string, string[]>();
  for (const edge of graph?.dependency_edges ?? []) {
    const taskId = edge.task_id;
    const dependsOnTaskId = edge.depends_on_task_id;
    if (taskId && dependsOnTaskId) {
      dependencyIdsByNode.set(taskId, [...(dependencyIdsByNode.get(taskId) ?? []), dependsOnTaskId]);
    }
  }

  for (const node of nodes) {
    const declaredParentId = node.parent_task_id && nodeIds.has(node.parent_task_id) ? node.parent_task_id : null;
    const dependencyIds = (dependencyIdsByNode.get(node.id) ?? []).filter((id) => nodeIds.has(id));
    // A direct Manager delegation stays attached to the Manager.  A later
    // task with one persisted predecessor is rendered under that predecessor
    // so the visual tree also exposes the real serial execution chain.
    const parentId = (
      declaredParentId
      && dependencyIds.length === 1
      && declaredParentId !== dependencyIds[0]
    ) ? dependencyIds[0] : declaredParentId;
    children.set(parentId, [...(children.get(parentId) ?? []), node]);
  }

  const renderNode = (node: AgentTaskNode) => {
    const status = STATUS_META[node.status] ?? { label: node.status, color: 'default' };
    const isRoot = !node.parent_task_id;
    const nested = children.get(node.id) ?? [];
    const dependencies = dependencyIdsByNode.get(node.id) ?? [];
    const nodeToolMessages = messages.filter((message) => message.task_id === node.id && message.message_type.startsWith('tool.'));
    const requests = nodeToolMessages.filter((message) => message.message_type === 'tool.request');
    const results = nodeToolMessages.filter((message) => message.message_type === 'tool.result');
    return (
      <li
        key={node.id}
        className={`at-chart-node ${isRoot ? 'at-chart-root' : ''} ${nested.length > 0 ? 'at-chart-branch' : ''}`}
      >
        <button
          type="button"
          className={`at-chart-node-card ${selectedId === node.id ? 'selected' : ''}`}
          aria-label={`选择任务 ${node.title}`}
          aria-pressed={selectedId === node.id}
          onClick={() => onSelect(node.id)}
        >
          <span className="at-chart-node-top">
            <span className="at-chart-title">
              {isRoot && <RobotOutlined aria-hidden="true" />}
              {isRoot ? `Manager：${node.title}` : node.title}
            </span>
            <Tag color={status.color} style={{ marginInlineEnd: 0 }}>{status.label}</Tag>
          </span>
          <span className="at-chart-meta">
            <span>{NODE_KIND_LABEL[node.node_kind] ?? '协作任务'}</span>
            <span>执行：{agentLabel(node.assignee_agent_id)}</span>
          </span>
          {node.revision_of_task_id && (
            <span className="at-chart-meta">修订自：{node.revision_of_task_id}</span>
          )}
          {node.status === 'blocked' && dependencies.length > 0 && (
            <span className="at-chart-meta">等待前置任务：{dependencies.join('、')}</span>
          )}
          <Progress percent={displayProgress(node)} size="small" showInfo={false} />
          {requests.length > 0 && (
            <span className="at-chart-tool-leaves">
              {requests.map((request, index) => {
                const result = results[index];
                const name = payloadText(request, 'tool_name') || payloadText(result, 'tool_name') || '未标识';
                return (
                  <span className="at-chart-tool-leaf" key={request.id}>
                    <ToolOutlined aria-hidden="true" />
                    {toolLabel(name)} · {resultLabel(result)}
                  </span>
                );
              })}
            </span>
          )}
        </button>
        {nested.length > 0 ? (
          <ul className="at-chart-children" data-parent-task-id={node.id}>
            {nested.map(renderNode)}
          </ul>
        ) : null}
      </li>
    );
  };

  const roots = children.get(null) ?? [];
  return (
    <section className="at-task-tree" aria-labelledby="task-tree-heading">
      <Typography.Title level={5} id="task-tree-heading">协作调用树</Typography.Title>
      {roots.length > 0 ? (
        <div className="at-task-chart-scroll">
          <ul className="at-task-chart" data-testid="agentteams-task-chart">{roots.map(renderNode)}</ul>
        </div>
      ) : (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="服务端尚未返回任务节点" />
      )}
    </section>
  );
}

export { STATUS_META as AGENT_TASK_STATUS_META };
