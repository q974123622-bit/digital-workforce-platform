import { ApiOutlined, CheckCircleOutlined, PlusOutlined, ReloadOutlined } from '@ant-design/icons';
import { Button, Empty, Space, Tabs, Tag, Typography } from 'antd';
import type { KnowledgeMarketItem, McpCredentialAlert, PluginSubmission, UnifiedPlugin } from '../../api/client';
import { formatServerTime } from '../../utils/serverTime';
import KnowledgeMarketPanel from '../knowledge/KnowledgeMarketPanel';
import PluginIcon from './PluginIcon';
import PluginMarketGrid from './PluginMarketGrid';
import type { PluginVersionTarget } from './PluginMarketGrid';
import {
  MCP_CATEGORY_LABELS,
  PLUGIN_DESCRIPTION_CLAMP,
  PLUGIN_TYPE_LABEL,
  credentialAlertColor,
  credentialAlertLabel,
  pluginDescription,
  pluginScopeLabel,
  pluginSourceMeta,
} from './pluginMeta';

const { Text } = Typography;

function pluginIdOf(item: UnifiedPlugin): string {
  return item.plugin_id ?? String(item.id ?? '');
}

/** 被驳回的提交：复用同一版本号重新上传，不产生孤立版本。 */
function ResubmitButton({ versionId, onResubmit }: { versionId: number; onResubmit: (versionId: number, file: File) => void }) {
  return (
    <label className="inline-flex cursor-pointer items-center gap-1 rounded-control border border-line px-2 py-0.5 text-xs text-ink-secondary hover:border-brand hover:text-brand">
      <ReloadOutlined />
      重新提交
      <input
        type="file"
        accept=".zip,application/zip"
        className="hidden"
        onChange={(event) => {
          const file = event.target.files?.[0];
          if (file) onResubmit(versionId, file);
          event.target.value = '';
        }}
      />
    </label>
  );
}

export default function PluginCatalog({
  market,
  effective,
  submissions,
  knowledgeBases,
  credentialAlerts = {},
  onEnable,
  onUpload,
  onOpenDetail,
  onResubmit,
  onNewVersion,
  onRequest,
  onKnowledgeChanged,
  onConnectMcp,
}: {
  market: UnifiedPlugin[];
  effective: UnifiedPlugin[];
  submissions: PluginSubmission[];
  /** 知识库市场货架（全部知识库 + 本人分身状态）。 */
  knowledgeBases: KnowledgeMarketItem[];
  /** 本人用户级 MCP 令牌到期提醒，按 plugin_id 索引（货架与已启用列表行内标出）。 */
  credentialAlerts?: Record<string, McpCredentialAlert>;
  onEnable: (pluginId: string, enabled: boolean) => void;
  onUpload: () => void;
  onOpenDetail: (pluginId: string) => void;
  onResubmit: (versionId: number, file: File) => void;
  onNewVersion: (target: PluginVersionTarget) => void;
  onRequest: (item: UnifiedPlugin) => void;
  /** 知识库申请成功后刷新货架与页签计数。 */
  onKnowledgeChanged: () => void;
  /** 员工自助接入 MCP 服务（填地址 → 自动解析 → AI 补简介 → 提交审核）。 */
  onConnectMcp: () => void;
}) {
  // 三个市场是同一份货架按类型拆开：授权事实与六态计算完全共用，这里只做展示分组。
  const mcpMarket = market.filter((item) => item.plugin_type === 'mcp');
  const skillMarket = market.filter((item) => item.plugin_type === 'skill');
  return (
    <Tabs
      items={[
        {
          key: 'mcp',
          label: `MCP 市场（${mcpMarket.length}）`,
          children: (
            <PluginMarketGrid
              market={mcpMarket}
              fixedType="mcp"
              credentialAlerts={credentialAlerts}
              onEnable={onEnable}
              onOpenDetail={onOpenDetail}
              onNewVersion={onNewVersion}
              onRequest={onRequest}
            />
          ),
        },
        {
          key: 'skill',
          label: `技能市场（${skillMarket.length}）`,
          children: (
            <PluginMarketGrid
              market={skillMarket}
              fixedType="skill"
              onEnable={onEnable}
              onOpenDetail={onOpenDetail}
              onNewVersion={onNewVersion}
              onRequest={onRequest}
            />
          ),
        },
        {
          key: 'knowledge',
          label: `知识库市场（${knowledgeBases.length}）`,
          children: (
            <KnowledgeMarketPanel items={knowledgeBases} onChanged={onKnowledgeChanged} />
          ),
        },
        {
          key: 'enabled',
          label: `已启用（${effective.length}）`,
          children: (
            <div className="plugin-catalog-grid grid gap-3 pb-6 md:grid-cols-2">
              {effective.map((item) => {
                const pluginId = pluginIdOf(item);
                return (
                  <article
                    key={pluginId}
                    role="button"
                    tabIndex={0}
                    aria-label={`查看插件 ${item.name}`}
                    onClick={() => onOpenDetail(pluginId)}
                    onKeyDown={(event) => { if (event.currentTarget === event.target && (event.key === 'Enter' || event.key === ' ')) onOpenDetail(pluginId); }}
                    className="plugin-card flex min-h-[168px] cursor-pointer flex-col rounded-container bg-surface p-4"
                  >
                    <div className="flex items-start gap-3">
                      <PluginIcon pluginType={item.plugin_type} />
                      <div className="min-w-0 flex-1">
                        <Text strong className="block truncate">{item.name}</Text>
                        <div className="mt-1.5 flex flex-wrap gap-1">
                          {item.featured && <Tag color="gold" className="!mr-0">精选</Tag>}
                          <Tag>{PLUGIN_TYPE_LABEL[item.plugin_type] ?? item.plugin_type}</Tag>
                          <Tag color="green">v{item.version ?? item.current_version ?? '—'}</Tag>
                          {item.scope && <Tag color={item.scope === 'shared' ? 'blue' : 'default'}>{pluginScopeLabel(item.scope)}</Tag>}
                          <Tag color={pluginSourceMeta(item.source).color}>{pluginSourceMeta(item.source).label}</Tag>
                          {item.plugin_type === 'skill' && item.skill_category && (
                            <Tag color="geekblue">{item.skill_category}</Tag>
                          )}
                          {item.plugin_type === 'mcp' && item.mcp_category && (
                            <Tag>{MCP_CATEGORY_LABELS[item.mcp_category] ?? item.mcp_category}</Tag>
                          )}
                          {item.data_level && <Tag>{item.data_level}</Tag>}
                          {credentialAlerts[pluginId] && (
                            <Tag color={credentialAlertColor(credentialAlerts[pluginId].state)} className="!mr-0">
                              {credentialAlertLabel(
                                credentialAlerts[pluginId].state,
                                credentialAlerts[pluginId].days_left,
                              )}
                            </Tag>
                          )}
                        </div>
                        {/* 简介与货架卡片同口径折叠三行，完整内容在详情抽屉里看。 */}
                        <p className={`mb-0 mt-3 text-xs leading-5 text-ink-secondary ${PLUGIN_DESCRIPTION_CLAMP}`}>
                          {pluginDescription(item)}
                        </p>
                      </div>
                    </div>
                    <Button size="small" className="mt-auto self-start" onClick={(event) => { event.stopPropagation(); onEnable(pluginId, false); }}>
                      停用
                    </Button>
                  </article>
                );
              })}
              {effective.length === 0 && <Empty description="暂未启用插件" className="md:col-span-2" />}
            </div>
          ),
        },
        {
          key: 'submissions',
          label: `我的提交（${submissions.length}）`,
          children: (
            <div className="space-y-2 pb-6">
              {submissions.map((item) => (
                <div key={item.id} className="rounded-container border border-line bg-surface p-3">
                  <div className="flex items-center gap-3">
                    <PluginIcon pluginType={item.plugin_type} />
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2">
                        <Text strong>{item.name}</Text>
                        <Tag>v{item.version}</Tag>
                      </div>
                      <div className="text-xs text-ink-secondary">
                        {PLUGIN_TYPE_LABEL[item.plugin_type] ?? item.plugin_type} · {item.data_level} · 提交于 {formatServerTime(item.created_at)}
                      </div>
                    </div>
                    <Tag color={item.review_status === 'approved' ? 'green' : item.review_status === 'rejected' ? 'red' : 'warning'}>
                      {item.review_status === 'approved' ? '审核通过' : item.review_status === 'rejected' ? '已驳回' : '待审核'}
                    </Tag>
                    <Tag color={item.publish_status === 'published' ? 'blue' : 'default'}>
                      {item.publish_status === 'published' ? '已发布' : '未发布'}
                    </Tag>
                    {item.review_status === 'approved' && <CheckCircleOutlined className="text-success" />}
                  </div>
                  {item.review_status === 'rejected' ? (
                    <div className="mt-2 flex flex-wrap items-center gap-3 rounded-control bg-surface-subtle px-2 py-1.5">
                      <span className="text-xs text-ink-secondary">
                        驳回原因：{item.review_note || '管理员未填写原因'}
                      </span>
                      <ResubmitButton versionId={item.id} onResubmit={onResubmit} />
                    </div>
                  ) : (
                    <div className="mt-2">
                      <Button
                        size="small" type="link" className="!px-0"
                        onClick={() => onNewVersion({
                          plugin_id: item.plugin_id ?? '',
                          name: item.name,
                          plugin_type: item.plugin_type,
                          scope: item.scope ?? 'personal',
                        })}
                      >
                        提交新版本
                      </Button>
                    </div>
                  )}
                </div>
              ))}
              {submissions.length === 0 && <Empty description="还没有提交记录，点击右上角上传" />}
            </div>
          ),
        },
      ]}
      tabBarExtraContent={
        <Space size={8}>
          <Button icon={<ApiOutlined />} onClick={onConnectMcp}>接入 MCP 服务</Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={onUpload}>上传插件</Button>
        </Space>
      }
    />
  );
}
