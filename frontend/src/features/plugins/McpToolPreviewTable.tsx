import { ApiOutlined, CheckCircleOutlined, ClockCircleOutlined, ThunderboltOutlined } from '@ant-design/icons';
import { Alert, Card, Space, Table, Tag, Typography } from 'antd';
import type { McpProbeResult, McpToolSummary } from '../../api/client';

const { Text } = Typography;

function StatChip({ icon, label, value }: { icon: React.ReactNode; label: string; value: string }) {
  return (
    <div className="flex items-center gap-2 rounded-control bg-surface-subtle px-3 py-2">
      <span className="text-brand">{icon}</span>
      <div className="min-w-0">
        <div className="text-xs text-ink-tertiary">{label}</div>
        <div className="truncate text-sm text-ink">{value}</div>
      </div>
    </div>
  );
}

/**
 * 步骤二：展示解析结果。
 *
 * 左侧服务信息（服务名、协议版本、能力声明、连通耗时），右侧工具清单表格；
 * 工具数量与描述长度均由后端截断，界面只如实呈现。
 */
export default function McpToolPreviewTable({ probe }: { probe: McpProbeResult }) {
  const serverName = String(
    probe.server_info?.title || probe.server_info?.name || '未声明服务名',
  );
  const serverVersion = String(probe.server_info?.version || '—');
  const capabilities = Object.keys(probe.capabilities ?? {});

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <StatChip icon={<ApiOutlined />} label="服务名称" value={`${serverName} · v${serverVersion}`} />
        <StatChip icon={<CheckCircleOutlined />} label="协议版本" value={probe.protocol_version || '—'} />
        <StatChip
          icon={<ThunderboltOutlined />}
          label="工具数量"
          value={probe.tools_truncated ? `${probe.tools_count}（仅展示前 ${probe.tools.length} 个）` : String(probe.tools_count)}
        />
        <StatChip icon={<ClockCircleOutlined />} label="连通耗时" value={`${probe.latency_ms} ms`} />
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <Text type="secondary" className="!text-xs">能力声明：</Text>
        {capabilities.length
          ? capabilities.map((item) => <Tag key={item}>{item}</Tag>)
          : <Text type="secondary" className="!text-xs">未声明</Text>}
        <Tag color={probe.authenticated ? 'green' : 'default'}>
          {probe.authenticated ? `已携带鉴权（${probe.auth_header_name}）` : '匿名接入'}
        </Tag>
      </div>

      {probe.tools_truncated && (
        <Alert
          type="info"
          showIcon
          message={`该服务共 ${probe.tools_count} 个工具，界面只展示前 ${probe.tools.length} 个`}
          description="登记后运行时仍会下发全部工具，这里的截断只影响预览。"
        />
      )}

      <Card size="small" title={`工具清单（${probe.tools.length}）`} className="border-line">
        <Table<McpToolSummary>
          rowKey="name"
          size="small"
          dataSource={probe.tools}
          pagination={probe.tools.length > 8 ? { pageSize: 8, size: 'small' } : false}
          scroll={{ y: 260 }}
          columns={[
            {
              title: '工具名',
              dataIndex: 'name',
              width: 200,
              render: (value: string) => <Text strong className="!text-xs">{value}</Text>,
            },
            {
              title: '原始说明',
              dataIndex: 'description',
              render: (value: string) => (
                <span className="text-xs leading-5 text-ink-secondary">{value || '服务未提供说明'}</span>
              ),
            },
            {
              title: '入参摘要',
              dataIndex: 'parameters',
              width: 280,
              render: (_: unknown, row: McpToolSummary) => (
                <Space size={[4, 4]} wrap>
                  {row.parameters.length ? row.parameters.map((param) => (
                    <Tag key={param.name} color={row.required.includes(param.name) ? 'blue' : 'default'}>
                      {param.name}
                      <span className="ml-1 text-[10px] opacity-70">{param.type}</span>
                    </Tag>
                  )) : <Text type="secondary" className="!text-xs">无入参</Text>}
                  {row.parameters_truncated && <Tag>已截断</Tag>}
                </Space>
              ),
            },
          ]}
        />
      </Card>
    </div>
  );
}
