import { CheckCircleOutlined, CloudServerOutlined, FileMarkdownOutlined, PlusOutlined } from '@ant-design/icons';
import { Button, Empty, Tabs, Tag, Typography } from 'antd';
import type { UnifiedPlugin, PluginSubmission } from '../../api/client';

const { Text } = Typography;

function PluginIcon({ pluginType }: { pluginType: string }) {
  return pluginType === 'skill' ? (
    <span className="grid h-9 w-9 place-items-center rounded-control bg-brand-soft text-brand"><FileMarkdownOutlined /></span>
  ) : (
    <span className="grid h-9 w-9 place-items-center rounded-control bg-success-soft text-success"><CloudServerOutlined /></span>
  );
}

const pluginDescription = (item: UnifiedPlugin) => {
  if (item.plugin_type === 'skill') return '将稳定的工作方法与表达习惯装载到数字分身中。';
  const labels: Record<string, string> = {
    knowledge: '连接已授权的企业知识资源，为回答提供可追溯依据。',
    cloud_information: '查询外部公开资讯，并通过平台网关统一审计。',
    fund: '提供基金资料与业务数据查询能力。',
    internal_system: '连接企业内部业务系统与受控工具。',
  };
  return labels[item.mcp_category ?? ''] ?? '通过受控网关连接数据或业务工具。';
};

export default function PluginCatalog({
  effective,
  available,
  submissions,
  onEnable,
  onUpload,
  onOpenDetail,
}: {
  effective: UnifiedPlugin[];
  available: UnifiedPlugin[];
  submissions: PluginSubmission[];
  onEnable: (pluginId: string, enabled: boolean) => void;
  onUpload: () => void;
  onOpenDetail: (pluginId: string) => void;
}) {
  return (
    <Tabs
      items={[
        {
          key: 'enabled',
          label: `已启用（${effective.length}）`,
          children: (
            <div className="plugin-catalog-grid grid gap-3 pb-6 md:grid-cols-2">
              {effective.map((item) => (
                <article
                  key={item.plugin_id}
                  role="button"
                  tabIndex={0}
                  aria-label={`查看插件 ${item.name}`}
                  onClick={() => onOpenDetail(item.plugin_id ?? String(item.id ?? ''))}
                  onKeyDown={(event) => { if (event.currentTarget === event.target && (event.key === 'Enter' || event.key === ' ')) onOpenDetail(item.plugin_id ?? String(item.id ?? '')); }}
                  className="plugin-card flex min-h-[168px] cursor-pointer flex-col rounded-container bg-surface p-4"
                >
                  <div className="flex items-start gap-3">
                    <PluginIcon pluginType={item.plugin_type} />
                    <div className="min-w-0 flex-1">
                      <Text strong className="block truncate">{item.name}</Text>
                      <div className="mt-1.5">
                        <Tag>{item.plugin_type === 'skill' ? 'Skill' : 'MCP'}</Tag>
                        <Tag color="green">v{item.version ?? item.current_version ?? '1.0.0'}</Tag>
                        {item.scope && <Tag color={item.scope === 'shared' ? 'blue' : 'default'}>{item.scope === 'shared' ? '共享' : '个人'}</Tag>}
                      </div>
                      <p className="mb-0 mt-3 text-xs leading-5 text-ink-secondary">{pluginDescription(item)}</p>
                    </div>
                  </div>
                  <Button size="small" className="mt-auto self-start" onClick={(event) => { event.stopPropagation(); onEnable(item.plugin_id ?? '', false); }}>
                    停用
                  </Button>
                </article>
              ))}
              {effective.length === 0 && <Empty description="暂未启用插件" className="md:col-span-2" />}
            </div>
          ),
        },
        {
          key: 'available',
          label: `可添加（${available.length}）`,
          children: (
            <div className="plugin-catalog-grid grid gap-3 pb-6 md:grid-cols-2">
              {available.map((item) => (
                <article
                  key={item.plugin_id ?? item.id}
                  role="button"
                  tabIndex={0}
                  aria-label={`查看插件 ${item.name}`}
                  onClick={() => onOpenDetail(item.plugin_id ?? String(item.id ?? ''))}
                  onKeyDown={(event) => { if (event.currentTarget === event.target && (event.key === 'Enter' || event.key === ' ')) onOpenDetail(item.plugin_id ?? String(item.id ?? '')); }}
                  className="plugin-card flex min-h-[168px] cursor-pointer flex-col rounded-container bg-surface p-4"
                >
                  <div className="flex items-start gap-3">
                    <PluginIcon pluginType={item.plugin_type} />
                    <div className="min-w-0 flex-1">
                      <Text strong className="block truncate">{item.name}</Text>
                      <div className="mt-1.5">
                        <Tag>{item.plugin_type === 'skill' ? 'Skill' : 'MCP'}</Tag>
                        <Tag color="green">v{item.version ?? item.current_version ?? '1.0.0'}</Tag>
                      </div>
                      <p className="mb-0 mt-3 text-xs leading-5 text-ink-secondary">{pluginDescription(item)}</p>
                    </div>
                  </div>
                  <Button type="primary" size="small" className="mt-auto self-start" icon={<PlusOutlined />} onClick={(event) => { event.stopPropagation(); onEnable(item.plugin_id ?? String(item.id ?? ''), true); }}>
                    启用
                  </Button>
                </article>
              ))}
              {available.length === 0 && <Empty description="暂无可添加插件" className="md:col-span-2" />}
            </div>
          ),
        },
        {
          key: 'submissions',
          label: `我的提交（${submissions.length}）`,
          children: (
            <div className="space-y-2 pb-6">
              {submissions.map((item) => (
                <div key={item.id} className="flex items-center gap-3 rounded-container border border-line bg-surface p-3">
                  <PluginIcon pluginType={item.plugin_type} />
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <Text strong>{item.name}</Text>
                      <Tag>v{item.version}</Tag>
                    </div>
                    <div className="text-xs text-ink-secondary">
                      {item.plugin_type === 'skill' ? 'Skill' : 'MCP'} · {item.data_level} · 提交于 {item.created_at}
                    </div>
                  </div>
                  <Tag color={item.review_status === 'approved' ? 'green' : item.review_status === 'rejected' ? 'red' : 'warning'}>
                    {item.review_status === 'approved' ? '审核通过' : item.review_status === 'rejected' ? '已驳回' : '待审核'}
                  </Tag>
                  <Tag color={item.publish_status === 'published' ? 'blue' : 'default'}>
                    {item.publish_status === 'published' ? '已发布' : '未发布'}
                  </Tag>
                  {item.review_note && <CheckCircleOutlined className="text-success" />}
                </div>
              ))}
              {submissions.length === 0 && <Empty description="还没有提交记录，点击右上角上传" />}
            </div>
          ),
        },
      ]}
      tabBarExtraContent={
        <Button type="primary" icon={<PlusOutlined />} onClick={onUpload}>上传插件</Button>
      }
    />
  );
}
