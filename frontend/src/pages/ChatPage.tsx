import { useEffect, useRef, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import {
  AppstoreOutlined,
  ArrowLeftOutlined,
  BookOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  RobotOutlined,
  SafetyCertificateOutlined,
  SendOutlined,
  StopOutlined,
} from '@ant-design/icons';
import { Avatar, Badge, Button, Card, Empty, Input, Space, Spin, Tag, Typography } from 'antd';
import type { ChatReply, Workspace } from '@dwp/shared-schema';
import { api } from '../api/client';
import MarkdownText from '../components/MarkdownText';

const { Text, Paragraph } = Typography;

const ROLE_META: Record<string, { label: string; grad: string; color: string; emoji: string }> = {
  formal_twin: { label: '正式员工 · 数字分身', grad: 'linear-gradient(135deg,#2f54eb 0%,#1d39c4 100%)', color: '#2f54eb', emoji: '⭐' },
  intern_twin: { label: '实习生 · 数字分身', grad: 'linear-gradient(135deg,#13a8a8 0%,#0e7a7a 100%)', color: '#13a8a8', emoji: '🌱' },
  virtual: { label: '虚拟员工', grad: 'linear-gradient(135deg,#722ed1 0%,#531dab 100%)', color: '#722ed1', emoji: '🤖' },
  rpa: { label: 'RPA 自动化', grad: 'linear-gradient(135deg,#fa8c16 0%,#d46b08 100%)', color: '#fa8c16', emoji: '⚙️' },
};

function roleKey(w: Workspace): string {
  const e = w.employee;
  if (e.type === 'twin') return `${e.employment_type === 'intern' ? 'intern' : 'formal'}_twin`;
  return e.type;
}

const DECISION_TAG: Record<string, { color: string; text: string }> = {
  allow: { color: 'success', text: '允许' },
  deny: { color: 'error', text: '拒绝' },
  approval: { color: 'warning', text: '需审批' },
};

interface Msg {
  role: 'user' | 'assistant';
  content: string;
  cards?: ChatReply['tool_cards'];
  denied?: ChatReply['policy_denied'];
  error?: boolean;
}

export default function ChatPage() {
  const { employeeNo = '' } = useParams();
  const [workspace, setWorkspace] = useState<Workspace | null>(null);
  const [loadingWs, setLoadingWs] = useState(true);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [input, setInput] = useState('');
  const [sending, setSending] = useState(false);
  const [messages, setMessages] = useState<Msg[]>([]);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    api
      .getWorkspace(employeeNo)
      .then(setWorkspace)
      .finally(() => setLoadingWs(false));
  }, [employeeNo]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, sending]);

  const send = async () => {
    const text = input.trim();
    if (!text || sending) return;
    setInput('');
    setMessages((prev) => [...prev, { role: 'user', content: text }]);
    setSending(true);
    try {
      const reply: ChatReply = await api.chat(employeeNo, text, sessionId ?? undefined);
      setSessionId(reply.session_id);
      setMessages((prev) => [
        ...prev,
        {
          role: 'assistant',
          content: reply.message,
          cards: reply.tool_cards,
          denied: reply.policy_denied,
        },
      ]);
    } catch (err) {
      setMessages((prev) => [...prev, { role: 'assistant', content: `（请求失败：${(err as Error).message}）`, error: true }]);
    } finally {
      setSending(false);
    }
  };

  if (loadingWs) {
    return (
      <div style={{ display: 'flex', justifyContent: 'center', padding: 80 }}>
        <Spin size="large" />
      </div>
    );
  }
  if (!workspace) {
    return (
      <Card>
        <Empty description="未找到该数字员工" />
        <Link to="/employees">返回员工列表</Link>
      </Card>
    );
  }

  const meta = ROLE_META[roleKey(workspace)] ?? ROLE_META.virtual;
  const emp = workspace.employee;
  const security = workspace.security;

  return (
    <div className="chat-page">
      {/* 左栏：工作台面板 */}
      <aside className="ws-panel">
        <Card
          className="ws-identity"
          styles={{ body: { padding: 0 } }}
        >
          <div className="ws-banner" style={{ background: meta.grad }}>
            <Avatar size={54} icon={<RobotOutlined />} style={{ background: 'rgba(255,255,255,0.22)', border: '1.5px solid rgba(255,255,255,0.7)' }} />
            <div className="ws-banner-text">
              <div className="ws-name">{emp.name}</div>
              <div className="ws-sub">{emp.employee_no} · {meta.label}</div>
            </div>
            <div className="ws-emoji">{meta.emoji}</div>
          </div>
          <div className="ws-body">
            <div className="ws-field"><span>部门</span><b>{emp.department || '—'}</b></div>
            <div className="ws-field"><span>Owner</span><b>{emp.owner_human_no}</b></div>
            <div className="ws-field"><span>状态</span><Badge status="processing" text="运行中" /></div>
          </div>
        </Card>

        <Card className="ws-card" title={<Space><RobotOutlined />人设</Space>} size="small">
          <Paragraph className="ws-persona">{workspace.role_prompt || '（未配置人设）'}</Paragraph>
        </Card>

        <Card className="ws-card" title={<Space><AppstoreOutlined />插件授权（{workspace.plugins.length}）</Space>} size="small">
          <div className="ws-list">
            {workspace.plugins.map((p) => (
              <div className="ws-plugin" key={p.plugin_id}>
                <div className="ws-plugin-name">{p.name}</div>
                <Tag color={DECISION_TAG[p.decision_mode]?.color ?? 'default'}>{DECISION_TAG[p.decision_mode]?.text ?? p.decision_mode}</Tag>
                <Text type="secondary" className="ws-plugin-meta">{p.type} · {p.data_level}</Text>
              </div>
            ))}
            {workspace.plugins.length === 0 && <Text type="secondary">暂无插件授权</Text>}
          </div>
        </Card>

        <Card className="ws-card" title={<Space><BookOutlined />知识库权限（{workspace.knowledge_bases.length}）</Space>} size="small">
          <div className="ws-list">
            {workspace.knowledge_bases.map((kb) => (
              <div className="ws-kb" key={kb.knowledge_base_id}>
                {kb.accessible ? (
                  <CheckCircleOutlined className="kb-ok" />
                ) : (
                  <CloseCircleOutlined className="kb-no" />
                )}
                <div className="ws-kb-text">
                  <div className="ws-plugin-name">{kb.name}</div>
                  <Text type="secondary" className="ws-plugin-meta">{kb.data_level} · {kb.accessible ? '可访问' : `${kb.decision}`}</Text>
                </div>
              </div>
            ))}
          </div>
        </Card>

        <Card className="ws-card" title={<Space><SafetyCertificateOutlined />安全策略</Space>} size="small">
          <div className="ws-sec">
            <Tag color="blue">{security.location === 'remote' ? '仅远程 Sandbox' : '本地运行'}</Tag>
            <Tag color={security.internet === 'deny' ? 'red' : 'green'}>{security.internet === 'deny' ? '禁网' : '允许联网'}</Tag>
            <Tag color="purple">数据上限 {security.max_data_level}</Tag>
          </div>
        </Card>
      </aside>

      {/* 右栏：聊天区 */}
      <main className="chat-main">
        <div className="chat-header">
          <Link to={`/employees/${emp.employee_no}`}>
            <Button type="text" icon={<ArrowLeftOutlined />} aria-label="返回详情" />
          </Link>
          <Avatar size={36} icon={<RobotOutlined />} style={{ background: meta.color }} />
          <div>
            <div className="chat-title">{emp.name} · 智能助手</div>
            <Text type="secondary" style={{ fontSize: 12 }}>会话 ID：{sessionId ?? '新会话'}</Text>
          </div>
        </div>

        <div className="chat-scroll">
          {messages.length === 0 && (
            <div className="chat-empty">
              <div className="chat-empty-emoji">{meta.emoji}</div>
              <Paragraph>你好，我是{emp.name}。你可以问我入职、制度、IT 或业务问题。</Paragraph>
              <Text type="secondary">所有回答均为虚构演示数据 · 检索经 Policy → Gateway → RAG</Text>
            </div>
          )}
          {messages.map((m, i) => (
            <div key={i} className={`msg ${m.role}`}>
              {m.role === 'assistant' && (
                <Avatar size={30} icon={<RobotOutlined />} style={{ background: meta.color, flexShrink: 0 }} />
              )}
              <div className="msg-col">
                <div className={`bubble ${m.error ? 'bubble-error' : ''}`}>
                  <MarkdownText text={m.content} />
                </div>
                {m.cards && m.cards.length > 0 && (
                  <div className="tool-cards">
                    {m.cards.map((c, j) => (
                      <div className={`tool-card ${c.decision}`} key={j}>
                        <div className="tool-card-head">
                          <span>{c.name}</span>
                          <Tag color={DECISION_TAG[c.decision]?.color ?? 'default'}>{DECISION_TAG[c.decision]?.text ?? c.decision}</Tag>
                        </div>
                        {c.policy_id && <div className="tool-card-sub">策略 {c.policy_id}</div>}
                      </div>
                    ))}
                  </div>
                )}
                {m.denied && (
                  <div className="deny-card">
                    <StopOutlined />
                    <div>
                      <div className="deny-title">Policy Denied · 无权访问</div>
                      <div className="deny-sub">策略 {m.denied.policy_id ?? '未授权'}：{m.denied.reason ?? '默认拒绝'}</div>
                    </div>
                  </div>
                )}
              </div>
            </div>
          ))}
          {sending && (
            <div className="msg assistant">
              <Avatar size={30} icon={<RobotOutlined />} style={{ background: meta.color }} />
              <div className="typing"><span /><span /><span /></div>
            </div>
          )}
          <div ref={bottomRef} />
        </div>

        <div className="chat-input">
          <Input.TextArea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onPressEnter={(e) => {
              if (!e.shiftKey) {
                e.preventDefault();
                void send();
              }
            }}
            placeholder="输入你的问题，Enter 发送，Shift+Enter 换行"
            autoSize={{ minRows: 1, maxRows: 4 }}
          />
          <Button type="primary" icon={<SendOutlined />} onClick={() => void send()} loading={sending}>
            发送
          </Button>
        </div>
      </main>
    </div>
  );
}
