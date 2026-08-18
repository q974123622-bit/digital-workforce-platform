import { useEffect, useRef, useState } from 'react';
import { useParams, Link } from 'react-router-dom';
import { Alert, Button, Card, Input, Space, Spin, Tag, Typography } from 'antd';
import type { Employee } from '@dwp/shared-schema';
import { api } from '../api/client';
import type { ChatResponse } from '../api/client';

// 一条聊天消息（前端本地类型，含 tool_cards 与 policy_denied）
interface Msg {
  role: 'user' | 'assistant';
  content: string;
  tool_cards?: ChatResponse['tool_cards'];
  policy_denied?: ChatResponse['policy_denied'];
}

// 临时 Mock：后端未启动时返回，用于演示 tool_cards / policy_denied 的数据形状。
// TODO: 后端跑起来后删除整个 mockChat。
function mockChat(message: string): ChatResponse {
  const denied = message.includes('内部') || message.includes('敏感');
  return {
    session_id: 'S-MOCK',
    trace_id: 'T-MOCK',
    message: denied
      ? '抱歉，当前身份无权访问该资源。'
      : `（Mock 回复）收到你的问题："${message}"。这是后端未启动时的演示回答。`,
    tool_cards: denied
      ? [{ plugin_id: 'knowledge-l2', name: '内部流程知识库', decision: 'deny' }]
      : [{ plugin_id: 'knowledge-l1', name: '公开制度知识库', decision: 'allow' }],
    policy_denied: denied
      ? { policy_id: 'POLICY-002', reason: '实习生不可访问内部知识库', plugin_id: 'knowledge-l2' }
      : null,
  };
}

export default function Chat() {
  const { employeeNo } = useParams();
  const [employee, setEmployee] = useState<Employee | null>(null);
  const [messages, setMessages] = useState<Msg[]>([]);
  const [input, setInput] = useState('');
  const [sending, setSending] = useState(false);
  const [sessionId, setSessionId] = useState<string | undefined>();
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (employeeNo) {
      api.getEmployee(employeeNo).then(setEmployee).catch(() => {});
    }
  }, [employeeNo]);

  // 有新消息时自动滚到底部
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  const send = async () => {
    const text = input.trim();
    if (!text || !employeeNo || sending) return;
    setInput('');
    setSending(true);
    setMessages((prev) => [...prev, { role: 'user', content: text }]);
    try {
      let res: ChatResponse;
      try {
        res = await api.chat(employeeNo, { message: text, session_id: sessionId });
      } catch {
        res = mockChat(text); // 后端未启动 → 临时 Mock（后端起来后这里会走真实接口）
      }
      setSessionId(res.session_id);
      setMessages((prev) => [
        ...prev,
        {
          role: 'assistant',
          content: res.message,
          tool_cards: res.tool_cards,
          policy_denied: res.policy_denied,
        },
      ]);
    } finally {
      setSending(false);
    }
  };

  if (!employee) return <Spin />;

  return (
    <div style={{ maxWidth: 860, margin: '0 auto' }}>
      {/* 顶部身份栏：展示当前对话身份（对应 PRD 4.3） */}
      <Card size="small" style={{ marginBottom: 16 }}>
        <Space wrap>
          <Typography.Text strong>{employee.name}</Typography.Text>
          <Tag color="blue">{employee.employee_no}</Tag>
          <Tag>Runtime: {employee.runtime_type}</Tag>
          <Tag>{employee.location === 'remote' ? '远程沙箱' : '本地'} / Internet {employee.internet}</Tag>
          <Tag>数据等级 {employee.max_data_level}</Tag>
          <Link to={`/employees/${employee.employee_no}`}>返回详情</Link>
        </Space>
      </Card>

      {/* 消息列表 */}
      <div style={{ marginBottom: 16 }}>
        {messages.map((m, i) => (
          <div key={i} style={{ marginBottom: 12, textAlign: m.role === 'user' ? 'right' : 'left' }}>
            {m.role === 'user' ? (
              <Typography.Text style={{ background: '#e6f4ff', padding: '8px 12px', borderRadius: 8, display: 'inline-block' }}>
                {m.content}
              </Typography.Text>
            ) : (
              <div style={{ textAlign: 'left' }}>
                <Typography.Text style={{ display: 'inline-block' }}>{m.content}</Typography.Text>
                {/* 工具调用卡片 */}
                {m.tool_cards && m.tool_cards.length > 0 && (
                  <div style={{ marginTop: 8 }}>
                    {m.tool_cards.map((tc, j) => (
                      <Tag
                        key={j}
                        color={tc.decision === 'allow' ? 'green' : tc.decision === 'deny' ? 'red' : 'orange'}
                        style={{ marginBottom: 4 }}
                      >
                        调用插件：{tc.name}（{tc.decision}）
                      </Tag>
                    ))}
                  </div>
                )}
                {/* 策略拒绝卡片 */}
                {m.policy_denied && (
                  <Alert
                    style={{ marginTop: 8 }}
                    type="error"
                    showIcon
                    message="Policy Denied"
                    description={`策略 ${m.policy_denied.policy_id}：${m.policy_denied.reason}（插件 ${m.policy_denied.plugin_id}）`}
                  />
                )}
              </div>
            )}
          </div>
        ))}
        <div ref={bottomRef} />
      </div>

      {/* 输入区 */}
      <Space.Compact style={{ width: '100%' }}>
        <Input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onPressEnter={send}
          placeholder="输入问题，回车发送（试试问：查一下内部制度）"
          disabled={sending}
        />
        <Button type="primary" onClick={send} loading={sending}>
          发送
        </Button>
      </Space.Compact>
    </div>
  );
}
