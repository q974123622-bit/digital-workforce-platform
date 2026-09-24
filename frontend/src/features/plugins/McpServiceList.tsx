import { CloudServerOutlined, EditOutlined, PlusOutlined, ReloadOutlined } from '@ant-design/icons';
import { Alert, Button, Card, Col, Modal, Row, Space, Statistic, Table, Tag, Tooltip, Typography, message } from 'antd';
import { useCallback, useState } from 'react';
import { api } from '../../api/client';
import type { McpRegisteredService } from '../../api/client';
import { ErrorState, LoadingState } from '../../components/PageState';
import { useAsyncData } from '../../hooks/useAsyncData';
import { formatServerTime } from '../../utils/serverTime';
import McpOnboardingWizard from './McpOnboardingWizard';
import McpServiceEditModal from './McpServiceEditModal';
import PluginDetailDrawer from './PluginDetailDrawer';

const RUNTIME_META: Record<string, { label: string; color: string }> = {
  running: { label: '连通正常', color: 'success' },
  unavailable: { label: '连接失败', color: 'error' },
  pending: { label: '接入中', color: 'warning' },
  not_configured: { label: '未接入运行时', color: 'default' },
};

/** 连通状态口径：优先看最近一次探测结论，历史数据回落到运行实例状态。 */
function runtimeKey(row: McpRegisteredService): string | null {
  if (row.probe_state === 'reachable') return 'running';
  return row.probe_state ?? row.runtime_state;
}

/** 服务当前状态：已发布 / 待审核 / 未发布，供列表与操作列复用。 */
function versionTag(row: McpRegisteredService) {
  if (row.current_version) return <Tag color="green">v{row.current_version}</Tag>;
  if (row.pending_version_id) return <Tag color="orange">待审核</Tag>;
  return <Tag>未发布</Tag>;
}

/**
 * 管理端「MCP 接入」页签。
 *
 * 展示已登记的 URL 接入 MCP 服务（名称、地址、工具数、连通状态、最近探测时间），
 * 支持重新测试连接、查看工具清单、跳转到授权页；右上角是接入向导入口。
 */
export default function McpServiceList({
  onGoAuthorize,
  onGoReview,
}: {
  /** 跳到「插件治理」页签的授权表格。 */
  onGoAuthorize: () => void;
  /** 跳到「插件治理」页签的版本审核表格。 */
  onGoReview: () => void;
}) {
  const fetcher = useCallback(() => api.listAdminMcpServices(), []);
  const { data, loading, error, reload } = useAsyncData(fetcher);
  const [open, setOpen] = useState(false);
  const [detailPluginId, setDetailPluginId] = useState<string | null>(null);
  const [editTarget, setEditTarget] = useState<McpRegisteredService | null>(null);
  const [reprobeingId, setReprobingId] = useState<string | null>(null);
  const [governingId, setGoverningId] = useState<string | null>(null);
  // 下架/恢复用原位内联确认（不依赖 Popconfirm，浮层在部分壳内不可靠）。
  const [confirming, setConfirming] = useState<{ id: string; action: 'withdrawn' | 'active' } | null>(null);

  if (loading) return <LoadingState rows={6} />;
  if (error) return <ErrorState onRetry={reload} />;

  const services = data?.services ?? [];
  const healthy = services.filter((row) => runtimeKey(row) === 'running').length;
  const toolTotal = services.reduce((sum, row) => sum + (row.tools_count ?? 0), 0);
  const attention = services.filter(
    (row) => row.pending_version_id || runtimeKey(row) === 'unavailable',
  ).length;

  const reprobe = async (pluginId: string) => {
    setReprobingId(pluginId);
    try {
      const result = await api.reprobeAdminMcpService(pluginId);
      message.success(`连通正常，解析到 ${result.tools_count} 个工具（${result.latency_ms} ms）`);
    } catch (err) {
      // 失败原因已由后端落库，刷新后行内即可看到摘要，不必只靠这条提示。
      message.error(`测试连接失败：${(err as Error).message}`);
    } finally {
      setReprobingId(null);
      reload();
    }
  };

  const applyGovernance = async (pluginId: string, status: 'withdrawn' | 'active') => {
    setGoverningId(pluginId);
    try {
      await api.updatePluginGovernance(pluginId, { status });
      message.success(status === 'withdrawn' ? '已下架，员工侧工具清单立即失效' : '已恢复上架，授权需另行配置');
      setConfirming(null);
      reload();
    } catch (err) {
      message.error(`操作失败：${(err as Error).message}`);
    } finally {
      setGoverningId(null);
    }
  };

  return (
    <div>
      <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
        <div>
          <Typography.Title level={2} className="!mb-1 !text-[26px]">MCP 接入</Typography.Title>
          <Typography.Text type="secondary">
            系统运行期间填写服务地址与访问方式，平台自动解析工具并由大模型补写中文简介，确认后即可授权给AI员工
          </Typography.Text>
        </div>
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setOpen(true)}>
          接入新的 MCP 服务
        </Button>
      </div>

      <Alert
        className="mb-4"
        showIcon
        type="info"
        message="登记即发布，授权后才生效"
        description="管理端登记后立即发布；到「插件治理」的「AI员工插件配置」里授权给指定AI员工，该员工会话中的工具清单才会出现这些工具。已登记的服务可随时编辑信息、地址与密钥，改地址或密钥会立即重测；下架后员工侧工具清单立即失效。密钥加密保存，界面只显示掩码。"
      />

      <Row gutter={0} className="admin-metrics mb-4 overflow-hidden rounded-xl border border-[var(--dwp-border-light)] bg-surface">
        <Col xs={12} lg={6}><Card variant="borderless"><Statistic title="已登记服务" value={services.length} /></Card></Col>
        <Col xs={12} lg={6}><Card variant="borderless"><Statistic title="连通正常" value={healthy} /></Card></Col>
        <Col xs={12} lg={6}><Card variant="borderless"><Statistic title="工具总数" value={toolTotal} /></Card></Col>
        <Col xs={12} lg={6}><Card variant="borderless"><Statistic title="待处理" value={attention} /></Card></Col>
      </Row>

      {data && !data.credential_encryption_available && (
        <Alert
          className="mb-4"
          type="warning"
          showIcon
          message="未配置凭据加密密钥"
          description="当前只能接入无鉴权服务；需要保存密钥时请让运维设置 DWP_MCP_CREDENTIAL_KEY 或放置 /run/secrets/mcp_credential_key。"
        />
      )}

      <Card className="border-line" title={<Space><CloudServerOutlined />已登记服务</Space>}>
        <Table<McpRegisteredService>
          rowKey="plugin_id"
          dataSource={services}
          pagination={false}
          locale={{ emptyText: '还没有通过 URL 接入的 MCP 服务，点击右上角开始接入' }}
          columns={[
            {
              title: '服务',
              dataIndex: 'name',
              render: (value: string, row) => (
                <div>
                  <div className="text-ink">{value}</div>
                  <div className="font-mono text-xs text-ink-tertiary">{row.plugin_id}</div>
                </div>
              ),
            },
            {
              title: '地址',
              dataIndex: 'url',
              width: 260,
              render: (value: string) => (
                <Tooltip title={value}>
                  <Typography.Text className="!font-mono !text-xs" ellipsis>{value}</Typography.Text>
                </Tooltip>
              ),
            },
            {
              title: '鉴权',
              dataIndex: 'authenticated',
              width: 140,
              render: (value: boolean, row) => (
                value
                  ? <Tag color="blue">{row.secret_hint || row.auth_header_name || '已配置'}</Tag>
                  : <Tag>匿名</Tag>
              ),
            },
            {
              title: '工具数',
              dataIndex: 'tools_count',
              width: 90,
              render: (value: number | null) => (value === null ? '—' : value),
            },
            {
              title: '连通状态',
              dataIndex: 'runtime_state',
              width: 120,
              render: (_: unknown, row) => {
                const key = runtimeKey(row);
                const meta = key ? RUNTIME_META[key] : undefined;
                return <Tag color={meta?.color ?? 'default'}>{meta?.label ?? '未发布'}</Tag>;
              },
            },
            {
              title: '失败原因',
              dataIndex: 'last_error',
              width: 200,
              render: (value: string, row) => {
                if (runtimeKey(row) !== 'unavailable' || !value) {
                  return <span className="text-xs text-ink-tertiary">—</span>;
                }
                return (
                  <Tooltip title={value}>
                    <span className="line-clamp-2 text-xs text-[var(--dwp-danger)]">{value}</span>
                  </Tooltip>
                );
              },
            },
            {
              title: '最近探测',
              dataIndex: 'checked_at',
              width: 160,
              render: (value: string | null, row) => (
                <div className="text-xs text-ink-secondary">
                  {value ? formatServerTime(value) : '—'}
                  {row.latency_ms ? <span className="ml-1 text-ink-tertiary">({row.latency_ms} ms)</span> : null}
                </div>
              ),
            },
            {
              title: '版本',
              dataIndex: 'current_version',
              width: 120,
              render: (_: unknown, row) => (
                <Space size={4} wrap>
                  {versionTag(row)}
                  {row.status === 'withdrawn' && <Tag color="default">已下架</Tag>}
                </Space>
              ),
            },
            {
              title: '操作',
              width: 330,
              render: (_: unknown, row) => (
                <Space size={4} wrap>
                  <Button
                    size="small"
                    icon={<ReloadOutlined />}
                    loading={reprobeingId === row.plugin_id}
                    onClick={() => void reprobe(row.plugin_id)}
                  >
                    重新测试
                  </Button>
                  <Button size="small" icon={<EditOutlined />} onClick={() => setEditTarget(row)}>编辑</Button>
                  <Button size="small" onClick={() => setDetailPluginId(row.plugin_id)}>查看工具</Button>
                  {row.pending_version_id
                    ? <Button size="small" type="primary" onClick={onGoReview}>去审核</Button>
                    : row.current_version
                      ? <Button size="small" onClick={onGoAuthorize}>去授权</Button>
                      : null}
                  {confirming?.id === row.plugin_id ? (
                    <Space size={4}>
                      <Button
                        size="small"
                        type="primary"
                        danger={confirming.action === 'withdrawn'}
                        loading={governingId === row.plugin_id}
                        onClick={() => void applyGovernance(row.plugin_id, confirming.action)}
                      >
                        {confirming.action === 'withdrawn' ? '确认下架' : '确认恢复'}
                      </Button>
                      <Button size="small" onClick={() => setConfirming(null)}>取消</Button>
                    </Space>
                  ) : (
                    <Button
                      size="small"
                      danger={row.status === 'active'}
                      onClick={() => setConfirming({
                        id: row.plugin_id,
                        action: row.status === 'active' ? 'withdrawn' : 'active',
                      })}
                    >
                      {row.status === 'active' ? '下架' : '恢复上架'}
                    </Button>
                  )}
                </Space>
              ),
            },
          ]}
        />
      </Card>

      <Modal
        title="接入 MCP 服务"
        open={open}
        onCancel={() => setOpen(false)}
        footer={null}
        width={760}
        destroyOnClose
      >
        <McpOnboardingWizard
          mode="admin"
          encryptionAvailable={data?.credential_encryption_available ?? true}
          onDone={() => { setOpen(false); reload(); }}
        />
      </Modal>

      <McpServiceEditModal
        service={editTarget}
        open={Boolean(editTarget)}
        onClose={() => setEditTarget(null)}
        onDone={() => { setEditTarget(null); reload(); }}
      />

      <PluginDetailDrawer admin pluginId={detailPluginId} onClose={() => setDetailPluginId(null)} />
    </div>
  );
}
