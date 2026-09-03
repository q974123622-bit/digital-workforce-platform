import { useCallback, useMemo, useState } from 'react';
import { CheckCircleOutlined, CloudServerOutlined, DatabaseOutlined, ExclamationCircleOutlined, ReloadOutlined, SafetyCertificateOutlined, TeamOutlined } from '@ant-design/icons';
import { Alert, Button, Card, Col, Empty, Form, Modal, Row, Select, Space, Statistic, Table, Tabs, Tag, Typography, message } from 'antd';
import type { PluginSubmission } from '../api/client';
import type { AgentProfile, EffectiveCapabilities, EffectiveCapabilityItem } from '@dwp/shared-schema';
import { api } from '../api/client';
import { ErrorState, LoadingState } from '../components/PageState';
import { useAsyncData } from '../hooks/useAsyncData';

interface CapabilityCenterData { agents: AgentProfile[]; effective: EffectiveCapabilities[] }

const STATUS_META: Record<EffectiveCapabilityItem['status'], { label: string; color: string }> = {
  available: { label: '现在可用', color: 'success' },
  approval: { label: '使用时需审批', color: 'warning' },
  unauthorized: { label: '未授权', color: 'default' },
  runtime_unavailable: { label: '运行环境未就绪', color: 'error' },
  disabled: { label: '已停用', color: 'default' },
};

export default function Plugins() {
  return <Tabs defaultActiveKey="effective" destroyOnHidden items={[
    { key: 'governance', label: '插件治理', children: <PluginGovernance /> },
    { key: 'effective', label: '运行能力核验', children: <CapabilityStatus /> },
  ]} />;
}

function PluginGovernance() {
  const fetcher = useCallback(async () => ({ plugins: await api.listAdminPlugins(), submissions: await api.listPluginSubmissions(), memory: await api.memoryHealth(), agents: await api.listAgents(), bindings: await api.listAgentPluginBindings() }), []);
  const { data, loading, error, reload } = useAsyncData(fetcher);
  const [bindingOpen, setBindingOpen] = useState(false);
  const [bindingForm] = Form.useForm();
  if (loading) return <LoadingState rows={7} />;
  if (error) return <ErrorState onRetry={reload} />;
  const act = async (row: PluginSubmission, action: 'approve' | 'reject' | 'publish') => {
    if (action === 'publish') await api.publishPlugin(row.plugin_id!, row.version!);
    else await api.reviewPlugin(row.id, action === 'approve');
    message.success(action === 'approve' ? '已批准，等待手动发布' : action === 'reject' ? '已拒绝' : '已发布'); reload();
  };
  return <div>
    <div className="mb-4"><Typography.Title level={3} className="!mb-1">插件治理</Typography.Title><Typography.Text type="secondary">统一管理 Skill 与 MCP 的审核、发布、版本和运行状态</Typography.Text></div>
    <Alert className="mb-4" showIcon type="info" message="审核与发布分离" description="批准只代表安全审核通过，管理员仍需手动发布；新版本发布失败不会影响旧版本。Hosted MCP 本机仅扫描，不执行源码。" />
    <Row gutter={[16,16]} className="mb-4"><Col xs={12} lg={6}><Card><Statistic title="插件总数" value={data?.plugins.length ?? 0} /></Card></Col><Col xs={12} lg={6}><Card><Statistic title="待审核" value={data?.submissions.filter((r) => r.review_status === 'pending').length ?? 0} /></Card></Col><Col xs={12} lg={6}><Card><Statistic title="长期记忆" value={data?.memory.memories ?? 0} /></Card></Col><Col xs={12} lg={6}><Card><Statistic title="mem0 状态" value={data?.memory.status === 'healthy' ? '正常' : '异常'} /></Card></Col></Row>
    <Card className="mb-4" title="版本审核与发布"><Table rowKey="id" dataSource={data?.submissions} columns={[
      { title: '插件', dataIndex: 'name' }, { title: '类型', dataIndex: 'plugin_type', render: (v) => <Tag color={v === 'skill' ? 'blue' : 'green'}>{v === 'skill' ? 'Skill' : 'MCP'}</Tag> },
      { title: '提交人', dataIndex: 'submitted_by' }, { title: '版本', dataIndex: 'version' }, { title: '审核', dataIndex: 'review_status' }, { title: '发布', dataIndex: 'publish_status' },
      { title: '操作', render: (_, row: PluginSubmission) => <Space>{row.review_status === 'pending' && <><Button size="small" type="primary" onClick={() => act(row,'approve')}>批准</Button><Button size="small" danger onClick={() => act(row,'reject')}>拒绝</Button></>}{row.review_status === 'approved' && row.publish_status !== 'published' && <Button size="small" type="primary" onClick={() => act(row,'publish')}>发布</Button>}</Space> },
    ]} /></Card>
    <Card title="数字员工插件配置" extra={<Button type="primary" onClick={() => setBindingOpen(true)}>新增授权</Button>}><Table rowKey="id" dataSource={data?.bindings} pagination={false} columns={[{ title: '数字员工', dataIndex: 'target_agent_id' }, { title: '插件', dataIndex: 'plugin_id' }, { title: '管理员', dataIndex: 'admin_enabled', render: (v: boolean) => <Tag color={v ? 'green' : 'red'}>{v ? '已授权' : '已停用'}</Tag> }, { title: '员工', dataIndex: 'employee_enabled', render: (v: boolean) => <Tag color={v ? 'blue' : 'default'}>{v ? '已启用' : '未启用'}</Tag> }]} /></Card>
    <Modal title="授权插件给数字员工" open={bindingOpen} onCancel={() => setBindingOpen(false)} onOk={async () => { const value = await bindingForm.validateFields(); await api.createAgentPluginBinding(value.plugin_id, value.target_agent_id); message.success('授权已创建'); setBindingOpen(false); bindingForm.resetFields(); reload(); }}><Form form={bindingForm} layout="vertical"><Form.Item name="plugin_id" label="已发布插件" rules={[{ required: true }]}><Select options={(data?.plugins ?? []).filter((row) => row.current_version).map((row) => ({ value: String(row.id), label: `${row.name} · ${row.current_version}` }))} /></Form.Item><Form.Item name="target_agent_id" label="目标数字员工" rules={[{ required: true }]}><Select options={(data?.agents ?? []).map((row) => ({ value: row.employee_id, label: row.display_name }))} /></Form.Item></Form></Modal>
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
        <div><Typography.Title level={3} className="!mb-1">知识与能力</Typography.Title><Typography.Text type="secondary">以数字员工为中心核对知识授权、Policy 决策和 Harness 实际运行状态</Typography.Text></div>
        <Button icon={<ReloadOutlined />} onClick={reload}>刷新状态</Button>
      </div>
      <Alert className="mb-4" type="info" showIcon message="当前为知识问答 MVP" description="流程、RPA、通用 AgentTeams 等历史实验能力不进入当前有效能力清单；页面只展示知识检索和受控的一次委派。" />
      <Card className="mb-4 border-[#e5e6eb]">
        <div className="flex flex-wrap items-center gap-3">
          <Typography.Text strong>查看数字员工</Typography.Text>
          <Select className="min-w-[280px]" value={selected?.employee_id} onChange={setSelectedId} options={(data?.agents ?? []).map((agent) => ({ value: agent.employee_id, label: `${agent.display_name} · ${agent.identity_kind === 'human_twin' ? '数字分身' : '岗位员工'}` }))} />
          <div className="flex-1" />
          <Tag color={selected?.knowledge_mode === 'internal' ? 'blue' : 'gold'}>{selected?.knowledge_mode === 'internal' ? '内部知识引擎' : 'Mock 知识'}</Tag>
          <Tag color={runtimeReady ? 'green' : 'red'}>Harness {runtimeReady ? '已就绪' : selected?.runtime_state ?? '未知'}</Tag>
        </div>
      </Card>
      <Row gutter={[16, 16]} className="mb-4">
        <Col xs={12} lg={6}><Card><Statistic title="有效能力" value={selected?.available_count ?? 0} prefix={<CheckCircleOutlined className="text-[#00b42a]" />} /></Card></Col>
        <Col xs={12} lg={6}><Card><Statistic title="需要处理" value={selected?.attention_count ?? 0} prefix={<ExclamationCircleOutlined className="text-[#f53f3f]" />} /></Card></Col>
        <Col xs={12} lg={6}><Card><Statistic title="知识库授权" value={capabilities.filter((item) => item.kind === 'knowledge' && item.authorized).length} prefix={<DatabaseOutlined />} /></Card></Col>
        <Col xs={12} lg={6}><Card><Statistic title="运行实例" value={runtimeReady ? '正常' : '异常'} prefix={<CloudServerOutlined />} /></Card></Col>
      </Row>
      <Card title={<Space><SafetyCertificateOutlined />现在可用的能力<Tag color="green">{available.length}</Tag></Space>} className="mb-4 border-[#e5e6eb]">
        {available.length ? <Row gutter={[12, 12]}>{available.map((item) => <CapabilityCard key={item.id} item={item} />)}</Row> : <Empty description="当前没有可用能力，请先检查授权和 Harness" />}
      </Card>
      {unavailable.length > 0 && <Card title={<Space><ExclamationCircleOutlined />尚不可用<Tag>{unavailable.length}</Tag></Space>} className="border-[#e5e6eb]"><Row gutter={[12, 12]}>{unavailable.map((item) => <CapabilityCard key={item.id} item={item} muted />)}</Row></Card>}
    </div>
  );
}

function CapabilityCard({ item, muted = false }: { item: EffectiveCapabilityItem; muted?: boolean }) {
  const status = STATUS_META[item.status];
  return (
    <Col xs={24} md={12} xl={8}>
      <Card size="small" className={muted ? 'h-full bg-[#f7f8fa]' : 'h-full'}>
        <div className="flex items-start gap-3">
          <div className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-lg ${item.kind === 'delegation' ? 'bg-[#f2ebff] text-[#722ed1]' : 'bg-[#e8f3ff] text-[#165dff]'}`}>{item.kind === 'delegation' ? <TeamOutlined /> : <DatabaseOutlined />}</div>
          <div className="min-w-0 flex-1">
            <div className="flex items-start justify-between gap-2"><Typography.Text strong>{item.name}</Typography.Text><Tag color={status.color}>{status.label}</Tag></div>
            <Typography.Paragraph type="secondary" className="!mb-2 !mt-1 !text-xs" ellipsis={{ rows: 2 }}>{item.description}</Typography.Paragraph>
            <Space size={[4, 4]} wrap>{item.data_level && <Tag>{item.data_level}</Tag>}<Tag color={item.authorized ? 'blue' : 'default'}>{item.authorized ? '已授权' : '未授权'}</Tag><Tag color={item.installed ? 'cyan' : 'default'}>{item.installed ? '已装载' : '未装载'}</Tag></Space>
            <div className="mt-2 text-xs text-[#86909c]">{item.reason}</div>
            {item.example_prompts[0] && <div className="mt-2 rounded-md bg-[#f7f8fa] px-2 py-1.5 text-xs text-[#4e5969]">示例：{item.example_prompts[0]}</div>}
          </div>
        </div>
      </Card>
    </Col>
  );
}
