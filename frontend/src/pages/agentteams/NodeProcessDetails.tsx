import { useState } from 'react';
import { RightOutlined } from '@ant-design/icons';
import { List, Space, Tag, Typography } from 'antd';
import type { AgentMessage, AgentTaskNode } from '@dwp/shared-schema';

interface Props {
  node: AgentTaskNode;
  messages: AgentMessage[];
  taskSummary?: string;
  rootSkillIds?: string[];
}

interface ProcessStep {
  key: string;
  title: string;
  description?: string;
  reasoning?: string;
  kind?: 'delegate' | 'review' | 'analysis' | 'execution' | 'result' | 'message';
}

const TOOL_LABELS: Record<string, string> = {
  query_onboarding_employee: '查询员工入职信息',
  query_it_service_status: '查询IT账号与设备状态',
  query_audit_evidence: '查询审计证据',
  'agentteams.hr.lookup_role_change': '岗位调整HR记录查询',
  'agentteams.it.lookup_permission_plan': '岗位调整IT权限计划查询',
  'agentteams.audit.review_role_change': '岗位调整权限审计复核',
  'agentteams.it.activate_role_change': '岗位调整IT权限激活',
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function messagePayload(message: AgentMessage): Record<string, unknown> {
  const payload = isRecord(message.payload) ? message.payload : {};
  return isRecord(payload.payload) ? payload.payload : payload;
}

function stringValue(payload: Record<string, unknown>, key: string) {
  const value = payload[key];
  return typeof value === 'string' ? value.trim() : '';
}

function numberValue(payload: Record<string, unknown>, key: string) {
  const value = payload[key];
  return typeof value === 'number' && Number.isFinite(value) ? value : 0;
}

function stringList(payload: Record<string, unknown>, key: string) {
  const value = payload[key];
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === 'string' && item.trim().length > 0)
    : [];
}

function objectList(payload: Record<string, unknown>, key: string) {
  const value = payload[key];
  return Array.isArray(value) ? value.filter(isRecord) : [];
}

function toolLabel(name: string) {
  return TOOL_LABELS[name] ?? name;
}

function traceText(payload: Record<string, unknown>) {
  return stringList(payload, 'reasoning_segments').map((item) => item.trim()).join('\n');
}

function gatewayResult(message: AgentMessage | undefined) {
  if (!message) return '等待Gateway返回';
  const payload = messagePayload(message);
  const decision = stringValue(payload, 'decision');
  const label = decision === 'allow'
    ? 'Gateway通过'
    : decision === 'deny'
      ? 'Gateway拒绝'
      : decision === 'failed'
        ? 'Gateway执行失败'
        : 'Gateway已返回';
  const auditIds = Array.isArray(payload.audit_ids)
    ? payload.audit_ids.filter((id): id is number => typeof id === 'number' && id > 0)
    : [];
  return auditIds.length > 0 ? `${label} · 审计#${auditIds.join('、')}` : label;
}

function findAssignment(node: AgentTaskNode, messages: AgentMessage[]) {
  for (const message of messages) {
    if (message.message_type !== 'task.delegate') continue;
    const subtasks = objectList(messagePayload(message), 'subtasks');
    const matched = subtasks.find((subtask) => {
      const clientTaskKey = stringValue(subtask, 'client_task_key');
      const title = stringValue(subtask, 'title');
      return Boolean(
        (node.client_task_key && clientTaskKey === node.client_task_key)
        || (title === node.title && stringValue(subtask, 'assignee_agent_id') === node.assignee_agent_id),
      );
    });
    if (matched) return matched;
  }
  return undefined;
}

function selectedSkills(node: AgentTaskNode, messages: AgentMessage[], rootSkillIds: string[]) {
  const ids = new Set(node.parent_task_id ? [] : rootSkillIds);
  for (const message of messages) {
    if (message.task_id !== node.id || message.message_type !== 'agent.message') continue;
    const payload = messagePayload(message);
    if (stringValue(payload, 'kind') !== 'skill.selected') continue;
    for (const id of stringList(payload, 'selected_skill_ids')) ids.add(id);
  }
  return [...ids];
}

function workerActions(node: AgentTaskNode, messages: AgentMessage[]) {
  const actions: ProcessStep[] = [];
  const nodeMessages = messages.filter((item) => item.task_id === node.id);
  const assignment = findAssignment(node, messages);
  const hasAssignmentMessage = nodeMessages.some((message) => message.message_type === 'task.assign');
  let skillStep: ProcessStep | undefined;
  let pendingToolDecision: ProcessStep | undefined;

  if (!hasAssignmentMessage && assignment) {
    actions.push({
      key: `${node.id}-assignment`,
      title: '接收 Manager 派发的任务',
      description: stringValue(assignment, 'instruction'),
      kind: 'message',
    });
  }

  for (const message of nodeMessages) {
    const payload = messagePayload(message);
    const kind = stringValue(payload, 'kind');
    if (message.message_type === 'task.assign') {
      actions.push({
        key: message.id,
        title: '接收 Manager 派发的任务',
        description: stringValue(payload, 'instruction') || (assignment ? stringValue(assignment, 'instruction') : ''),
        kind: 'message',
      });
      continue;
    }
    if (message.message_type === 'agent.message' && kind === 'skill.selected') {
      skillStep = {
        key: message.id,
        title: '第1轮：选择 Skill',
        description: stringList(payload, 'selected_skill_ids').join('、'),
        kind: 'analysis',
      };
      actions.push(skillStep);
      continue;
    }
    if (message.message_type === 'agent.message' && kind === 'decision.trace') {
      const reasoning = traceText(payload);
      const stage = stringValue(payload, 'stage');
      const iteration = numberValue(payload, 'iteration') || 1;
      const action = stringValue(payload, 'action');
      if (stage === 'skill_selection') {
        if (!skillStep) {
          skillStep = {
            key: message.id,
            title: `第${iteration}轮：选择 Skill`,
            kind: 'analysis',
          };
          actions.push(skillStep);
        }
        appendDecisionBasis(skillStep, reasoning);
        continue;
      }
      const step: ProcessStep = {
        key: message.id,
        title: action === 'tool'
          ? `第${iteration}轮：决定调用 Tool`
          : action === 'complete'
            ? `第${iteration}轮：决定形成节点结果`
            : `第${iteration}轮：形成下一步判断`,
        description: action === 'complete' ? '准备向 Manager 返回本节点结果。' : undefined,
        reasoning,
        kind: 'analysis',
      };
      actions.push(step);
      pendingToolDecision = action === 'tool' ? step : undefined;
      continue;
    }
    if (message.message_type === 'tool.request') {
      const name = stringValue(payload, 'tool_name') || '未标识工具';
      if (pendingToolDecision) {
        pendingToolDecision.description = `${toolLabel(name)} · 向 Gateway 发出工具请求。`;
        pendingToolDecision = undefined;
      } else {
        actions.push({
          key: message.id,
          title: `调用 Tool：${toolLabel(name)}`,
          description: '向 Gateway 发出工具请求。',
          kind: 'execution',
        });
      }
      continue;
    }
    if (message.message_type === 'tool.result') {
      const name = stringValue(payload, 'tool_name') || '工具执行';
      actions.push({ key: message.id, title: `Gateway 返回：${toolLabel(name)}`, description: gatewayResult(message), kind: 'result' });
      continue;
    }
    if (message.message_type === 'task.result') {
      actions.push({
        key: message.id,
        title: '向 Manager 返回节点结果',
        description: stringValue(payload, 'result'),
        kind: 'result',
      });
      continue;
    }
    if (message.message_type === 'agent.message') {
      const text = stringValue(payload, 'text');
      if (text) {
        actions.push({
          key: message.id,
          title: `协作沟通：${message.sender_agent_id} → ${message.receiver_agent_id}`,
          description: text,
          kind: 'message',
        });
      }
    }
  }
  return actions;
}

function appendDecisionBasis(step: ProcessStep, reasoning: string) {
  if (!reasoning) return;
  step.reasoning = step.reasoning ? `${step.reasoning}\n${reasoning}` : reasoning;
}

function managerSteps(node: AgentTaskNode, messages: AgentMessage[]) {
  const rootMessages = messages.filter((message) => message.task_id === node.id);
  const steps: ProcessStep[] = [];
  let skillStep: ProcessStep | undefined;
  let pendingDelegation: ProcessStep | undefined;

  for (const message of rootMessages) {
    const payload = messagePayload(message);
    const kind = stringValue(payload, 'kind');
    if (message.message_type === 'agent.message' && kind === 'skill.selected') {
      skillStep = {
        key: message.id,
        title: '第1轮：选择场景 Skill',
        description: stringList(payload, 'selected_skill_ids').join('、'),
        kind: 'analysis',
      };
      steps.push(skillStep);
      continue;
    }
    if (message.message_type === 'agent.message' && kind === 'decision.trace') {
      const reasoning = traceText(payload);
      const stage = stringValue(payload, 'stage');
      const iteration = numberValue(payload, 'iteration') || 1;
      const action = stringValue(payload, 'action');
      if (stage === 'skill_selection') {
        if (!skillStep) {
          skillStep = { key: message.id, title: `第${iteration}轮：选择场景 Skill`, kind: 'analysis' };
          steps.push(skillStep);
        }
        appendDecisionBasis(skillStep, reasoning);
        continue;
      }
      const step: ProcessStep = {
        key: message.id,
        title: action === 'delegate'
          ? `第${iteration}轮：派发子任务`
          : action === 'complete' || action === 'summarize'
            ? `第${iteration}轮：形成最终汇总`
            : `第${iteration}轮：形成下一步判断`,
        description: action === 'complete' || action === 'summarize' ? '证据齐全，进入最终汇总。' : undefined,
        reasoning,
        kind: action === 'delegate' ? 'delegate' : 'analysis',
      };
      steps.push(step);
      pendingDelegation = action === 'delegate' ? step : undefined;
      continue;
    }
    if (message.message_type === 'task.delegate') {
      const subtasks = objectList(payload, 'subtasks');
      const titles = subtasks.map((subtask) => stringValue(subtask, 'title')).filter(Boolean);
      const instructions = subtasks.map((subtask) => stringValue(subtask, 'instruction')).filter(Boolean);
      const detail = [titles.join('、'), instructions.join('；')].filter(Boolean).join('：');
      if (pendingDelegation) {
        pendingDelegation.description = detail;
        pendingDelegation = undefined;
      } else {
        const round = numberValue(payload, 'round') || 1;
        steps.push({ key: message.id, title: `第${round}轮：派发子任务`, description: detail, kind: 'delegate' });
      }
      continue;
    }
    if (message.message_type !== 'task.summary' || stringValue(payload, 'request') !== 'task.summary') continue;
    const round = numberValue(payload, 'current_round') || 1;
    const childCount = objectList(payload, 'child_results').length;
    steps.push({
      key: message.id,
      title: `接收第${round}轮子任务结果`,
      description: `已收到${childCount || '本轮'}项子任务结果，交由 Manager 继续判断。`,
      kind: 'review',
    });
  }
  return steps;
}

function nodeConclusion(node: AgentTaskNode, messages: AgentMessage[], taskSummary?: string) {
  const resultMessage = [...messages]
    .reverse()
    .find((message) => message.task_id === node.id && message.message_type === 'task.result');
  const messageResult = resultMessage ? stringValue(messagePayload(resultMessage), 'result') : '';
  const outputResult = stringValue(node.output_payload, 'result')
    || stringValue(node.output_payload, 'summary')
    || stringValue(node.output_payload, 'conclusion');
  return messageResult || outputResult || (!node.parent_task_id ? taskSummary?.trim() ?? '' : '');
}

function elapsedLabel(node: AgentTaskNode, messages: AgentMessage[]) {
  const timestamps = messages
    .filter((message) => message.task_id === node.id)
    .map((message) => Date.parse(message.created_at))
    .filter(Number.isFinite);
  if (timestamps.length < 2) return '0秒';
  const seconds = Math.max(0, Math.round((Math.max(...timestamps) - Math.min(...timestamps)) / 1000));
  const minutes = Math.floor(seconds / 60);
  const remainder = seconds % 60;
  if (minutes === 0) return `${seconds}秒`;
  return remainder === 0 ? `${minutes}分钟` : `${minutes}分钟${remainder}秒`;
}

function ProcessList({
  steps,
  manager,
  expandedStepKeys,
  onToggleStep,
}: {
  steps: ProcessStep[];
  manager: boolean;
  expandedStepKeys: Set<string>;
  onToggleStep: (key: string) => void;
}) {
  if (steps.length === 0) {
    return <Typography.Text type="secondary">当前节点暂无可公开的处理记录。</Typography.Text>;
  }
  return (
    <List
      size="small"
      className={`at-node-process-list${manager ? ' at-manager-process-list' : ''}`}
      dataSource={steps}
      renderItem={(step, index) => {
        const reasoningExpanded = Boolean(step.reasoning && expandedStepKeys.has(step.key));
        return (
          <List.Item className={`at-process-step${step.kind ? ` at-process-step-${step.kind}` : ''}`}>
            <span className="at-node-process-index">{index + 1}</span>
            <div className="at-process-step-content">
              {step.reasoning ? (
                <button
                  type="button"
                  className="at-process-step-toggle"
                  aria-expanded={reasoningExpanded}
                  aria-label={`${reasoningExpanded ? '收起' : '展开'}决策依据：${step.title}`}
                  onClick={() => onToggleStep(step.key)}
                >
                  <Typography.Text strong>{step.title}</Typography.Text>
                  <RightOutlined className="at-process-step-toggle-icon" aria-hidden rotate={reasoningExpanded ? 90 : 0} />
                </button>
              ) : (
                <Typography.Text strong>{step.title}</Typography.Text>
              )}
              {step.description && <Typography.Paragraph type="secondary">{step.description}</Typography.Paragraph>}
              {reasoningExpanded && step.reasoning && (
                <div className="at-process-reasoning">
                  <Typography.Text type="secondary" className="at-process-reasoning-label">决策依据</Typography.Text>
                  <Typography.Paragraph type="secondary">{step.reasoning}</Typography.Paragraph>
                </div>
              )}
            </div>
          </List.Item>
        );
      }}
    />
  );
}

export function NodeProcessDetails({ node, messages, taskSummary, rootSkillIds = [] }: Props) {
  const [expandedStepKeys, setExpandedStepKeys] = useState<Set<string>>(() => new Set());
  const isManager = !node.parent_task_id;
  const assignment = isManager ? undefined : findAssignment(node, messages);
  const skills = selectedSkills(node, messages, rootSkillIds);
  const capability = assignment ? stringValue(assignment, 'required_capability') : '';
  const instruction = assignment ? stringValue(assignment, 'instruction') : '';
  const steps = isManager ? managerSteps(node, messages) : workerActions(node, messages);
  const persistedConclusion = nodeConclusion(node, messages, taskSummary);
  const conclusion = isManager && persistedConclusion
    ? '已形成最终汇总，可在上方“最终汇总”区域核对。'
    : persistedConclusion;
  const duration = elapsedLabel(node, messages);
  const reasoningStepKeys = steps.filter((step) => step.reasoning).map((step) => step.key);
  const allReasoningExpanded = reasoningStepKeys.length > 0
    && reasoningStepKeys.every((key) => expandedStepKeys.has(key));

  const toggleAllReasoning = () => {
    setExpandedStepKeys(allReasoningExpanded ? new Set() : new Set(reasoningStepKeys));
  };

  const toggleStepReasoning = (key: string) => {
    setExpandedStepKeys((current) => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  return (
    <div className="at-node-process" data-testid="agentteams-node-process">
      <div className="at-node-process-section at-node-conclusion">
        <Typography.Text type="secondary">{isManager ? '汇总结果' : '节点结果'}</Typography.Text>
        <Typography.Paragraph>{conclusion || '当前尚无结果。'}</Typography.Paragraph>
      </div>
      <button
        type="button"
        className="at-process-toggle"
        aria-expanded={allReasoningExpanded}
        aria-label={`${allReasoningExpanded ? '收起' : '展开'}决策依据，用时${duration}`}
        onClick={toggleAllReasoning}
      >
        <span>用时 {duration}</span>
        <RightOutlined className="at-process-toggle-icon" aria-hidden rotate={allReasoningExpanded ? 90 : 0} />
      </button>
      <div className="at-process-expanded">
        <Typography.Text strong className="at-node-process-heading">
          {isManager ? 'Manager决策过程' : '节点处理过程'}
        </Typography.Text>
        {instruction && (
          <div className="at-node-process-section">
            <Typography.Text type="secondary">任务指令</Typography.Text>
            <Typography.Paragraph>{instruction}</Typography.Paragraph>
          </div>
        )}
        {(skills.length > 0 || capability) && (
          <div className="at-node-process-section">
            <Typography.Text type="secondary">使用能力</Typography.Text>
            <Space size={[4, 4]} wrap>
              {skills.map((skill) => <Tag color="blue" key={skill}>{skill}</Tag>)}
              {capability && <Tag color="cyan">{capability}</Tag>}
            </Space>
          </div>
        )}
        <div className="at-node-process-section">
          <Typography.Text type="secondary">{isManager ? '决策链条' : '完整处理链条'}</Typography.Text>
          <ProcessList
            steps={steps}
            manager={isManager}
            expandedStepKeys={expandedStepKeys}
            onToggleStep={toggleStepReasoning}
          />
        </div>
      </div>
    </div>
  );
}
