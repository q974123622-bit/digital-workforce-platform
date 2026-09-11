import { Alert, Card, Empty, List, Space, Tag, Timeline, Typography } from 'antd';
import type { AgentMessage, AgentTaskEvent } from '@dwp/shared-schema';

interface Props {
  events: AgentTaskEvent[];
  messages: AgentMessage[];
  newEventIds?: Set<number>;
  selectedTaskId?: string | null;
  isRealMatrix?: boolean;
}

const PLAIN_MATRIX_TYPES = new Set(['matrix.text', 'matrix.duplicate', 'matrix.terminal_late']);

function formatTime(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN', { hour12: false });
}

function safeText(value: string, limit = 280) {
  const redacted = value.replace(/\b(token|secret|password|passwd|api[_-]?key)\s*[:=]\s*[^\s,;]+/gi, '$1=[已隐藏]');
  return redacted.length > limit ? `${redacted.slice(0, limit)}…` : redacted;
}

function payloadString(payload: Record<string, unknown>, key: string) {
  return typeof payload[key] === 'string' ? payload[key] : '';
}

function auditIds(payload: Record<string, unknown>) {
  const values = Array.isArray(payload.audit_ids) ? payload.audit_ids : [];
  return values.filter((value): value is number => typeof value === 'number').slice(0, 8);
}

function positiveAuditIds(payload: Record<string, unknown>) {
  return auditIds(payload).filter((value) => Number.isInteger(value) && value > 0);
}

function nestedToolPayload(payload: Record<string, unknown>) {
  const nested = payload.payload;
  return nested && typeof nested === 'object' && !Array.isArray(nested)
    ? nested as Record<string, unknown>
    : payload;
}

interface ToolAuditRecord {
  id: string;
  request?: AgentMessage;
  result?: AgentMessage;
}

function pairToolMessages(messages: AgentMessage[]) {
  const records: ToolAuditRecord[] = [];
  const pendingByTask = new Map<string, ToolAuditRecord[]>();

  for (const message of messages) {
    if (message.message_type === 'tool.request') {
      const record = { id: message.id, request: message };
      records.push(record);
      const pending = pendingByTask.get(message.task_id) ?? [];
      pending.push(record);
      pendingByTask.set(message.task_id, pending);
      continue;
    }
    if (message.message_type !== 'tool.result') continue;

    const pending = pendingByTask.get(message.task_id) ?? [];
    const record = pending.shift();
    if (record) {
      record.result = message;
    } else {
      records.push({ id: message.id, result: message });
    }
  }

  return records;
}

function policyLabel(decision: string) {
  if (decision === 'allow') return '允许';
  if (decision === 'deny') return '拒绝';
  if (decision === 'failed') return '执行失败';
  return '未产生';
}

function isProtocolMessage(message: AgentMessage) {
  return !PLAIN_MATRIX_TYPES.has(message.message_type) && message.message_type !== 'protocol.invalid';
}

function messageSummary(message: AgentMessage) {
  if (message.message_type === 'agent.message') return safeText(message.body || '未提供正文');
  if (isProtocolMessage(message)) return '已接收结构化协议消息；未展开协议载荷。';
  return safeText(message.body || '未提供正文');
}

export function TimelinePanel({ events, messages, newEventIds = new Set(), selectedTaskId, isRealMatrix = false }: Props) {
  const visibleEvents = selectedTaskId ? events.filter((event) => event.task_id === selectedTaskId) : events;
  const visibleMessages = selectedTaskId ? messages.filter((message) => message.task_id === selectedTaskId) : messages;
  const toolAudits = pairToolMessages(visibleMessages);
  const protocolErrors = visibleEvents.filter((event) => event.event_type === 'protocol.invalid');

  return (
    <div className="at-timeline-panels">
      {protocolErrors.map((event) => (
        <Alert
          key={event.id}
          type="warning"
          showIcon
          message="协议消息未通过校验"
          description={event.summary}
          className="at-protocol-alert"
        />
      ))}

      <Card size="small" title="事件时间线" className="at-detail-card">
        {visibleEvents.length === 0 ? (
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当前节点暂无事件" />
        ) : (
          <Timeline
            items={visibleEvents.map((event) => ({
              color: event.event_type.includes('failed') || event.event_type === 'protocol.invalid' ? 'red' : 'blue',
              children: (
                <div className={`at-timeline-event ${newEventIds.has(event.id) ? 'at-new-evidence' : ''}`}>
                  <Space size={[6, 4]} wrap>
                    <Tag color="blue">#{event.event_seq}</Tag>
                    <Tag>{event.event_type}</Tag>
                    {newEventIds.has(event.id) && <Tag color="gold">新到证据</Tag>}
                    {event.event_type === 'skill.selected' && <Tag color="purple">DeepSeek 决策</Tag>}
                    <Typography.Text strong>{event.actor_agent_id}</Typography.Text>
                    {event.target_agent_id && <Typography.Text type="secondary">→ {event.target_agent_id}</Typography.Text>}
                  </Space>
                  <div>{event.summary}</div>
                  <Typography.Text type="secondary" className="at-time">{formatTime(event.created_at)}</Typography.Text>
                </div>
              ),
            }))}
          />
        )}
      </Card>

      <Card size="small" title="Matrix 消息日志" className="at-detail-card">
        {visibleMessages.length === 0 ? (
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当前节点暂无 Matrix 消息" />
        ) : (
          <List
            size="small"
            dataSource={visibleMessages}
            renderItem={(message) => (
              <List.Item className="at-message-row">
                <div className="at-message-content">
                  <Space size={[6, 4]} wrap>
                    <Tag color={isProtocolMessage(message) ? 'blue' : 'default'}>
                      {isProtocolMessage(message) ? '协议消息' : '普通讨论'}
                    </Tag>
                    <Tag>{message.message_type}</Tag>
                    <Typography.Text strong>{message.sender_agent_id}</Typography.Text>
                    <Typography.Text type="secondary">→ {message.receiver_agent_id}</Typography.Text>
                  </Space>
                  <Typography.Paragraph className="at-message-summary">{messageSummary(message)}</Typography.Paragraph>
                  <Typography.Text type="secondary" className="at-time">
                    {isRealMatrix && typeof message.matrix_event_id === 'string' && message.matrix_event_id.startsWith('$')
                      ? `Matrix事件：${message.matrix_event_id}`
                      : `Transport事件ID：${message.matrix_event_id ?? '尚未产生'}`} · {formatTime(message.created_at)}
                  </Typography.Text>
                </div>
              </List.Item>
            )}
          />
        )}
      </Card>

      <Card size="small" title="工具审计" className="at-detail-card">
        {toolAudits.length === 0 ? (
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当前节点暂无工具调用记录" />
        ) : (
          <List
            size="small"
            dataSource={toolAudits}
            renderItem={(audit) => {
              const requestPayload = audit.request ? nestedToolPayload(audit.request.payload) : {};
              const resultPayload = audit.result?.payload ?? {};
              const decision = payloadString(resultPayload, 'decision');
              const ids = positiveAuditIds(resultPayload);
              const toolName = payloadString(resultPayload, 'tool_name')
                || payloadString(requestPayload, 'tool_name')
                || payloadString(resultPayload, 'tool_type')
                || '未提供';
              const caller = audit.request?.sender_agent_id ?? audit.result?.receiver_agent_id ?? '未提供';
              const result = typeof resultPayload.data === 'string'
                ? safeText(resultPayload.data, 160)
                : resultPayload.truncated === true
                  ? '受控结果已截断'
                  : audit.result
                    ? '已返回受控结果摘要'
                    : '等待平台返回';
              return (
                <List.Item key={audit.id}>
                  <div className="at-tool-row">
                    <Typography.Text strong>工具调用：{toolName}</Typography.Text>
                    <Space size={[6, 4]} wrap>
                      <Tag>调用者：{caller}</Tag>
                      <Tag color={decision === 'allow' ? 'success' : decision === 'deny' ? 'error' : decision ? 'warning' : 'processing'}>
                        权限检查：{decision ? policyLabel(decision) : '等待检查'}
                      </Tag>
                      {ids.length > 0
                        ? ids.map((id) => <Tag key={id}>审计记录ID：{id}</Tag>)
                        : <Tag>审计记录ID：{audit.result ? '未产生' : '等待产生'}</Tag>}
                    </Space>
                    <Typography.Text type="secondary">结果摘要：{result}</Typography.Text>
                  </div>
                </List.Item>
              );
            }}
          />
        )}
      </Card>
    </div>
  );
}
