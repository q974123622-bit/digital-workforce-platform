import { PlusOutlined } from '@ant-design/icons';
import { Button, Tag, Typography } from 'antd';
import type { MouseEvent } from 'react';
import type { McpCredentialAlert, PluginMarketState, UnifiedPlugin } from '../../api/client';
import { formatServerTime } from '../../utils/serverTime';
import PluginIcon from './PluginIcon';
import {
  MCP_CATEGORY_LABELS,
  PLUGIN_DESCRIPTION_CLAMP,
  PLUGIN_TYPE_LABEL,
  credentialAlertColor,
  credentialAlertLabel,
  marketStateMeta,
  pluginDescription,
  pluginScopeLabel,
  pluginSourceMeta,
} from './pluginMeta';

const { Text } = Typography;

export interface PluginVersionTarget {
  plugin_id: string;
  name: string;
  plugin_type: 'skill' | 'mcp';
  scope: string;
}

export function pluginIdOf(item: UnifiedPlugin): string {
  return item.plugin_id ?? String(item.id ?? '');
}

/** 分类标签：技能用业务分类，MCP 用 MCP 分类。 */
function CategoryTag({ item }: { item: UnifiedPlugin }) {
  if (item.plugin_type === 'skill') {
    return item.skill_category ? <Tag color="geekblue">{item.skill_category}</Tag> : null;
  }
  return item.mcp_category
    ? <Tag>{MCP_CATEGORY_LABELS[item.mcp_category] ?? item.mcp_category}</Tag>
    : null;
}

/** 六态操作区：每个状态给出唯一且明确的操作，禁用态用 title 说明原因。 */
function MarketAction({
  item, state, onEnable, onNewVersion, onRequest,
}: {
  item: UnifiedPlugin;
  state: PluginMarketState;
  onEnable: (pluginId: string, enabled: boolean) => void;
  onNewVersion: (target: PluginVersionTarget) => void;
  onRequest: (item: UnifiedPlugin) => void;
}) {
  const pluginId = pluginIdOf(item);
  const stop = (event: MouseEvent) => event.stopPropagation();
  const hasVersion = Boolean(item.version || item.current_version);

  switch (state) {
    case 'enabled':
      return <Button size="small" onClick={(event) => { stop(event); onEnable(pluginId, false); }}>停用</Button>;
    case 'granted':
      return (
        <Button
          type="primary" size="small" icon={<PlusOutlined />}
          onClick={(event) => { stop(event); onEnable(pluginId, true); }}
        >
          启用
        </Button>
      );
    case 'requestable':
      return (
        <Button
          type="primary" size="small" disabled={!hasVersion}
          title={hasVersion ? undefined : '管理员尚未发布可用版本'}
          onClick={(event) => { stop(event); onRequest(item); }}
        >
          申请使用
        </Button>
      );
    case 'pending':
      return <Button size="small" disabled title="申请已提交，等待管理员审批">申请中</Button>;
    case 'blocked':
      return <Button size="small" disabled title="管理员已禁止该插件，如需使用请联系管理员">不可申请</Button>;
    default:
      return (
        <Button
          type="link" size="small" className="!px-0"
          onClick={(event) => {
            stop(event);
            onNewVersion({
              plugin_id: pluginId, name: item.name, plugin_type: item.plugin_type, scope: item.scope ?? 'personal',
            });
          }}
        >
          提交新版本
        </Button>
      );
  }
}

export default function PluginMarketCard({
  item, credential, onEnable, onOpenDetail, onNewVersion, onRequest,
}: {
  item: UnifiedPlugin;
  /** 本人在这条 MCP 服务上的用户级令牌状态（即将过期 / 已过期）。 */
  credential?: McpCredentialAlert | null;
  onEnable: (pluginId: string, enabled: boolean) => void;
  onOpenDetail: (pluginId: string) => void;
  onNewVersion: (target: PluginVersionTarget) => void;
  onRequest: (item: UnifiedPlugin) => void;
}) {
  const pluginId = pluginIdOf(item);
  const state = item.state ?? 'requestable';
  const meta = marketStateMeta(state);
  const version = item.version ?? item.current_version;
  const source = pluginSourceMeta(item.source);
  const hint = state === 'pending' && item.request_id ? `申请单 ARQ-${item.request_id} · 等待管理员审批` : meta?.hint;
  const credentialLabel = credentialAlertLabel(credential?.state, credential?.days_left);

  return (
    <article
      role="button"
      tabIndex={0}
      aria-label={`查看插件 ${item.name}`}
      onClick={() => onOpenDetail(pluginId)}
      onKeyDown={(event) => {
        if (event.currentTarget === event.target && (event.key === 'Enter' || event.key === ' ')) onOpenDetail(pluginId);
      }}
      className={`plugin-card flex min-h-[176px] cursor-pointer flex-col rounded-container bg-surface p-4${item.featured ? ' plugin-card--featured' : ''}`}
    >
      <div className="flex items-start gap-3">
        <PluginIcon pluginType={item.plugin_type} />
        <div className="min-w-0 flex-1">
          <div className="flex items-start justify-between gap-2">
            <Text strong className="block truncate">{item.name}</Text>
            {meta && <Tag color={meta.color} className="!mr-0 shrink-0">{meta.label}</Tag>}
          </div>
          <div className="mt-1.5 flex flex-wrap gap-1">
            {item.featured && <Tag color="gold" className="!mr-0">精选</Tag>}
            <Tag>{PLUGIN_TYPE_LABEL[item.plugin_type] ?? item.plugin_type}</Tag>
            {version && <Tag color="green">v{version}</Tag>}
            <Tag color={item.scope === 'shared' ? 'blue' : 'default'}>{pluginScopeLabel(item.scope)}</Tag>
            <Tag color={source.color}>{source.label}</Tag>
            <CategoryTag item={item} />
            {item.data_level && <Tag>{item.data_level}</Tag>}
            {credentialLabel && (
              <Tag
                color={credentialAlertColor(credential?.state)}
                className="!mr-0"
                title={credential?.expires_at ? `到期时间：${formatServerTime(credential.expires_at)}` : undefined}
              >
                {credentialLabel}
              </Tag>
            )}
          </div>
          {/* 简介统一折叠三行：SkillHub 上最长接近 1000 字，全展开会把一屏撑满。 */}
          <p className={`mb-0 mt-3 text-xs leading-5 text-ink-secondary ${PLUGIN_DESCRIPTION_CLAMP}`}>
            {pluginDescription(item)}
          </p>
        </div>
      </div>
      <div className="mt-auto flex items-end justify-between gap-3 pt-3">
        <span className="text-xs leading-5 text-ink-tertiary">{hint}</span>
        <MarketAction
          item={item} state={state}
          onEnable={onEnable} onNewVersion={onNewVersion} onRequest={onRequest}
        />
      </div>
    </article>
  );
}
