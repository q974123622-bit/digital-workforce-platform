import type { PluginMarketState, UnifiedPlugin } from '../../api/client';

/** MCP 分类的唯一中文口径（插件市场筛选、详情抽屉、卡片标签共用）。 */
export const MCP_CATEGORY_LABELS: Record<string, string> = {
  knowledge: '知识库',
  cloud_information: '云端资讯',
  fund: '基金',
  internal_system: '企业内部系统',
  employee_service: '员工服务',
  other: '其他',
};

export const MCP_CATEGORY_OPTIONS = Object.entries(MCP_CATEGORY_LABELS).map(([value, label]) => ({ value, label }));

export const PLUGIN_TYPE_LABEL: Record<string, string> = { skill: 'Skill', mcp: 'MCP' };

/** 插件能力说明：优先用后端描述，缺失时按类型/分类给出一句话兜底。 */
export function pluginDescription(item: UnifiedPlugin): string {
  if (item.description) return item.description;
  if (item.plugin_type === 'skill') return '将稳定的工作方法与表达习惯装载到分身中。';
  const labels: Record<string, string> = {
    knowledge: '连接已授权的企业知识资源，为回答提供可追溯依据。',
    cloud_information: '查询外部公开资讯，并通过平台网关统一审计。',
    fund: '提供基金资料与业务数据查询能力。',
    internal_system: '连接企业内部业务系统与受控工具。',
    employee_service: '查询员工自助服务数据，如考勤、会议室、餐卡与内部送审。',
  };
  const category = item.mcp_category ?? item.category ?? '';
  return labels[category] ?? '通过受控网关连接数据或业务工具。';
}

export function pluginScopeLabel(scope?: string): string {
  return scope === 'shared' ? '共享' : '个人';
}

/** 来源徽标：让「这个技能从哪来」一眼可读（SkillHub 导入 / 平台共享 / 本人）。 */
export const PLUGIN_SOURCE_META: Record<string, { label: string; color: string }> = {
  skillhub: { label: '来源 SkillHub', color: 'purple' },
  platform: { label: '平台共享', color: 'default' },
  personal: { label: '本人', color: 'cyan' },
};

export function pluginSourceMeta(source?: string) {
  return PLUGIN_SOURCE_META[source ?? 'platform'] ?? PLUGIN_SOURCE_META.platform;
}

/** 员工侧「来源」筛选选项（分级管理的维度）。 */
export const PLUGIN_SOURCE_OPTIONS = [
  { value: 'skillhub', label: 'SkillHub 导入' },
  { value: 'platform', label: '平台共享' },
  { value: 'personal', label: '本人上传' },
];

/** 数据等级筛选选项；等级越高，申请审批越严。 */
export const DATA_LEVEL_OPTIONS = [
  { value: 'L1', label: 'L1 公开' },
  { value: 'L2', label: 'L2 内部' },
  { value: 'L3', label: 'L3 敏感' },
];

/** 技能业务分类词表（与后端 `skillhub_client.DEFAULT_SKILL_CATEGORIES` 一致）。 */
export const SKILL_CATEGORY_OPTIONS = [
  'AI大赛-财富管理赛道',
  'AI大赛-机构服务赛道',
  'AI大赛-办公助手赛道',
  'AI大赛-内控管理赛道',
  'AI大赛-运营管理赛道',
  'AI大赛-AI研发赛道',
  '产品经理',
  '沙箱执行',
  '测试工具',
  '数据治理',
  '知识管理',
  '生活服务',
  'AI智能',
  '开发编程',
  '办公效率',
  '数据分析',
  '内容创作',
  '安全合规',
  '通讯协作',
].map((value) => ({ value, label: value }));

/** 卡片简介统一折叠为三行；完整内容在详情抽屉里看。 */
export const PLUGIN_DESCRIPTION_CLAMP = 'line-clamp-3';

export function pluginFeaturedLabel(featured?: boolean): string {
  return featured ? '精选' : '';
}

export interface MarketStateMeta {
  /** 状态徽标文案（同时是操作按钮的语义来源，不单靠颜色传达状态）。 */
  label: string;
  /** antd Tag 语义色。 */
  color: 'success' | 'processing' | 'warning' | 'error' | 'default';
  /** 卡片底部的一行说明，解释「现在能做什么」。 */
  hint: string;
}

export const MARKET_STATE_META: Record<PluginMarketState, MarketStateMeta> = {
  own_draft: { label: '未发布', color: 'default', hint: '提交新版本并通过审核后即可装载到你的分身。' },
  enabled: { label: '已启用', color: 'success', hint: '已装载到你的分身，可随时停用。' },
  granted: { label: '待启用', color: 'processing', hint: '管理员已授权，启用后立即装载到你的分身。' },
  blocked: { label: '已禁止', color: 'error', hint: '管理员已禁止该插件，如需使用请联系管理员。' },
  pending: { label: '申请中', color: 'warning', hint: '申请已提交，等待管理员审批。' },
  requestable: { label: '可申请', color: 'default', hint: '填写使用理由提交申请，审批通过后即可启用。' },
};

export function marketStateMeta(state?: PluginMarketState): MarketStateMeta | null {
  return state ? MARKET_STATE_META[state] : null;
}

/**
 * 技能投放态的唯一文案口径（运行能力核验页与AI员工档案页共用）。
 *
 * 真源是 harness 工作区文件系统：后端 `runtime_manager.deploy_state()` 比对
 * 授权版本的 sha256 与目标目录标记后给出，页面只负责呈现。
 */
export const DEPLOY_STATE_META: Record<string, { label: string; color: 'success' | 'processing' | 'warning' | 'error' | 'default' }> = {
  deployed: { label: '已投放', color: 'success' },
  stale: { label: '待重新投放', color: 'warning' },
  missing: { label: '待投放', color: 'default' },
  failed: { label: '投放失败', color: 'error' },
  removed: { label: '已回收', color: 'default' },
};

export function deployStateMeta(state?: string | null) {
  return state ? DEPLOY_STATE_META[state] ?? null : null;
}

/**
 * 用户级 MCP 令牌到期文案的唯一口径（页头告警与货架行内标签共用）。
 *
 * 后端只在「即将过期 / 已过期」时才返回告警项，`ok` / `unknown` 不提醒。
 */
export function credentialAlertLabel(state?: string | null, daysLeft?: number | null): string {
  if (state === 'expired') return '令牌已过期';
  if (state === 'expiring') return `令牌 ${daysLeft ?? 0} 天后过期`;
  return '';
}

export function credentialAlertColor(state?: string | null): 'error' | 'warning' {
  return state === 'expired' ? 'error' : 'warning';
}
