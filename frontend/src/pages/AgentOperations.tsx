import { ReloadOutlined } from '@ant-design/icons';
import type { AgentProfile } from '@dwp/shared-schema';
import { Button, Card, Empty, Space, Table, Tag, Typography, message } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { useCallback, useState } from 'react';
import { api } from '../api/client';
import { useAsyncData } from '../hooks/useAsyncData';
import { ErrorState, LoadingState } from '../components/PageState';

const { Paragraph, Text, Title } = Typography;

export default function AgentOperations() {
  // 列表与「运行环境」汇总并发拉齐，共用同一份 loading / error 态。
  const fetcher = useCallback(async () => {
    const [rows, info] = await Promise.all([api.listAgents(), api.getRuntimeInfo()]);
    return { rows, info };
  }, []);
  const { data, loading, error, reload } = useAsyncData(fetcher);
  const [acting, setActing] = useState('');

  const changeRuntime = async (row: AgentProfile, start: boolean) => {
    setActing(row.employee_id);
    try {
      if (start) await api.startAgentRuntime(row.employee_id);
      else await api.stopAgentRuntime(row.employee_id);
      message.success(start ? 'Harness 运行环境已就绪' : 'Harness 运行环境已停止');
      reload();
    } catch (cause) {
      message.error((cause as Error).message);
    } finally {
      setActing('');
    }
  };

  const columns: ColumnsType<AgentProfile> = [
    {
      title: 'AI员工 / 分身', key: 'identity', width: 220,
      render: (_, row) => <div><Text strong>{row.display_name}</Text><div className="text-xs text-ink-tertiary">{row.employee_id} · {row.department}</div></div>,
    },
    {
      title: '身份', dataIndex: 'identity_kind', width: 120,
      render: (value) => <Tag color={value === 'human_twin' ? 'blue' : 'default'}>{value === 'human_twin' ? '真人分身' : '岗位同事'}</Tag>,
    },
    {
      title: '职责与知识范围', key: 'scope',
      render: (_, row) => <div><Paragraph className="!mb-1 !text-sm" ellipsis={{ rows: 2 }}>{row.responsibilities.join('；') || '待配置职责'}</Paragraph><Space size={[4, 4]} wrap>{row.knowledge_domains.map((domain) => <Tag key={domain}>{domain}</Tag>)}</Space></div>,
    },
    {
      title: 'Harness', key: 'runtime', width: 250,
      render: (_, row) => (
        <div>
          <Tag color={row.runtime_state === 'ready' ? 'green' : row.runtime_state === 'failed' ? 'red' : 'default'}>{row.runtime_state}</Tag>
          <div className="mt-1 font-mono text-xs text-ink-secondary">{row.container_name}</div>
          {/* 底座镜像 + dsh 框架版本：取自容器标签，灰度切换后逐行不同；查不到时回落部署声明值。 */}
          <div className="mt-0.5 font-mono text-[11px] text-ink-tertiary">
            {row.runtime_image}
            {row.runtime_version ? ` · dsh ${row.runtime_version}` : ''}
          </div>
        </div>
      ),
    },
    {
      title: '人设', key: 'persona', width: 110,
      render: (_, row) => <div>v{row.persona_version}<div className="text-xs text-ink-tertiary">{row.persona_status}</div></div>,
    },
    {
      title: '操作', key: 'action', width: 150,
      render: (_, row) => <Space><Button size="small" type="primary" loading={acting === row.employee_id} disabled={row.runtime_state === 'ready'} onClick={() => changeRuntime(row, true)}>启动</Button><Button size="small" loading={acting === row.employee_id} disabled={row.runtime_state === 'stopped'} onClick={() => changeRuntime(row, false)}>停止</Button></Space>,
    },
  ];

  if (loading) return <LoadingState rows={8} />;
  if (error) return <ErrorState onRetry={reload} />;
  const rows = data?.rows ?? [];
  const info = data?.info;
  return (
    <div>
      <div className="mb-4 flex items-start justify-between">
        <div>
          <Title level={2} className="!mb-1 !text-[26px]">AI员工</Title>
          <Text type="secondary">统一查看我的分身、岗位员工及其独立运行环境</Text>
          {/* 运行环境汇总：这一整套平台由哪几个镜像、哪个交付版本组成（含 Harness 底座与 dsh 版本）。 */}
          {info && (
            <div className="mt-3 flex flex-wrap items-center gap-2">
              {info.delivery_tag && (
                <span className="inline-flex items-center gap-1.5 rounded-full border border-line-light bg-brand-soft px-2.5 py-1 text-[11px] font-medium text-brand">
                  交付版本
                  <span className="font-mono">{info.delivery_tag}</span>
                </span>
              )}
              {info.images.map((image) => (
                <span
                  key={image.role}
                  className="inline-flex items-center gap-1.5 rounded-full border border-line-light bg-surface px-2.5 py-1 text-[11px] text-ink-tertiary transition-colors hover:border-line hover:text-ink-secondary"
                >
                  {image.role}
                  <span className="font-mono text-ink-secondary">{image.name}</span>
                  {image.note && <span className="font-mono">· {image.note}</span>}
                </span>
              ))}
            </div>
          )}
        </div>
        <Button icon={<ReloadOutlined />} onClick={reload}>刷新</Button>
      </div>
      <Card className="border-line" styles={{ body: { padding: 0 } }}>
        {rows.length ? <Table rowKey="employee_id" columns={columns} dataSource={rows} pagination={false} scroll={{ x: 1040 }} /> : <Empty className="py-12" />}
      </Card>
    </div>
  );
}
