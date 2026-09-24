import { CloudServerOutlined, FileMarkdownOutlined } from '@ant-design/icons';

/** 插件图标块：Skill 走品牌色，MCP 走成功色，供市场卡片与提交列表共用。 */
export default function PluginIcon({ pluginType }: { pluginType: string }) {
  return pluginType === 'skill' ? (
    <span className="grid h-9 w-9 shrink-0 place-items-center rounded-control bg-brand-soft text-brand"><FileMarkdownOutlined /></span>
  ) : (
    <span className="grid h-9 w-9 shrink-0 place-items-center rounded-control bg-success-soft text-success"><CloudServerOutlined /></span>
  );
}
