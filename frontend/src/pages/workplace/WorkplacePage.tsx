import { useCallback, useEffect, useMemo, useState } from 'react';
import type { ReactNode } from 'react';
import {
  CheckCircleOutlined,
  ClockCircleOutlined,
  DeleteOutlined,
  DownOutlined,
  ExperimentOutlined,
  PlusOutlined,
  RobotOutlined,
  SendOutlined,
  StopOutlined,
  TeamOutlined,
  ThunderboltOutlined,
  UpOutlined,
  UploadOutlined,
} from '@ant-design/icons';
import {
  Alert,
  Avatar,
  Button,
  Checkbox,
  Drawer,
  Empty,
  Form,
  Input,
  Modal,
  Popconfirm,
  Radio,
  Space,
  Switch,
  Tag,
  Typography,
  Upload,
  message,
} from 'antd';
import type {
  Conversation,
  ConversationSummary,
  Skill,
  TaskRun,
  Workflow,
  WorkplaceHome,
} from '@dwp/shared-schema';
import { api } from '../../api/client';
import { ErrorState, LoadingState } from '../../components/PageState';
import MarkdownText from '../../components/MarkdownText';
import { useCurrentUser } from '../../context/CurrentUserContext';
import { useAsyncData } from '../../hooks/useAsyncData';

const { Text, Paragraph } = Typography;

interface ContactMeta {
  label: string;
  emoji: string;
  color: string;
  bg: string;
}

const GROUP_META: Record<string, ContactMeta> = {
  twin: { label: '我的分身', emoji: '⭐', color: '#2f54eb', bg: '#eef4ff' },
  virtual: { label: '智能助理', emoji: '🤖', color: '#722ed1', bg: '#f9f0ff' },
  rpa: { label: '自动化小助手', emoji: '⚙️', color: '#fa8c16', bg: '#fff7e6' },
};

const PLUGIN_TYPE_LABEL: Record<string, string> = {
  knowledge: '知识库',
  mcp: 'MCP 查询',
  workflow: '流程',
  rpa: 'RPA',
  http: '公网搜索',
};

const DECISION_META: Record<string, { label: string; color: string }> = {
  allow: { label: '可用', color: 'success' },
  deny: { label: '已禁用', color: 'default' },
  approval: { label: '需审批', color: 'warning' },
};

const DATA_LEVEL_LABEL: Record<string, string> = {
  L1: '公开',
  L2: '内部',
  L3: '敏感',
};

const TASK_STATUS: Record<string, { label: string; color: string; icon: ReactNode }> = {
  pending: { label: '待执行', color: 'default', icon: <ClockCircleOutlined /> },
  running: { label: '执行中', color: 'processing', icon: <ExperimentOutlined /> },
  approval: { label: '待审批', color: 'warning', icon: <ClockCircleOutlined /> },
  completed: { label: '已完成', color: 'success', icon: <CheckCircleOutlined /> },
  denied: { label: '已拒绝', color: 'error', icon: <StopOutlined /> },
  failed: { label: '失败', color: 'error', icon: <StopOutlined /> },
};

const EXECUTION_MODE_LABEL: Record<string, string> = {
  harness: 'DeepSeek Harness',
  demo_adapter: 'Demo Adapter 降级',
  knowledge_adapter: 'Knowledge Adapter',
  pending: '待选择执行器',
  failed: '执行失败',
};

const TOOL_TYPE_LABEL: Record<string, string> = {
  mcp: 'MCP',
  workflow: 'Workflow',
  rpa: 'RPA',
  http: 'HTTP',
};

interface TaskCardProps {
  task: TaskRun;
  workerName: (employeeNo: string) => string;
  metaOf: (employeeNo: string) => ContactMeta;
  onApprove: (task: TaskRun, approve: boolean) => void;
  acting: boolean;
}

function TaskCard({ task, workerName, metaOf, onApprove, acting }: TaskCardProps) {
  const [expanded, setExpanded] = useState(task.status !== 'completed');
  const meta = TASK_STATUS[task.status] ?? TASK_STATUS.pending;
  return (
    <div className="wp-task-card">
      <div
        className="wp-task-head"
        role="button"
        tabIndex={0}
        onClick={() => setExpanded((value) => !value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            setExpanded((value) => !value);
          }
        }}
      >
        <Avatar size={30} style={{ background: '#eef4ff', color: '#2f54eb' }} icon={<RobotOutlined />} />
        <div style={{ minWidth: 0, flex: 1 }}>
          <div className="wp-task-title">{task.request}</div>
          <Text type="secondary" style={{ fontSize: 11 }}>
            {task.id} ·{' '}
            {task.source === 'agentteams'
              ? 'AgentTeams 团队协作 · Harness 驱动 · Policy/Gateway 工具调用'
              : '内置协作 · Harness 驱动 · Policy/Gateway 工具调用'}
          </Text>
        </div>
        <Space size={4}>
          <Tag icon={meta.icon} color={meta.color}>
            {meta.label}
          </Tag>
          <Button
            type="text"
            size="small"
            aria-label={expanded ? '收起任务详情' : '展开任务详情'}
            icon={expanded ? <UpOutlined /> : <DownOutlined />}
            onClick={(e) => {
              e.stopPropagation();
              setExpanded((value) => !value);
            }}
          />
        </Space>
      </div>
      {expanded ? (
        <>
          <div className="subtask-list" style={{ padding: '10px 12px 0' }}>
            {task.subtasks.map((sub, index) => {
              const subMeta = TASK_STATUS[sub.status] ?? TASK_STATUS.pending;
              const workerMeta = metaOf(sub.worker_id);
              const runtimeMode = sub.runtime_mode ?? sub.execution_mode;
              return (
                <div className={`subtask ${sub.status}`} key={`${sub.worker_no}-${index}`}>
                  <div className="subtask-top">
                    <Space size={8}>
                      <Avatar
                        size={24}
                        style={{ background: workerMeta.bg, color: workerMeta.color, fontSize: 12 }}
                      >
                        {workerMeta.emoji}
                      </Avatar>
                      <Text strong>{workerName(sub.worker_id)}</Text>
                      <Text type="secondary" style={{ fontSize: 12 }}>
                        {sub.summary}
                      </Text>
                    </Space>
                    <Space size={4}>
                      {runtimeMode && runtimeMode !== 'pending' && (
                        <Tag color={runtimeMode === 'harness' ? 'geekblue' : 'orange'}>
                          运行时：{EXECUTION_MODE_LABEL[runtimeMode] ?? runtimeMode}
                        </Tag>
                      )}
                      {sub.tool_name && (
                        <Tag color="cyan">
                          工具：{sub.tool_name}
                          {sub.tool_type && !sub.tool_name.toLowerCase().includes(sub.tool_type.toLowerCase())
                            ? ` · ${TOOL_TYPE_LABEL[sub.tool_type] ?? sub.tool_type}`
                            : ''}
                        </Tag>
                      )}
                      <Tag icon={subMeta.icon} color={subMeta.color}>
                        {subMeta.label}
                      </Tag>
                    </Space>
                  </div>
                  {sub.result && (
                    <Paragraph type="secondary" style={{ margin: '6px 0 0', fontSize: 12 }}>
                      {sub.result}
                    </Paragraph>
                  )}
                  {sub.runtime_summary && runtimeMode === 'harness' && (
                    <Paragraph type="secondary" style={{ margin: '4px 0 0', fontSize: 11 }}>
                      Harness 计划：{sub.runtime_summary}
                    </Paragraph>
                  )}
                  {(sub.collaboration_messages?.length ?? 0) > 0 && (
                    <div style={{ marginTop: 6 }}>
                      <Text type="secondary" style={{ fontSize: 11 }}>AgentTeams 协作记录</Text>
                      {sub.collaboration_messages?.map((item, messageIndex) => (
                        <Paragraph key={`${sub.worker_no}-collab-${messageIndex}`} style={{ margin: '2px 0', fontSize: 12 }}>
                          {item}
                        </Paragraph>
                      ))}
                    </div>
                  )}
                  {sub.approval && (
                    <Alert
                      type="warning"
                      showIcon
                      style={{ marginTop: 8 }}
                      message={`敏感操作需审批${sub.approval.policy_id ? `（${sub.approval.policy_id}）` : ''}`}
                    />
                  )}
                </div>
              );
            })}
          </div>
          {task.status === 'approval' && (
            <div className="approval-zone" style={{ margin: 10 }}>
              <Text strong style={{ marginRight: 12 }}>
                等待审批：
              </Text>
              <Space>
                <Button type="primary" loading={acting} onClick={() => onApprove(task, true)}>
                  批准
                </Button>
                <Button danger loading={acting} onClick={() => onApprove(task, false)}>
                  拒绝
                </Button>
              </Space>
            </div>
          )}
          {task.status === 'completed' && task.summary && (
            <div className="summary-card" style={{ margin: 10 }}>
              <Text strong>👑 Leader 汇总</Text>
              <Paragraph style={{ margin: '8px 0 0' }}>{task.summary}</Paragraph>
            </div>
          )}
        </>
      ) : (
        task.summary && <div className="wp-task-collapsed">{task.summary}</div>
      )}
    </div>
  );
}

const formatTime = (iso: string) => {
  const date = new Date(iso);
  const now = new Date();
  const sameDay = date.toDateString() === now.toDateString();
  return sameDay
    ? date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
    : date.toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' });
};

export default function WorkplacePage() {
  const { actor } = useCurrentUser();
  const actorNo = actor.employee_no;

  const homeFetcher = useCallback(() => api.getWorkplace(actorNo), [actorNo]);
  const { data: home, loading, error, reload } = useAsyncData<WorkplaceHome>(homeFetcher);

  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [convsLoading, setConvsLoading] = useState(true);
  const [workflows, setWorkflows] = useState<Workflow[]>([]);
  const [selectedWorkflow, setSelectedWorkflow] = useState<Workflow | null>(null);
  const refreshConversations = useCallback(() => {
    setConvsLoading(true);
    api
      .listConversations(actorNo)
      .then(setConversations)
      .catch(() => setConversations([]))
      .finally(() => setConvsLoading(false));
  }, [actorNo]);

  useEffect(() => {
    refreshConversations();
    api
      .listWorkflows()
      .then(setWorkflows)
      .catch(() => setWorkflows([]));
  }, [refreshConversations]);

  const [tab, setTab] = useState<'messages' | 'contacts' | 'workflows'>('messages');
  const [keyword, setKeyword] = useState('');
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [selected, setSelected] = useState<Conversation | null>(null);
  const [input, setInput] = useState('');
  const [sending, setSending] = useState(false);
  const [pendingAfterSeq, setPendingAfterSeq] = useState<number | null>(null);
  const pendingReply = pendingAfterSeq !== null;
  const [chatError, setChatError] = useState<string>();
  const [acting, setActing] = useState(false);

  const [newChatOpen, setNewChatOpen] = useState(false);
  const [chatMode, setChatMode] = useState<'direct' | 'group'>('direct');
  const [groupTitle, setGroupTitle] = useState('');
  const [selectedNos, setSelectedNos] = useState<string[]>([]);

  const [skillDrawerOpen, setSkillDrawerOpen] = useState(false);
  const [skillModalOpen, setSkillModalOpen] = useState(false);
  const [skillForm] = Form.useForm();

  const [addMemberOpen, setAddMemberOpen] = useState(false);
  const [memberNos, setMemberNos] = useState<string[]>([]);

  // 切换身份时重置会话选择
  useEffect(() => {
    setSelectedId(null);
    setSelected(null);
    setInput('');
    setPendingAfterSeq(null);
  }, [actorNo]);

  useEffect(() => {
    if (!selectedId) {
      setSelected(null);
      return;
    }
    let active = true;
    api
      .getConversation(selectedId)
      .then((conv) => {
        if (active) setSelected(conv);
      })
      .catch(() => {
        if (active) setSelected(null);
      });
    return () => {
      active = false;
    };
  }, [selectedId]);

  // 等待后台回复，或任务尚未进入 completed / approval / failed / denied 时持续轮询。
  const hasPollableTask = selected?.tasks.some((task) =>
    ['pending', 'parsing', 'running'].includes(task.status),
  );
  useEffect(() => {
    if ((!hasPollableTask && !pendingReply) || !selectedId) return;

    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const poll = async () => {
      try {
        const conv = await api.getConversation(selectedId);
        if (cancelled) return;
        setSelected(conv);
        const hasMatchingReply =
          pendingAfterSeq != null &&
          conv.messages.some((m) => m.role === 'assistant' && m.seq > pendingAfterSeq);
        // trigger_message_seq 是前端在拿到 Task ID 前关联本次消息和任务卡片的稳定键。
        const matchingTask =
          pendingAfterSeq == null
            ? undefined
            : conv.tasks.find((task) => task.trigger_message_seq === pendingAfterSeq);
        const responseAppeared = hasMatchingReply || matchingTask != null;
        if (responseAppeared) setPendingAfterSeq(null);
        await refreshConversations();

        const taskStillRunning = conv.tasks.some((task) =>
          ['pending', 'parsing', 'running'].includes(task.status),
        );
        const replyStillPending = pendingAfterSeq != null && !responseAppeared;
        if (!cancelled && (taskStillRunning || replyStillPending)) {
          timer = setTimeout(poll, 2500);
        }
      } catch {
        // 临时网络失败不终止轮询；保留当前状态并继续重试。
        if (!cancelled) timer = setTimeout(poll, 2500);
      }
    };

    timer = setTimeout(poll, 2500);
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [hasPollableTask, pendingReply, pendingAfterSeq, selectedId, refreshConversations]);

  const twin = home?.twin ?? null;
  const employees = home?.available_employees ?? [];
  const skills = home?.skills ?? [];

  const metaOf = useCallback(
    (employeeNo: string): ContactMeta => {
      if (twin && employeeNo === twin.employee_no) return GROUP_META.twin;
      const emp = employees.find((e) => e.employee_no === employeeNo);
      if (emp?.type === 'rpa') return GROUP_META.rpa;
      if (emp?.type === 'virtual') return GROUP_META.virtual;
      return { label: employeeNo, emoji: '👤', color: '#13c2c2', bg: '#e6fffb' };
    },
    [twin, employees],
  );

  const contactGroups = useMemo(() => {
    if (!home) return [];
    return [
      { key: 'twin', label: '我的分身', items: [home.twin] },
      { key: 'virtual', label: '智能助理', items: home.available_employees.filter((e) => e.type === 'virtual') },
      { key: 'rpa', label: '自动化小助手', items: home.available_employees.filter((e) => e.type === 'rpa') },
    ]
      .map((group) => ({
        ...group,
        items: group.items.filter(
          (emp) =>
            !keyword ||
            emp.name.includes(keyword) ||
            emp.department.includes(keyword) ||
            emp.role_prompt.includes(keyword),
        ),
      }))
      .filter((group) => group.items.length > 0);
  }, [home, keyword]);

  const twinConv = useMemo(
    () =>
      twin
        ? conversations.find(
            (conv) => conv.kind === 'direct' && conv.participants.some((p) => p.employee_no === twin.employee_no),
          )
        : undefined,
    [conversations, twin],
  );
  const otherConvs = useMemo(() => conversations.filter((conv) => conv.id !== twinConv?.id), [conversations, twinConv]);
  const visibleConvs = useMemo(
    () =>
      keyword
        ? otherConvs.filter((conv) => {
            const name = conv.kind === 'group' ? conv.title : conv.participants[0]?.name ?? '';
            return name.includes(keyword) || conv.last_message.includes(keyword);
          })
        : otherConvs,
    [keyword, otherConvs],
  );
  const visibleWorkflows = useMemo(
    () =>
      keyword
        ? workflows.filter((wf) => wf.name.includes(keyword) || wf.description.includes(keyword))
        : workflows,
    [keyword, workflows],
  );

  const copyDemoPrompt = async (workflow: Workflow) => {
    try {
      await navigator.clipboard.writeText(workflow.demo_prompt);
      message.success('示例指令已复制，去群聊粘贴试试');
    } catch {
      message.warning('复制失败，请手动选择示例指令');
    }
  };

  const openDirect = async (employeeNo: string) => {
    const existing = conversations.find(
      (conv) => conv.kind === 'direct' && conv.participants.some((p) => p.employee_no === employeeNo),
    );
    if (existing) {
      setSelectedId(existing.id);
      return;
    }
    try {
      const conv = await api.createConversation({
        actor_no: actorNo,
        kind: 'direct',
        participant_employee_nos: [employeeNo],
      });
      await refreshConversations();
      setSelectedId(conv.id);
    } catch (err) {
      message.error(`发起会话失败：${(err as Error).message}`);
    }
  };

  const send = async () => {
    const text = input.trim();
    if (!text || sending || !selectedId) return;
    setInput('');
    setSending(true);
    setChatError(undefined);
    // 先本地渲染自己的消息，成员思考期间立即可见；回复到达后用服务端会话整体替换
    setSelected((prev) => {
      if (!prev) return prev;
      const lastSeq = prev.messages.reduce((max, m) => Math.max(max, m.seq), 0);
      return {
        ...prev,
        messages: [
          ...prev.messages,
          {
            id: -Date.now(),
            conversation_id: prev.id,
            participant_no: actorNo,
            participant_name: actor.name,
            role: 'user',
            content: text,
            tool_cards: [],
            seq: lastSeq + 1,
          },
        ],
      };
    });
    try {
      const conv = await api.sendConversationMessage(selectedId, actorNo, text);
      setSelected(conv);
      const triggerSeq = conv.messages
        .filter((m) => m.role === 'user')
        .reduce((max, m) => Math.max(max, m.seq), 0);
      setPendingAfterSeq(triggerSeq || null);
      await refreshConversations();
    } catch (err) {
      setInput(text);
      setChatError((err as Error).message);
      // 发送失败：撤掉本地临时消息，避免与恢复在输入框的文本重复
      setSelected((prev) =>
        prev ? { ...prev, messages: prev.messages.filter((m) => m.id >= 0) } : prev,
      );
    } finally {
      setSending(false);
    }
  };

  const workerName = (employeeNo: string) => {
    const participant = selected?.participants.find((p) => p.employee_no === employeeNo);
    if (participant) return participant.name;
    if (twin && employeeNo === twin.employee_no) return twin.name;
    const emp = employees.find((e) => e.employee_no === employeeNo);
    return emp?.name ?? employeeNo;
  };

  const handleApprove = async (task: TaskRun, approve: boolean) => {
    if (!selectedId || acting) return;
    setActing(true);
    try {
      await api.approveTask(task.id, approve, actorNo);
      const conv = await api.getConversation(selectedId);
      setSelected(conv);
      await refreshConversations();
    } catch (err) {
      message.error(`审批失败：${(err as Error).message}`);
    } finally {
      setActing(false);
    }
  };

  const handleClear = async () => {
    if (!selectedId) return;
    try {
      await api.clearConversation(selectedId, actorNo);
      setPendingAfterSeq(null);
      const conv = await api.getConversation(selectedId);
      setSelected(conv);
      await refreshConversations();
    } catch (err) {
      message.error(`清空失败：${(err as Error).message}`);
    }
  };

  const submitNewChat = async () => {
    if (chatMode === 'direct' && selectedNos.length !== 1) {
      message.warning('请选择 1 位联系人');
      return;
    }
    if (chatMode === 'group' && selectedNos.length === 0) {
      message.warning('请至少选择 1 位数字员工');
      return;
    }
    try {
      const conv = await api.createConversation({
        actor_no: actorNo,
        kind: chatMode,
        title: chatMode === 'group' && groupTitle.trim() ? groupTitle.trim() : undefined,
        participant_employee_nos: selectedNos,
      });
      setNewChatOpen(false);
      setSelectedNos([]);
      setGroupTitle('');
      await refreshConversations();
      setSelectedId(conv.id);
      setTab('messages');
    } catch (err) {
      message.error(`创建会话失败：${(err as Error).message}`);
    }
  };

  const submitSkill = async () => {
    const values = await skillForm.validateFields();
    try {
      await api.createSkill({
        actor_no: actorNo,
        name: values.name,
        description: values.description ?? '',
        content: values.content ?? '',
      });
      setSkillModalOpen(false);
      skillForm.resetFields();
      reload();
      message.success('技能已上传，我的分身已经学会了');
    } catch (err) {
      message.error(`上传失败：${(err as Error).message}`);
    }
  };

  const toggleSkill = async (skill: Skill, enabled: boolean) => {
    try {
      await api.updateSkill(skill.id, actorNo, { status: enabled ? 'active' : 'disabled' });
      reload();
    } catch (err) {
      message.error(`更新失败：${(err as Error).message}`);
    }
  };

  const removeSkill = async (skill: Skill) => {
    try {
      await api.deleteSkill(skill.id, actorNo);
      reload();
    } catch (err) {
      message.error(`删除失败：${(err as Error).message}`);
    }
  };

  const submitAddMembers = async () => {
    if (!selectedId) return;
    try {
      let current = selected;
      for (const no of memberNos) {
        current = await api.addConversationParticipant(selectedId, no);
      }
      setSelected(current);
      setAddMemberOpen(false);
      setMemberNos([]);
      await refreshConversations();
    } catch (err) {
      message.error(`添加成员失败：${(err as Error).message}`);
    }
  };

  const pickEmployee = (no: string) => {
    if (chatMode === 'direct') {
      setSelectedNos([no]);
      return;
    }
    setSelectedNos((prev) => (prev.includes(no) ? prev.filter((n) => n !== no) : [...prev, no]));
  };

  if (loading) return <LoadingState rows={8} />;
  if (error || !home || !home.actor || !home.twin || !Array.isArray(home.available_employees)) {
    return <ErrorState onRetry={reload} />;
  }

  const chatParticipants = selected?.participants ?? [];
  const isGroup = selected?.kind === 'group';
  const taskGroups = new Map<number, TaskRun[]>();
  const unattachedTasks: TaskRun[] = [];
  for (const task of selected?.tasks ?? []) {
    if (task.trigger_message_seq != null && selected?.messages.some((m) => m.seq === task.trigger_message_seq)) {
      const list = taskGroups.get(task.trigger_message_seq) ?? [];
      list.push(task);
      taskGroups.set(task.trigger_message_seq, list);
    } else {
      unattachedTasks.push(task);
    }
  }
  const selectedTitle =
    selected && (selected.kind === 'group' ? selected.title || '协作空间' : selected.participants[0]?.name ?? '');
  const selectedMeta = selected ? metaOf(selected.participants[0]?.employee_no ?? '') : GROUP_META.twin;
  const addableMembers = employees.filter(
    (emp) => !chatParticipants.some((p) => p.employee_no === emp.employee_no),
  );

  return (
    <div className="workplace">
      {/* 左栏：会话/通讯录 */}
      <aside className="wp-sidebar">
        <div className="wp-sidebar-head">
          <Space size={10}>
            <Avatar size={36} style={{ background: '#2f54eb' }}>
              {actor.name.slice(0, 1)}
            </Avatar>
            <div style={{ minWidth: 0 }}>
              <div className="wp-sidebar-title">我的职场</div>
              <Text type="secondary" style={{ fontSize: 12 }}>
                {actor.name} · {actor.department}
              </Text>
            </div>
          </Space>
            <Input
              allowClear
              placeholder={tab === 'messages' ? '搜索会话' : tab === 'contacts' ? '搜索联系人' : '搜索工作流'}
            value={keyword}
            onChange={(e) => setKeyword(e.target.value)}
            style={{ marginTop: 12 }}
          />
          <div className="wp-tabs">
            <Button
              type={tab === 'messages' ? 'primary' : 'text'}
              size="small"
              onClick={() => {
                setTab('messages');
                setKeyword('');
              }}
            >
              消息
            </Button>
            <Button
              type={tab === 'contacts' ? 'primary' : 'text'}
              size="small"
              onClick={() => {
                setTab('contacts');
                setKeyword('');
              }}
            >
              通讯录
            </Button>
            <Button
              type={tab === 'workflows' ? 'primary' : 'text'}
              size="small"
              onClick={() => {
                setTab('workflows');
                setKeyword('');
              }}
            >
              工作流
            </Button>
            <div style={{ flex: 1 }} />
            <Button
              type="primary"
              shape="circle"
              size="small"
              icon={<PlusOutlined />}
              aria-label="发起会话"
              onClick={() => setNewChatOpen(true)}
            />
          </div>
        </div>

        <div className="wp-list">
          {tab === 'messages' ? (
            <>
              {!keyword && twin && (
                <div
                  className="wp-row pinned"
                  role="button"
                  tabIndex={0}
                  onClick={() => void openDirect(twin.employee_no)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' || e.key === ' ') {
                      e.preventDefault();
                      void openDirect(twin.employee_no);
                    }
                  }}
                >
                  <Avatar size={42} style={{ background: GROUP_META.twin.bg, color: GROUP_META.twin.color }}>
                    {GROUP_META.twin.emoji}
                  </Avatar>
                  <div className="wp-row-main">
                    <div className="wp-row-top">
                      <span className="wp-row-name">{twin.name}</span>
                      {twinConv && <span className="wp-row-time">{formatTime(twinConv.updated_at)}</span>}
                    </div>
                    <div className="wp-row-preview">
                      {twinConv ? twinConv.last_message : '开始和我的分身聊聊吧'}
                    </div>
                  </div>
                  <Tag className="wp-pinned-tag" color="blue">
                    我的分身
                  </Tag>
                </div>
              )}
              {visibleConvs.length === 0 && !convsLoading && (
                <Empty
                  style={{ marginTop: 48 }}
                  description={keyword ? '没有找到相关会话' : '还没有会话，点 + 发起一个吧'}
                />
              )}
              {visibleConvs.map((conv) => {
                const isGroupConv = conv.kind === 'group';
                const participant = conv.participants[0];
                const meta = isGroupConv ? GROUP_META.twin : metaOf(participant?.employee_no ?? '');
                return (
                  <div
                    key={conv.id}
                    className={`wp-row ${selectedId === conv.id ? 'active' : ''}`}
                    role="button"
                    tabIndex={0}
                    onClick={() => setSelectedId(conv.id)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' || e.key === ' ') {
                        e.preventDefault();
                        setSelectedId(conv.id);
                      }
                    }}
                  >
                    {isGroupConv ? (
                      <Avatar size={42} style={{ background: '#e6fffb', color: '#13c2c2' }}>
                        <TeamOutlined />
                      </Avatar>
                    ) : (
                      <Avatar size={42} style={{ background: meta.bg, color: meta.color }}>
                        {meta.emoji}
                      </Avatar>
                    )}
                    <div className="wp-row-main">
                      <div className="wp-row-top">
                        <span className="wp-row-name">
                          {isGroupConv ? `${conv.title || '协作空间'}（${conv.participants.length}）` : participant?.name ?? ''}
                        </span>
                        <span className="wp-row-time">{formatTime(conv.updated_at)}</span>
                      </div>
                      <div className="wp-row-preview">{conv.last_message || '暂无消息'}</div>
                    </div>
                  </div>
                );
              })}
            </>
          ) : tab === 'contacts' ? (
            <>
              <div className="wp-contacts-head">
                <Text type="secondary" style={{ fontSize: 12 }}>
                  选择联系人，点击「私聊」或勾选后发起群聊
                </Text>
                <Button size="small" type="link" onClick={() => setNewChatOpen(true)}>
                  发起群聊
                </Button>
              </div>
              {contactGroups.map((group) => (
                <div key={group.key} className="wp-contact-group">
                  <div className="wp-contact-label">{group.label}</div>
                  {group.items.map((emp) => {
                    const meta = metaOf(emp.employee_no);
                    return (
                      <div key={emp.employee_no} className="wp-contact-row">
                        <Avatar size={38} style={{ background: meta.bg, color: meta.color }}>
                          {meta.emoji}
                        </Avatar>
                        <div className="wp-row-main">
                          <div className="wp-row-name">{emp.name}</div>
                          <div className="wp-row-preview">{emp.role_prompt || `${emp.department} · ${meta.label}`}</div>
                        </div>
                        <Space size={4}>
                          {emp.type === 'twin' && (
                            <Button size="small" onClick={() => setSkillDrawerOpen(true)}>
                              技能
                            </Button>
                          )}
                          <Button size="small" type="primary" ghost onClick={() => void openDirect(emp.employee_no)}>
                            私聊
                          </Button>
                        </Space>
                      </div>
                    );
                  })}
                </div>
              ))}
              {contactGroups.length === 0 && <Empty style={{ marginTop: 48 }} description="没有找到相关联系人" />}
            </>
          ) : (
            <>
              <div className="wp-contacts-head">
                <Text type="secondary" style={{ fontSize: 12 }}>
                  Mock 工作流/RPA 目录 · 点击卡片查看步骤与授权成员
                </Text>
              </div>
              {visibleWorkflows.map((workflow) => {
                const isRpa = workflow.type === 'rpa';
                const meta = isRpa ? GROUP_META.rpa : GROUP_META.virtual;
                return (
                  <div
                    key={workflow.plugin_id}
                    className="wp-contact-row clickable-card"
                    role="button"
                    tabIndex={0}
                    onClick={() => setSelectedWorkflow(workflow)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' || e.key === ' ') {
                        e.preventDefault();
                        setSelectedWorkflow(workflow);
                      }
                    }}
                  >
                    <Avatar size={38} style={{ background: meta.bg, color: meta.color }}>
                      {isRpa ? <ThunderboltOutlined /> : <ExperimentOutlined />}
                    </Avatar>
                    <div className="wp-row-main">
                      <div className="wp-row-top">
                        <span className="wp-row-name">{workflow.name}</span>
                        <Tag color={isRpa ? 'orange' : 'purple'} style={{ marginInlineEnd: 0 }}>
                          {isRpa ? 'RPA' : '流程'}
                        </Tag>
                      </div>
                      <div className="wp-row-preview">{workflow.description}</div>
                      {workflow.owner_employee && (
                        <div className="wp-row-preview">
                          由 {workflow.owner_employee.name} 处理
                        </div>
                      )}
                    </div>
                  </div>
                );
              })}
              {visibleWorkflows.length === 0 && <Empty style={{ marginTop: 48 }} description="没有找到相关工作流" />}
            </>
          )}
        </div>
      </aside>

      {/* 右栏：对话窗口 */}
      <main className="wp-chat">
        {selected ? (
          <>
            <div className="wp-chat-head">
              {isGroup ? (
                <Avatar size={40} style={{ background: '#e6fffb', color: '#13c2c2' }}>
                  <TeamOutlined />
                </Avatar>
              ) : (
                <Avatar size={40} style={{ background: selectedMeta.bg, color: selectedMeta.color }}>
                  {selectedMeta.emoji}
                </Avatar>
              )}
              <div style={{ minWidth: 0 }}>
                <div className="wp-chat-title">{selectedTitle}</div>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  {isGroup ? `${selected.participants.length} 人 · ${selected.participants.map((p) => p.name).join('、')}` : selectedMeta.label}
                </Text>
              </div>
              <div style={{ flex: 1 }} />
              <Popconfirm
                title="清空本会话？将删除本会话的消息与任务，不影响其他协作空间。"
                onConfirm={() => void handleClear()}
              >
                <Button size="small" danger>
                  清空会话
                </Button>
              </Popconfirm>
              {isGroup && addableMembers.length > 0 && (
                <Button size="small" onClick={() => setAddMemberOpen(true)}>
                  添加成员
                </Button>
              )}
              {!isGroup && selected.participants[0]?.employee_no === twin?.employee_no && (
                <Button size="small" onClick={() => setSkillDrawerOpen(true)}>
                  分身资料
                </Button>
              )}
            </div>

            <div className="wp-chat-scroll">
              {selected.messages.length === 0 && (
                <div className="wp-empty">
                  <div className="wp-empty-emoji">{isGroup ? '🤝' : selectedMeta.emoji}</div>
                  <Paragraph>打个招呼，开始今天的协作吧。</Paragraph>
                </div>
              )}
              {selected.messages.map((msg) => {
                const mine = msg.role === 'user';
                return (
                  <div key={msg.id}>
                    <div className={`wp-msg ${mine ? 'me' : ''}`}>
                      {!mine && (
                        <Avatar size={34} style={{ background: metaOf(msg.participant_no).bg, color: metaOf(msg.participant_no).color }}>
                          {metaOf(msg.participant_no).emoji}
                        </Avatar>
                      )}
                      <div className="wp-msg-col">
                        {!mine && isGroup && (
                          <div className="wp-msg-name">
                            {msg.participant_name}
                            {/完成|TASK_COMPLETED|交付/.test(msg.content) && (
                              <span className="wp-feedback-ok">✅ 已完成</span>
                            )}
                            {!/完成|TASK_COMPLETED|交付/.test(msg.content) && /收到|开始|处理|认领/.test(msg.content) && (
                              <span className="wp-feedback-run">⏳ 执行中</span>
                            )}
                          </div>
                        )}
                        <div className={`wp-bubble ${mine ? 'me' : 'other'}`}>
                          <MarkdownText text={msg.content} />
                        </div>
                        {msg.tool_cards.length > 0 && (
                          <div className="tool-cards">
                            {(msg.tool_cards as Array<{ name: string; decision: string; policy_id?: string | null }>).map(
                              (card, index) => (
                                <div className={`tool-card ${card.decision}`} key={`${msg.id}-${index}`}>
                                  <div className="tool-card-head">{card.name}</div>
                                  <div className="tool-card-sub">决策：{card.decision}</div>
                                </div>
                              ),
                            )}
                          </div>
                        )}
                      </div>
                    </div>
                    {isGroup &&
                      taskGroups
                        .get(msg.seq)
                        ?.map((task) => (
                          <TaskCard
                            key={task.id}
                            task={task}
                            workerName={workerName}
                            metaOf={metaOf}
                            onApprove={handleApprove}
                            acting={acting}
                          />
                        ))}
                  </div>
                );
              })}
              {sending && (
                <div className="wp-thinking">
                  <RobotOutlined spin />
                  成员们正在思考…
                </div>
              )}
              {isGroup && unattachedTasks.length > 0 && (
                <div className="wp-tasks">
                  {unattachedTasks.map((task) => (
                    <TaskCard
                      key={task.id}
                      task={task}
                      workerName={workerName}
                      metaOf={metaOf}
                      onApprove={handleApprove}
                      acting={acting}
                    />
                  ))}
                </div>
              )}
              {chatError && (
                <div className="wp-chat-error">发送失败：{chatError}，消息已保留在输入框</div>
              )}
            </div>

            <div className="wp-chat-input">
              <Input.TextArea
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onPressEnter={(e) => {
                  if (!e.shiftKey) {
                    e.preventDefault();
                    void send();
                  }
                }}
                placeholder={isGroup ? '描述一个任务，我来拆解安排给同事们…' : '发消息…'}
                autoSize={{ minRows: 1, maxRows: 4 }}
              />
              <Button type="primary" icon={<SendOutlined />} onClick={() => void send()} loading={sending}>
                发送
              </Button>
            </div>
          </>
        ) : (
          <div className="wp-empty wp-empty-page">
            <div className="wp-empty-emoji">👋</div>
            <Typography.Title level={4} style={{ margin: 0 }}>
              早上好，{actor.name}
            </Typography.Title>
            <Paragraph type="secondary">可以和我的分身聊聊，或找一位数字员工帮忙。</Paragraph>
            <Space>
              {twin && (
                <Button type="primary" icon={<RobotOutlined />} onClick={() => void openDirect(twin.employee_no)}>
                  和我的分身聊聊
                </Button>
              )}
              <Button icon={<TeamOutlined />} onClick={() => setNewChatOpen(true)}>
                发起协作
              </Button>
            </Space>
          </div>
        )}
      </main>

      {/* 发起会话弹窗 */}
      <Modal
        title="发起会话"
        open={newChatOpen}
        onCancel={() => {
          setNewChatOpen(false);
          setSelectedNos([]);
          setGroupTitle('');
        }}
        onOk={() => void submitNewChat()}
        okText="创建"
      >
        <Radio.Group
          value={chatMode}
          onChange={(e) => {
            setChatMode(e.target.value);
            setSelectedNos([]);
          }}
          style={{ marginBottom: 14 }}
        >
          <Radio.Button value="direct">私聊</Radio.Button>
          <Radio.Button value="group">群聊</Radio.Button>
        </Radio.Group>
        {chatMode === 'group' && (
          <Input
            placeholder="群聊名称（可选）"
            value={groupTitle}
            onChange={(e) => setGroupTitle(e.target.value)}
            style={{ marginBottom: 12 }}
          />
        )}
        <div className="wp-modal-contacts">
          {contactGroups.map((group) => (
            <div key={group.key} className="wp-contact-group">
              <div className="wp-contact-label">{group.label}</div>
              {group.items.map((emp) => {
                const meta = metaOf(emp.employee_no);
                const checked = selectedNos.includes(emp.employee_no);
                return (
                  <div key={emp.employee_no} className="wp-contact-row" onClick={() => pickEmployee(emp.employee_no)}>
                    <Avatar size={34} style={{ background: meta.bg, color: meta.color }}>
                      {meta.emoji}
                    </Avatar>
                    <div className="wp-row-main">
                      <div className="wp-row-name">{emp.name}</div>
                      <div className="wp-row-preview">{emp.department}</div>
                    </div>
                    <Checkbox checked={checked} onChange={() => pickEmployee(emp.employee_no)} />
                  </div>
                );
              })}
            </div>
          ))}
        </div>
      </Modal>

      {/* 添加群成员弹窗 */}
      <Modal
        title="添加成员"
        open={addMemberOpen}
        onCancel={() => {
          setAddMemberOpen(false);
          setMemberNos([]);
        }}
        onOk={() => void submitAddMembers()}
        okText="添加"
        okButtonProps={{ disabled: memberNos.length === 0 }}
      >
        {addableMembers.length === 0 ? (
          <Empty description="没有可添加的数字员工" />
        ) : (
          addableMembers.map((emp) => {
            const meta = metaOf(emp.employee_no);
            return (
              <div
                key={emp.employee_no}
                className="wp-contact-row"
                onClick={() =>
                  setMemberNos((prev) => (prev.includes(emp.employee_no) ? prev.filter((n) => n !== emp.employee_no) : [...prev, emp.employee_no]))
                }
              >
                <Avatar size={34} style={{ background: meta.bg, color: meta.color }}>
                  {meta.emoji}
                </Avatar>
                <div className="wp-row-main">
                  <div className="wp-row-name">{emp.name}</div>
                  <div className="wp-row-preview">{emp.role_prompt || emp.department}</div>
                </div>
                <Checkbox checked={memberNos.includes(emp.employee_no)} />
              </div>
            );
          })
        )}
      </Modal>

      {/* 分身资料抽屉 + 技能管理 */}
      <Drawer
        title="我的分身"
        open={skillDrawerOpen}
        onClose={() => setSkillDrawerOpen(false)}
        width={380}
        extra={
          <Button type="primary" size="small" icon={<UploadOutlined />} onClick={() => setSkillModalOpen(true)}>
            上传技能
          </Button>
        }
      >
        {twin && (
          <>
            <div className="wp-drawer-hero">
              <Avatar size={56} style={{ background: GROUP_META.twin.bg, color: GROUP_META.twin.color, fontSize: 28 }}>
                {GROUP_META.twin.emoji}
              </Avatar>
              <div>
                <div className="wp-chat-title">{twin.name}</div>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  {twin.department} · {GROUP_META.twin.label}
                </Text>
              </div>
            </div>
            <Paragraph className="wp-persona">{twin.role_prompt || '（尚未配置擅长方向）'}</Paragraph>
            <div className="wp-drawer-section">
              <div className="wp-drawer-section-title">
                已掌握技能（{skills.length}）
                <Text type="secondary" style={{ fontSize: 12 }}>
                  已学会 {skills.filter((s) => s.status === 'active').length} 项
                </Text>
              </div>
              {skills.length === 0 && <Empty description="还没有技能，点击右上角上传" />}
              {skills.map((skill) => (
                <div key={skill.id} className="wp-skill">
                  <div style={{ minWidth: 0 }}>
                    <div className="wp-row-name">{skill.name}</div>
                    <div className="wp-row-preview">{skill.description || skill.content.slice(0, 60)}</div>
                  </div>
                  <Switch
                    size="small"
                    checked={skill.status === 'active'}
                    onChange={(checked) => void toggleSkill(skill, checked)}
                    aria-label={`切换技能 ${skill.name}`}
                  />
                  <Popconfirm title="确定删除该技能？" onConfirm={() => void removeSkill(skill)}>
                    <Button type="text" size="small" danger icon={<DeleteOutlined />} aria-label={`删除技能 ${skill.name}`} />
                  </Popconfirm>
                </div>
              ))}
            </div>

            <div className="wp-drawer-section">
              <div className="wp-drawer-section-title">可用能力（插件授权 · {twin.grants.length}）</div>
              {twin.grants.length === 0 ? (
                <Empty description="暂无插件授权" />
              ) : (
                twin.grants.map((grant) => (
                  <div className="wp-skill" key={grant.plugin_id}>
                    <div style={{ minWidth: 0 }}>
                      <div className="wp-row-name">{grant.name}</div>
                      <div className="wp-row-preview">
                        {PLUGIN_TYPE_LABEL[grant.type] ?? grant.type} ·{' '}
                        {DATA_LEVEL_LABEL[grant.data_level] ?? grant.data_level} 数据
                      </div>
                    </div>
                    <Tag color={DECISION_META[grant.decision_mode]?.color ?? 'default'}>
                      {DECISION_META[grant.decision_mode]?.label ?? grant.decision_mode}
                    </Tag>
                  </div>
                ))
              )}
              <Text type="secondary" style={{ fontSize: 12, display: 'block', marginTop: 8 }}>
                流程 / RPA 工作流由协作空间里的数字员工执行，分身负责拆解任务并指派。
              </Text>
            </div>
          </>
        )}
      </Drawer>

      {/* 工作流详情抽屉 */}
      <Drawer
        title="工作流详情"
        open={selectedWorkflow !== null}
        onClose={() => setSelectedWorkflow(null)}
        width={400}
      >
        {selectedWorkflow && (
          <>
            <div className="wp-drawer-hero">
              <Avatar
                size={52}
                style={{
                  background: selectedWorkflow.type === 'rpa' ? GROUP_META.rpa.bg : GROUP_META.virtual.bg,
                  color: selectedWorkflow.type === 'rpa' ? GROUP_META.rpa.color : GROUP_META.virtual.color,
                  fontSize: 24,
                }}
              >
                {selectedWorkflow.type === 'rpa' ? <ThunderboltOutlined /> : <ExperimentOutlined />}
              </Avatar>
              <div>
                <div className="wp-chat-title">{selectedWorkflow.name}</div>
                <Space size={4} wrap>
                  <Tag color={selectedWorkflow.type === 'rpa' ? 'orange' : 'purple'}>
                    {selectedWorkflow.type === 'rpa' ? 'RPA 自动化' : '流程（Workflow）'}
                  </Tag>
                  <Tag color={selectedWorkflow.data_level === 'L3' ? 'red' : selectedWorkflow.data_level === 'L2' ? 'blue' : 'green'}>
                    {selectedWorkflow.data_level === 'L3' ? '敏感 L3' : selectedWorkflow.data_level === 'L2' ? '内部 L2' : '公开 L1'}
                  </Tag>
                </Space>
              </div>
            </div>
            <Paragraph className="wp-persona">{selectedWorkflow.description}</Paragraph>

            <div className="wp-drawer-section">
              <div className="wp-drawer-section-title">执行步骤</div>
              <div className="wp-wf-steps">
                {selectedWorkflow.steps.map((step, index) => (
                  <div className="wp-wf-step" key={step}>
                    <span className="wp-wf-step-no">{index + 1}</span>
                    <span>{step}</span>
                  </div>
                ))}
                {selectedWorkflow.steps.length === 0 && <Text type="secondary">暂无步骤说明</Text>}
              </div>
            </div>

            <div className="wp-drawer-section">
              <div className="wp-drawer-section-title">授权成员（可执行）</div>
              <div className="wp-wf-members">
                {selectedWorkflow.authorized_employees.map((emp) => {
                  const meta = metaOf(emp.employee_no);
                  return (
                    <div className="wp-wf-member" key={emp.employee_no}>
                      <Avatar size={28} style={{ background: meta.bg, color: meta.color }}>
                        {meta.emoji}
                      </Avatar>
                      <Text>{emp.name}</Text>
                      <Text type="secondary" style={{ fontSize: 12 }}>
                        {emp.employee_no}
                      </Text>
                    </div>
                  );
                })}
                {selectedWorkflow.authorized_employees.length === 0 && <Text type="secondary">暂未授权</Text>}
              </div>
            </div>

            {selectedWorkflow.demo_prompt && (
              <div className="wp-drawer-section">
                <div className="wp-drawer-section-title">示例指令</div>
                <div className="wp-wf-demo">
                  <span>「{selectedWorkflow.demo_prompt}」</span>
                  <Button size="small" onClick={() => void copyDemoPrompt(selectedWorkflow)}>
                    复制
                  </Button>
                </div>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  把这句话发给协作空间，分身会拆解并指派给对应成员执行
                </Text>
              </div>
            )}
          </>
        )}
      </Drawer>

      {/* 上传技能弹窗 */}
      <Modal
        title="上传技能"
        open={skillModalOpen}
        onCancel={() => {
          setSkillModalOpen(false);
          skillForm.resetFields();
        }}
        onOk={() => void submitSkill()}
        okText="上传"
        width={520}
      >
        <Form form={skillForm} layout="vertical">
          <Form.Item name="name" label="技能名称" rules={[{ required: true, message: '请输入技能名称' }]}>
            <Input placeholder="例如：报销制度速答" maxLength={40} />
          </Form.Item>
          <Form.Item name="description" label="一句话说明">
            <Input placeholder="我的分身会用这句话向别人介绍这项技能" maxLength={80} />
          </Form.Item>
          <Form.Item name="content" label="技能内容" rules={[{ required: true, message: '请填写或导入技能内容' }]}>
            <Input.TextArea rows={7} placeholder="粘贴 Markdown / 纯文本内容，或直接拖入 .md / .txt 文件" />
          </Form.Item>
          <Upload.Dragger
            accept=".md,.txt,.markdown"
            maxCount={1}
            beforeUpload={(file) => {
              const reader = new FileReader();
              reader.onload = () => {
                skillForm.setFieldValue('content', String(reader.result ?? ''));
              };
              reader.readAsText(file);
              return false;
            }}
            showUploadList={false}
          >
            <p className="ant-upload-drag-icon">
              <UploadOutlined />
            </p>
            <p className="ant-upload-text">点击或拖拽 .md / .txt 文件导入内容</p>
          </Upload.Dragger>
        </Form>
      </Modal>
    </div>
  );
}
