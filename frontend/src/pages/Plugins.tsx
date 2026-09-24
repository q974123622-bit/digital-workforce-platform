import { useCallback, useEffect, useMemo, useState } from 'react';
import type { ReactNode } from 'react';
import { CheckCircleOutlined, CloudServerOutlined, DatabaseOutlined, ExclamationCircleOutlined, FileMarkdownOutlined, ReloadOutlined, SafetyCertificateOutlined, TeamOutlined } from '@ant-design/icons';
import { Alert, Button, Card, Col, Empty, Form, InputNumber, Modal, Row, Select, Space, Statistic, Switch, Table, Tabs, Tag, Typography, message } from 'antd';
import type { PluginBinding, PluginSubmission, UnifiedPlugin } from '../api/client';
import type { AgentProfile, EffectiveCapabilities, EffectiveCapabilityItem } from '@dwp/shared-schema';
import { api } from '../api/client';
import { ErrorState, LoadingState } from '../components/PageState';
import { useAsyncData } from '../hooks/useAsyncData';
import PluginDetailDrawer from '../features/plugins/PluginDetailDrawer';
import McpServiceList from '../features/plugins/McpServiceList';
import SkillGovernance from '../features/plugins/SkillGovernance';
import SkillHubMarket from '../features/plugins/SkillHubMarket';
import KnowledgeGrantPanel from '../features/knowledge/KnowledgeGrantPanel';
import { deployStateMeta } from '../features/plugins/pluginMeta';
import { formatServerTime } from '../utils/serverTime';

interface CapabilityCenterData { agents: AgentProfile[]; effective: EffectiveCapabilities[] }

const STATUS_META: Record<EffectiveCapabilityItem['status'], { label: string; color: string }> = {
  available: { label: '现在可用', color: 'success' },
  approval: { label: '使用时需审批', color: 'warning' },
  unauthorized: { label: '未授权', color: 'default' },
  runtime_unavailable: { label: '运行环境未就绪', color: 'error' },
  // 数据源不可达：不是权限问题，用中性灰而不是红色告警。
  source_unavailable: { label: '数据源暂不可用', color: 'default' },
  disabled: { label: '已停用', color: 'default' },
};

export default function Plugins() {
  // 受控页签：MCP 接入页的「去授权 / 去审核」需要把用户带到插件治理页。
  const [activeKey, setActiveKey] = useState('governance');
  // 技能市场只是内网 SkillHub 的浏览/导入入口，平台自身不依赖它。离线环境里
  // SkillHub 往往不通，未接入时整个页签不出现 —— 免得用户点进去只看一条「未接入」。
  const overviewFetcher = useCallback(async () => {
    try { return await api.getSkillHubOverview(); } catch { return null; }
  }, []);
  const skillhub = useAsyncData(overviewFetcher);
  const skillhubEnabled = skillhub.data?.enabled === true;
  useEffect(() => {
    if (!skillhubEnabled && activeKey === 'skillhub') setActiveKey('governance');
  }, [skillhubEnabled, activeKey]);

  const items = [
    { key: 'governance', label: '插件治理', children: <PluginGovernance /> },
    {
      key: 'mcp',
      label: 'MCP 接入',
      children: (
        <McpServiceList
          onGoAuthorize={() => setActiveKey('governance')}
          onGoReview={() => setActiveKey('governance')}
        />
      ),
    },
    { key: 'skill-governance', label: '技能治理', children: <SkillGovernance /> },
    { key: 'effective', label: '运行能力核验', children: <CapabilityStatus /> },
  ];
  if (skillhubEnabled) {
    items.splice(2, 0, { key: 'skillhub', label: '技能市场', children: <SkillHubMarket /> });
  }
  return <Tabs activeKey={activeKey} onChange={setActiveKey} destroyOnHidden items={items} />;
}

function PluginGovernance() {
  // 五个数据块互不依赖，并发拉取，避免首屏逐条串行等待。
  const fetcher = useCallback(async () => {
    const [plugins, submissions, memory, agents, bindings] = await Promise.all([
      api.listAdminPlugins(),
      api.listPluginSubmissions(),
      api.memoryHealth(),
      api.listAgents(),
      api.listAgentPluginBindings(),
    ]);
    return { plugins, submissions, memory, agents, bindings };
  }, []);
  const { data, loading, error, reload } = useAsyncData(fetcher);
  const [bindingOpen, setBindingOpen] = useState(false);
  const [detailPluginId, setDetailPluginId] = useState<string | null>(null);
  const [withdrawingPluginId, setWithdrawingPluginId] = useState<string | null>(null);
  const [bindingForm] = Form.useForm();
  // 绑定列表单独持有本地副本：改一个开关就整页 reload 会让表格闪成 loading、丢掉焦点。
  const [bindings, setBindings] = useState<PluginBinding[]>([]);
  const [savingBindingId, setSavingBindingId] = useState<number | null>(null);
  // 解除授权走内联确认（与工作台一致，不依赖浮层）。
  const [revokingBindingId, setRevokingBindingId] = useState<number | null>(null);
  // 优先级用本地草稿，提交发生在失焦时，避免每敲一个数字就发一次请求。
  const [priorityDraft, setPriorityDraft] = useState<Record<number, number>>({});
  useEffect(() => { if (data?.bindings) setBindings(data.bindings); }, [data]);
  if (loading) return <LoadingState rows={7} />;
  if (error) return <ErrorState onRetry={reload} />;
  const act = async (row: PluginSubmission, action: 'approve' | 'reject' | 'publish' | 'rollback') => {
    if (action === 'publish') await api.publishPlugin(row.plugin_id!, row.version!);
    else if (action === 'rollback') await api.rollbackPlugin(row.plugin_id!, row.version!);
    else await api.reviewPlugin(row.id, action === 'approve', '');
    message.success(
      action === 'approve' ? '已批准，等待手动发布'
        : action === 'reject' ? '已拒绝'
          : action === 'rollback' ? `已回滚到 v${row.version}` : '已发布',
    );
    reload();
  };
  const withdraw = async (pluginId: string) => {
    try {
      await api.withdrawPlugin(pluginId);
      setWithdrawingPluginId(null);
      message.success('插件已下架，相关授权已全部停用');
      reload();
    } catch (err) { message.error(`下架失败：${(err as Error).message}`); }
  };
  const agentLabel = (employeeId: string) =>
    (data?.agents ?? []).find((row) => row.employee_id === employeeId)?.display_name ?? employeeId;
  const pluginLabel = (pluginId: string) =>
    (data?.plugins ?? []).find((row) => String(row.id) === pluginId)?.name ?? pluginId;
  // 只有 published 的版本才会被 effective_plugins 采用；锁定到其它状态会让该插件直接失效。
  const versionsOf = (pluginId: string) => Array.from(new Set(
    (data?.submissions ?? [])
      .filter((row) => row.plugin_id === pluginId && row.publish_status === 'published')
      .map((row) => row.version),
  ));
  const patchBinding = async (row: PluginBinding, payload: Partial<Omit<PluginBinding, 'id'>>) => {
    setSavingBindingId(row.id);
    try {
      const updated = await api.updateAgentPluginBinding(row.id, payload);
      setBindings((current) => current.map((item) => (item.id === updated.id ? updated : item)));
      message.success('授权已更新');
    } catch (err) { message.error(`更新失败：${(err as Error).message}`); }
    finally { setSavingBindingId(null); }
  };
  const revokeBinding = async (id: number) => {
    try {
      await api.deleteAgentPluginBinding(id);
      setBindings((current) => current.filter((item) => item.id !== id));
      setRevokingBindingId(null);
      message.success('已解除授权');
    } catch (err) { message.error(`解除授权失败：${(err as Error).message}`); }
  };
  const bindingColumns = [
    {
      title: 'AI员工', dataIndex: 'target_agent_id',
      render: (value: string) => <div><div className="text-ink">{agentLabel(value)}</div><div className="text-xs text-ink-tertiary">{value}</div></div>,
    },
    {
      title: '插件', dataIndex: 'plugin_id',
      render: (value: string) => <div><div className="text-ink">{pluginLabel(value)}</div><div className="text-xs text-ink-tertiary">{value}</div></div>,
    },
    {
      title: '管理员授权', dataIndex: 'admin_enabled', width: 110,
      render: (value: boolean, row: PluginBinding) => (
        <Switch
          size="small" checked={value} loading={savingBindingId === row.id}
          onChange={(checked) => void patchBinding(row, { admin_enabled: checked })}
          aria-label={`切换授权 ${row.plugin_id}`}
        />
      ),
    },
    {
      title: '员工自启停', dataIndex: 'employee_enabled', width: 110,
      render: (value: boolean) => <Tag color={value ? 'blue' : 'default'}>{value ? '已启用' : '已停用'}</Tag>,
    },
    {
      title: '决策方式', dataIndex: 'decision_mode', width: 140,
      render: (value: string, row: PluginBinding) => (
        <Select
          size="small" className="w-full" value={value}
          onChange={(next) => void patchBinding(row, { decision_mode: next })}
          options={[
            { value: 'allow', label: '直接放行' },
            { value: 'approval', label: '需审批' },
            { value: 'deny', label: '禁止' },
          ]}
        />
      ),
    },
    {
      title: '优先级', dataIndex: 'priority', width: 100,
      render: (value: number, row: PluginBinding) => (
        <InputNumber
          size="small" min={1} max={999} className="w-full"
          value={priorityDraft[row.id] ?? value}
          onChange={(next) => { if (typeof next === 'number') setPriorityDraft((current) => ({ ...current, [row.id]: next })); }}
          onBlur={() => {
            const draft = priorityDraft[row.id];
            setPriorityDraft((current) => { const next = { ...current }; delete next[row.id]; return next; });
            if (draft === undefined || draft === value) return;
            void patchBinding(row, { priority: draft });
          }}
        />
      ),
    },
    {
      title: '版本锁定', dataIndex: 'pinned_version', width: 150,
      render: (value: string | null | undefined, row: PluginBinding) => (
        <Select
          size="small" className="w-full" allowClear placeholder="跟随当前版本"
          value={value ?? undefined}
          onChange={(next) => void patchBinding(row, { pinned_version: next ?? null })}
          options={versionsOf(row.plugin_id).map((version) => ({ value: version, label: `v${version}` }))}
        />
      ),
    },
    {
      title: '操作', width: 150,
      render: (_: unknown, row: PluginBinding) => (
        revokingBindingId === row.id ? (
          <Space size={4}>
            <Button size="small" danger type="primary" onClick={() => void revokeBinding(row.id)}>确认解除</Button>
            <Button size="small" onClick={() => setRevokingBindingId(null)}>取消</Button>
          </Space>
        ) : (
          <Button size="small" danger onClick={() => setRevokingBindingId(row.id)}>解除授权</Button>
        )
      ),
    },
  ];
  return <div>
    <div className="mb-4"><Typography.Title level={2} className="!mb-1 !text-[26px]">插件治理</Typography.Title><Typography.Text type="secondary">统一管理 Skill 与 MCP 的审核、发布、版本和运行状态</Typography.Text></div>
    <Alert className="mb-4" showIcon type="info" message="审核与发布分离" description="批准只代表安全审核通过，管理员仍需手动发布；新版本发布失败不会影响旧版本。Hosted MCP 本机仅扫描，不执行源码。" />
    <Row gutter={0} className="admin-metrics mb-4 overflow-hidden rounded-xl border border-[var(--dwp-border-light)] bg-surface"><Col xs={12} lg={6}><Card variant="borderless"><Statistic title="插件总数" value={data?.plugins.length ?? 0} /></Card></Col><Col xs={12} lg={6}><Card variant="borderless"><Statistic title="待审核" value={data?.submissions.filter((r) => r.review_status === 'pending').length ?? 0} /></Card></Col><Col xs={12} lg={6}><Card variant="borderless"><Statistic title="长期记忆" value={data?.memory.memories ?? 0} /></Card></Col><Col xs={12} lg={6}><Card variant="borderless"><Statistic title="mem0 状态" value={data?.memory.status === 'healthy' ? '正常' : '异常'} /></Card></Col></Row>
    <Card className="mb-4" title="插件目录" extra={<Typography.Text type="secondary" className="!text-xs">点击插件查看版本、授权和 Runtime</Typography.Text>}>
      <Table
        rowKey={(row) => String(row.id)}
        dataSource={data?.plugins}
        pagination={false}
        onRow={(row) => ({ onClick: () => setDetailPluginId(String(row.id)), className: 'cursor-pointer' })}
        columns={[
          { title: '插件', dataIndex: 'name' },
          { title: '类型', dataIndex: 'plugin_type', render: (value) => <Tag>{value === 'skill' ? 'Skill' : 'MCP'}</Tag> },
          { title: '范围', dataIndex: 'scope', render: (value) => value === 'personal' ? '个人' : '共享' },
          { title: '当前版本', dataIndex: 'current_version', render: (value) => value ? `v${value}` : '尚未发布' },
          { title: '状态', dataIndex: 'status', render: (value) => <Tag color={value === 'active' ? 'success' : 'default'}>{value === 'active' ? '已启用' : value === 'withdrawn' ? '已下架' : '已停用'}</Tag> },
          {
            title: '操作', width: 170,
            render: (_: unknown, row: UnifiedPlugin) => {
              const pluginId = String(row.id);
              if (row.status !== 'active') return <Typography.Text type="secondary" className="!text-xs">已下架</Typography.Text>;
              return withdrawingPluginId === pluginId ? (
                <Space size={4} onClick={(event) => event.stopPropagation()}>
                  <Button size="small" danger type="primary" onClick={() => void withdraw(pluginId)}>确认下架</Button>
                  <Button size="small" onClick={() => setWithdrawingPluginId(null)}>取消</Button>
                </Space>
              ) : (
                <Button
                  size="small" danger
                  onClick={(event) => { event.stopPropagation(); setWithdrawingPluginId(pluginId); }}
                >
                  下架
                </Button>
              );
            },
          },
        ]}
      />
    </Card>
    <Card className="mb-4" title="版本审核与发布"><Table rowKey="id" dataSource={data?.submissions} columns={[
      { title: '插件', dataIndex: 'name' }, { title: '类型', dataIndex: 'plugin_type', render: (v) => <Tag color={v === 'skill' ? 'blue' : 'green'}>{v === 'skill' ? 'Skill' : 'MCP'}</Tag> },
      { title: '提交人', dataIndex: 'submitted_by' }, { title: '版本', dataIndex: 'version' }, { title: '审核', dataIndex: 'review_status' }, { title: '发布', dataIndex: 'publish_status' },
      {
        title: '审核意见', dataIndex: 'review_note',
        render: (value: string) => value
          ? <Typography.Text type="secondary" className="!text-xs">{value}</Typography.Text>
          : <Typography.Text type="secondary" className="!text-xs">—</Typography.Text>,
      },
      {
        title: '操作',
        render: (_: unknown, row: PluginSubmission) => (
          <Space>
            {row.review_status === 'pending' && <><Button size="small" type="primary" onClick={() => act(row, 'approve')}>批准</Button><Button size="small" danger onClick={() => act(row, 'reject')}>拒绝</Button></>}
            {row.review_status === 'approved' && row.publish_status !== 'published' && <Button size="small" type="primary" onClick={() => act(row, 'publish')}>发布</Button>}
            {row.publish_status === 'superseded' && <Button size="small" onClick={() => act(row, 'rollback')}>回滚到此版本</Button>}
          </Space>
        ),
      },
    ]} /></Card>
    <Card
      className="mb-4"
      title="AI员工插件配置"
      extra={<Button type="primary" onClick={() => setBindingOpen(true)}>新增授权</Button>}
    >
      <Alert
        className="mb-3"
        type="warning"
        showIcon
        message="授权粒度是「AI员工」，不是自然人"
        description="每个人有独立分身（DT-工号），绑到分身即等价于配给本人。关闭「管理员授权」后该插件对该员工立即不可见；决策方式选「需审批」时该员工需先在插件中心提交使用申请，审批通过后自动转为「直接放行」并即时生效；选「禁止」则员工无法申请（需管理员先改回其他方式）。"
      />
      <Table rowKey="id" dataSource={bindings} pagination={false} columns={bindingColumns} />
    </Card>
    <KnowledgeGrantPanel />
    <Modal
      title="授权插件给AI员工"
      open={bindingOpen}
      onCancel={() => { setBindingOpen(false); bindingForm.resetFields(); }}
      onOk={async () => {
        const value = await bindingForm.validateFields();
        try {
          const created = await api.createAgentPluginBinding({
            plugin_id: value.plugin_id,
            target_agent_id: value.target_agent_id,
            decision_mode: value.decision_mode ?? 'allow',
            priority: value.priority ?? 100,
            admin_enabled: value.admin_enabled ?? true,
          });
          setBindings((current) => [...current, created].sort(
            (a, b) => a.target_agent_id.localeCompare(b.target_agent_id) || a.priority - b.priority,
          ));
          message.success('授权已创建');
          setBindingOpen(false); bindingForm.resetFields();
        } catch (err) { message.error(`创建失败：${(err as Error).message}`); }
      }}
    >
      <Form form={bindingForm} layout="vertical">
        <Form.Item name="plugin_id" label="已发布插件" rules={[{ required: true }]}>
          <Select
            showSearch
            optionFilterProp="label"
            placeholder="按名称搜索共享/个人插件"
            options={[
              {
                label: '共享插件',
                options: (data?.plugins ?? [])
                  .filter((row) => row.current_version && row.scope !== 'personal')
                  .map((row) => ({ value: String(row.id), label: `${row.name} · v${row.current_version}` })),
              },
              {
                label: '个人插件',
                options: (data?.plugins ?? [])
                  .filter((row) => row.current_version && row.scope === 'personal')
                  .map((row) => ({ value: String(row.id), label: `${row.name} · v${row.current_version} · ${row.owner_human_no ?? '未知属主'}` })),
              },
            ].filter((group) => group.options.length > 0)}
          />
        </Form.Item>
        <Form.Item name="target_agent_id" label="目标AI员工" rules={[{ required: true }]}>
          <Select options={(data?.agents ?? []).map((row) => ({ value: row.employee_id, label: row.display_name }))} />
        </Form.Item>
        <Form.Item name="decision_mode" label="决策方式" initialValue="allow">
          <Select options={[{ value: 'allow', label: '直接放行' }, { value: 'approval', label: '需审批' }, { value: 'deny', label: '禁止' }]} />
        </Form.Item>
        <Form.Item name="priority" label="优先级" initialValue={100} tooltip="数字越小越靠前">
          <InputNumber min={1} max={999} className="w-full" />
        </Form.Item>
        <Form.Item name="admin_enabled" label="管理员授权" initialValue valuePropName="checked">
          <Switch />
        </Form.Item>
      </Form>
    </Modal>
    <PluginDetailDrawer admin pluginId={detailPluginId} onClose={() => setDetailPluginId(null)} />
  </div>;
}

function CapabilityStatus() {
  const fetcher = useCallback(async (): Promise<CapabilityCenterData> => {
    const agents = await api.listAgents();
    const effective = await Promise.all(agents.map((agent) => api.getEffectiveCapabilities(agent.employee_id)));
    return { agents, effective };
  }, []);
  const { data, loading, error, reload } = useAsyncData(fetcher);
  const [selectedId, setSelectedId] = useState<string>();
  const selected = useMemo(() => {
    if (!data?.effective.length) return undefined;
    return data.effective.find((row) => row.employee_id === selectedId) ?? data.effective[0];
  }, [data, selectedId]);

  if (loading) return <LoadingState rows={7} />;
  if (error) return <ErrorState onRetry={reload} />;

  const capabilities = selected?.capabilities ?? [];
  const available = capabilities.filter((item) => ['available', 'approval'].includes(item.status));
  const unavailable = capabilities.filter((item) => !['available', 'approval'].includes(item.status));
  const runtimeReady = ['ready', 'busy'].includes(selected?.runtime_state ?? '');

  return (
    <div>
      <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
        <div><Typography.Title level={2} className="!mb-1 !text-[26px]">运行能力核验</Typography.Title><Typography.Text type="secondary">以AI员工为中心核对知识授权、策略决策和运行环境</Typography.Text></div>
        <Button icon={<ReloadOutlined />} onClick={reload}>刷新状态</Button>
      </div>
      <Alert className="mb-4" type="info" showIcon message="当前为知识问答 MVP" description="流程、RPA、通用 AgentTeams 等历史实验能力不进入当前有效能力清单；页面只展示知识检索和受控的一次委派。" />
      <Card className="mb-4 border-line">
        <div className="flex flex-wrap items-center gap-3">
          <Typography.Text strong>查看AI员工</Typography.Text>
          <Select className="min-w-[280px]" value={selected?.employee_id} onChange={setSelectedId} options={(data?.agents ?? []).map((agent) => ({ value: agent.employee_id, label: `${agent.display_name} · ${agent.identity_kind === 'human_twin' ? '我的分身' : '岗位员工'}` }))} />
          <div className="flex-1" />
          <Tag color={selected?.knowledge_mode === 'internal' ? 'blue' : 'gold'}>{selected?.knowledge_mode === 'internal' ? '内部知识引擎' : 'Mock 知识'}</Tag>
          <Tag color={runtimeReady ? 'green' : 'red'}>运行环境 {runtimeReady ? '已就绪' : selected?.runtime_state ?? '未知'}</Tag>
        </div>
      </Card>
      <Row gutter={[16, 16]} className="mb-4">
        <Col xs={12} lg={6}><Card><Statistic title="有效能力" value={selected?.available_count ?? 0} prefix={<CheckCircleOutlined className="text-[var(--dwp-success)]" />} /></Card></Col>
        <Col xs={12} lg={6}><Card><Statistic title="需要处理" value={selected?.attention_count ?? 0} prefix={<ExclamationCircleOutlined className="text-[var(--dwp-danger)]" />} /></Card></Col>
        <Col xs={12} lg={6}><Card><Statistic title="知识库授权" value={capabilities.filter((item) => item.kind === 'knowledge' && item.authorized).length} prefix={<DatabaseOutlined />} /></Card></Col>
        <Col xs={12} lg={6}><Card><Statistic title="运行实例" value={runtimeReady ? '正常' : '异常'} prefix={<CloudServerOutlined />} /></Card></Col>
      </Row>
      <Card title={<Space><SafetyCertificateOutlined />现在可用的能力<Tag color="green">{available.length}</Tag></Space>} className="mb-4 border-line">
        {available.length ? <Row gutter={[12, 12]}>{available.map((item) => <CapabilityCard key={item.id} item={item} />)}</Row> : <Empty description="当前没有可用能力，请先检查授权和运行环境" />}
      </Card>
      {unavailable.length > 0 && <Card title={<Space><ExclamationCircleOutlined />尚不可用<Tag>{unavailable.length}</Tag></Space>} className="border-line"><Row gutter={[12, 12]}>{unavailable.map((item) => <CapabilityCard key={item.id} item={item} muted />)}</Row></Card>}
    </div>
  );
}

const CAPABILITY_KIND_META: Record<string, { icon: ReactNode; className: string }> = {
  delegation: { icon: <TeamOutlined />, className: 'bg-[var(--dwp-accent-soft)] text-[var(--dwp-accent)]' },
  skill: { icon: <FileMarkdownOutlined />, className: 'bg-[var(--dwp-brand-soft)] text-[var(--dwp-brand)]' },
  plugin: { icon: <CloudServerOutlined />, className: 'bg-[var(--dwp-success-soft)] text-[var(--dwp-success)]' },
  knowledge: { icon: <DatabaseOutlined />, className: 'bg-[var(--dwp-brand-soft)] text-[var(--dwp-brand)]' },
};

function CapabilityCard({ item, muted = false }: { item: EffectiveCapabilityItem; muted?: boolean }) {
  const status = STATUS_META[item.status];
  const kindMeta = CAPABILITY_KIND_META[item.kind] ?? CAPABILITY_KIND_META.knowledge;
  // 技能比插件多一层「有没有真投放到工作区」，投放态来自后端文件系统比对。
  const deploy = deployStateMeta(item.deploy_state);
  return (
    <Col xs={24} md={12} xl={8}>
      <Card size="small" className={muted ? 'h-full bg-surface-subtle' : 'h-full'}>
        <div className="flex items-start gap-3">
          <div className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-lg ${kindMeta.className}`}>{kindMeta.icon}</div>
          <div className="min-w-0 flex-1">
            <div className="flex items-start justify-between gap-2"><Typography.Text strong>{item.name}</Typography.Text><Tag color={status.color}>{status.label}</Tag></div>
            <Typography.Paragraph type="secondary" className="!mb-2 !mt-1 !text-xs" ellipsis={{ rows: 2 }}>{item.description}</Typography.Paragraph>
            <Space size={[4, 4]} wrap>
              {item.data_level && <Tag>{item.data_level}</Tag>}
              <Tag color={item.authorized ? 'blue' : 'default'}>{item.authorized ? '已授权' : '未授权'}</Tag>
              {deploy
                ? <Tag color={deploy.color}>{deploy.label}</Tag>
                : <Tag color={item.installed ? 'cyan' : 'default'}>{item.installed ? '已装载' : '未装载'}</Tag>}
            </Space>
            <div className="mt-2 text-xs text-ink-tertiary">{item.reason}</div>
            {item.kind === 'skill' && item.deploy_detail && (
              <div className="mt-1 text-xs text-ink-tertiary">
                投放：{item.deploy_detail}
                {item.deployed_at ? `（${formatServerTime(item.deployed_at)}）` : ''}
              </div>
            )}
            {item.example_prompts[0] && <div className="mt-2 rounded-md bg-surface-subtle px-2 py-1.5 text-xs text-ink-secondary">示例：{item.example_prompts[0]}</div>}
          </div>
        </div>
      </Card>
    </Col>
  );
}
