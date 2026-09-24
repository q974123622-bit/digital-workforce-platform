import {
  CheckCircleOutlined,
  CloudServerOutlined,
  FileMarkdownOutlined,
  LockOutlined,
  ToolOutlined,
} from '@ant-design/icons';
import { Alert, Button, Drawer, Empty, Skeleton, Tag, Typography } from 'antd';
import { useEffect, useMemo, useState } from 'react';
import { api, type PluginMarketState, type PluginSafeDetail } from '../../api/client';
import { formatServerTime } from '../../utils/serverTime';
import { MCP_CATEGORY_LABELS, pluginSourceMeta } from './pluginMeta';

const DEPLOYMENT_LABELS: Record<string, string> = {
  instruction: '受控指令',
  external: '外部服务',
  hosted: '平台托管',
};

const STATUS_LABELS: Record<string, string> = {
  approved: '审核通过',
  pending: '待审核',
  rejected: '已退回',
  published: '已发布',
  superseded: '已被替代',
  draft: '草稿',
  healthy: '正常',
  ready: '就绪',
  running: '运行中',
  unavailable: '不可用',
  mock: 'Mock',
  not_configured: '未接入',
  unknown: '未知',
};

function displayStatus(value: string | null | undefined) {
  return value ? STATUS_LABELS[value] ?? value : '尚未检查';
}

export default function PluginDetailDrawer({
  pluginId,
  admin = false,
  enabled,
  marketState,
  refreshToken = 0,
  onClose,
  onToggle,
  onRequest,
}: {
  pluginId: string | null;
  admin?: boolean;
  enabled?: boolean;
  /** 市场货架上的六态；未提供时按授权字段回落推断。 */
  marketState?: PluginMarketState;
  refreshToken?: number;
  onClose: () => void;
  onToggle?: (pluginId: string, enabled: boolean) => void;
  /** 未授权时提供「申请使用」入口；缺省时按钮置灰。 */
  onRequest?: (pluginId: string) => void;
}) {
  const [detail, setDetail] = useState<PluginSafeDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    let active = true;
    if (!pluginId) {
      setDetail(null);
      setError('');
      return () => { active = false; };
    }
    setLoading(true);
    setError('');
    (admin ? api.getAdminPluginDetail(pluginId) : api.getMyPluginDetail(pluginId))
      .then((value) => active && setDetail(value))
      .catch((reason) => active && setError(reason instanceof Error ? reason.message : '插件详情加载失败'))
      .finally(() => active && setLoading(false));
    return () => { active = false; };
  }, [admin, pluginId, refreshToken]);

  const category = useMemo(() => {
    if (!detail) return '';
    if (detail.plugin_type === 'skill') {
      return detail.skill_category ? `Skill · ${detail.skill_category}` : 'Skill · 工作方法';
    }
    return `${MCP_CATEGORY_LABELS[detail.mcp_category ?? 'other'] ?? '其他'} MCP`;
  }, [detail]);

  // 真实运行时未接入（state=not_configured/unknown/pending）时不得显示为可用。
  // `running` 是 External MCP（含 URL 自助接入）探测成功后的真实状态。
  const runtimeConnected = ['ready', 'busy', 'running'].includes(detail?.runtime?.state ?? '');

  // 底部操作区与市场卡片保持同一套状态语义：未授权时给出「申请使用」而不是死按钮。
  const state: PluginMarketState | null = detail
    ? marketState ?? (enabled ? 'enabled' : detail.authorization.admin_enabled ? 'granted' : 'requestable')
    : null;
  const footerNote = (() => {
    switch (state) {
      case 'blocked': return '管理员已禁止该插件';
      case 'pending': return '申请已提交，等待管理员审批';
      case 'own_draft': return '尚未发布，先提交新版本';
      case 'enabled':
      case 'granted': return '管理员已授权使用';
      case 'requestable': return '管理员尚未授权，可提交使用申请';
      default: return '管理员尚未授权';
    }
  })();
  const footerAction = (() => {
    if (!detail) return null;
    if (state === 'blocked') return <Button disabled title="管理员已禁止该插件">不可申请</Button>;
    if (state === 'pending') return <Button disabled title="申请已提交，等待管理员审批">申请中</Button>;
    if (state === 'own_draft') return <Button disabled title="请先提交新版本并通过审核">尚未发布</Button>;
    if (detail.authorization.admin_enabled) {
      return (
        <Button
          type={enabled ? 'default' : 'primary'}
          onClick={() => onToggle?.(detail.plugin_id, !enabled)}
        >
          {enabled ? '停用插件' : '启用插件'}
        </Button>
      );
    }
    if (onRequest) {
      return <Button type="primary" onClick={() => onRequest(detail.plugin_id)}>申请使用</Button>;
    }
    return <Button disabled>管理员尚未授权</Button>;
  })();

  return (
    <Drawer
      className="plugin-detail-drawer"
      title={null}
      width={500}
      open={Boolean(pluginId)}
      onClose={onClose}
      footer={detail && !admin && (onToggle || onRequest) ? (
        <div className="flex items-center justify-between gap-3">
          <Typography.Text type="secondary" className="!text-xs">{footerNote}</Typography.Text>
          {footerAction}
        </div>
      ) : undefined}
    >
      {loading && <Skeleton active paragraph={{ rows: 8 }} />}
      {!loading && error && <Alert type="error" showIcon message="无法读取插件详情" description={error} />}
      {!loading && !error && detail && (
        <div className="space-y-7">
          <header className="plugin-detail-hero">
            <span className={`plugin-detail-icon ${detail.plugin_type}`}>
              {detail.plugin_type === 'skill' ? <FileMarkdownOutlined /> : <CloudServerOutlined />}
            </span>
            <div className="min-w-0">
              <div className="mb-1 flex items-center gap-2 text-xs font-medium text-ink-tertiary">
                <span>{category}</span>
                {detail.featured && <Tag color="gold" className="!mr-0">精选</Tag>}
              </div>
              <Typography.Title level={3} className="!mb-2 !text-[24px]">{detail.name}</Typography.Title>
              {/* 货架卡片折叠三行，这里展示简介全文 */}
              <Typography.Paragraph className="!mb-0 !leading-7 !text-ink-secondary">
                {detail.description}
              </Typography.Paragraph>
            </div>
          </header>

          <section>
            <div className="plugin-detail-section-title">基本信息</div>
            <dl className="plugin-detail-facts">
              <div><dt>插件类型</dt><dd>{detail.plugin_type === 'skill' ? 'Skill' : 'MCP'}</dd></div>
              <div><dt>来源</dt><dd>{pluginSourceMeta(detail.source).label}</dd></div>
              <div><dt>使用范围</dt><dd>{detail.scope === 'shared' ? '企业共享' : '仅个人'}</dd></div>
              {detail.plugin_type === 'skill' && (
                <div><dt>业务分类</dt><dd>{detail.skill_category || '尚未标注'}</dd></div>
              )}
              <div><dt>当前版本</dt><dd>{detail.current_version ? `v${detail.current_version}` : '尚未发布'}</dd></div>
              <div><dt>数据等级</dt><dd>{detail.version?.data_level ?? '尚未标注'}</dd></div>
              <div><dt>部署方式</dt><dd>{DEPLOYMENT_LABELS[detail.version?.deployment_mode ?? ''] ?? '尚未检查'}</dd></div>
              <div><dt>有效状态</dt><dd>{detail.authorization.effective ? '当前可用' : '尚未生效'}</dd></div>
            </dl>
          </section>

          {detail.version?.review_status === 'rejected' && (
            <Alert
              type="warning"
              showIcon
              message="该版本已被驳回"
              description={detail.version.review_note || '管理员未填写驳回原因，请修改后重新提交。'}
            />
          )}

          {detail.plugin_type === 'skill' && (
            <section>
              <div className="plugin-detail-section-title"><FileMarkdownOutlined /> 工作方法摘要</div>
              <div className="plugin-detail-note">{detail.instruction_summary || '暂未提供工作方法摘要'}</div>
              <div className="mt-2 flex items-center gap-1.5 text-xs text-ink-tertiary"><LockOutlined />完整内部指令不会在界面中展示</div>
            </section>
          )}

          {detail.plugin_type === 'mcp' && (
            <section>
              <div className="plugin-detail-section-title"><ToolOutlined /> 可调用工具</div>
              {detail.tools.length ? (
                <div className="divide-y divide-line-light border-y border-line-light">
                  {detail.tools.map((tool) => (
                    <div key={tool.name} className="py-3">
                      <div className="font-medium text-ink">{tool.name}</div>
                      <div className="mt-1 text-sm leading-6 text-ink-secondary">{tool.description}</div>
                    </div>
                  ))}
                </div>
              ) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂未公开工具清单" />}
            </section>
          )}

          <section>
            <div className="plugin-detail-section-title"><CheckCircleOutlined /> 授权与运行</div>
            <div className="flex flex-wrap gap-2">
              <Tag color={detail.authorization.admin_enabled ? 'success' : 'default'}>管理员{detail.authorization.admin_enabled ? '已授权' : '未授权'}</Tag>
              <Tag color={detail.authorization.employee_enabled ? 'blue' : 'default'}>员工{detail.authorization.employee_enabled ? '已启用' : '未启用'}</Tag>
              {/* 未接入真实运行时就不显示「正常」：state=not_configured/unknown 一律如实标注。 */}
              <Tag color={runtimeConnected ? 'success' : 'default'}>
                Runtime {runtimeConnected ? '已接入' : '未接入真实运行时'}
              </Tag>
            </div>
            <div className="mt-3 text-xs text-ink-tertiary">
              最近检查：{detail.runtime?.updated_at ? formatServerTime(detail.runtime.updated_at) : '尚未检查'}
              {!runtimeConnected && detail.runtime ? ` · 原始状态：${displayStatus(detail.runtime.state)}` : ''}
            </div>
          </section>

          {admin && (
            <>
              <section>
                <div className="plugin-detail-section-title">版本历史</div>
                <div className="space-y-2">
                  {(detail.versions ?? []).map((version) => (
                    <div key={`${version.version}-${version.created_at}`} className="plugin-governance-row">
                      <div><strong>v{version.version}</strong><span>{version.data_level} · {DEPLOYMENT_LABELS[version.deployment_mode] ?? version.deployment_mode}</span></div>
                      <div><Tag>{displayStatus(version.review_status)}</Tag><Tag>{displayStatus(version.publish_status)}</Tag></div>
                    </div>
                  ))}
                  {!detail.versions?.length && <Typography.Text type="secondary">暂无版本记录</Typography.Text>}
                </div>
              </section>
              <section>
                <div className="plugin-detail-section-title">授权对象</div>
                <div className="space-y-2">
                  {(detail.bindings ?? []).map((binding) => (
                    <div key={binding.id} className="plugin-governance-row">
                      <div><strong>{binding.target_agent_id}</strong><span>优先级 {binding.priority}</span></div>
                      <Tag color={binding.admin_enabled && binding.employee_enabled ? 'success' : 'default'}>
                        {binding.admin_enabled && binding.employee_enabled ? '已启用' : '未生效'}
                      </Tag>
                    </div>
                  ))}
                  {!detail.bindings?.length && <Typography.Text type="secondary">尚未授权给AI员工</Typography.Text>}
                </div>
              </section>
            </>
          )}
        </div>
      )}
    </Drawer>
  );
}
