import { useCallback } from 'react';
import { LinkOutlined } from '@ant-design/icons';
import { Card, Col, Row, Space, Tag, Typography } from 'antd';
import type { Capability } from '@dwp/shared-schema';
import { api } from '../api/client';
import { PluginTypeTag, PLUGIN_TYPE_META, StatusBadge } from '../components/tags';
import { useCurrentUser } from '../context/CurrentUserContext';
import { EmptyState, ErrorState, LoadingState } from '../components/PageState';
import { useAsyncData } from '../hooks/useAsyncData';

export default function Plugins() {
  const { actor } = useCurrentUser();
  const fetcher = useCallback(() => api.listCapabilities(actor.employee_no), [actor.employee_no]);
  const { data: capabilities, loading, error, reload } = useAsyncData<Capability[]>(fetcher);

  if (loading) return <LoadingState rows={6} />;
  if (error) return <ErrorState onRetry={reload} />;

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, marginBottom: 16, flexWrap: 'wrap' }}>
        <div>
          <Typography.Title level={3} style={{ margin: 0 }}>
            能力中心
          </Typography.Title>
          <Typography.Text type="secondary">Skill 与 Plugin 使用统一能力契约；可执行能力统一经过 Policy / Gateway</Typography.Text>
        </div>
        <Tag color="blue" style={{ borderRadius: 12, padding: '2px 12px' }}>
          共 {(capabilities ?? []).length} 项能力
        </Tag>
      </div>

      {capabilities && capabilities.length > 0 ? (
        <Row gutter={[16, 16]}>
          {capabilities.map((capability) => {
            const meta = PLUGIN_TYPE_META[capability.kind];
            return (
              <Col xs={24} md={12} xl={8} key={`${capability.source_type}-${capability.id}`}>
                <Card className="hover-card" styles={{ body: { padding: 20 } }}>
                  <Space size={12} align="start">
                    <div
                      style={{
                        width: 42,
                        height: 42,
                        borderRadius: 12,
                        background: meta?.bg ?? '#f0f3f7',
                        color: meta?.hex ?? '#5c6b83',
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        fontSize: 20,
                        flexShrink: 0,
                      }}
                    >
                      {meta?.icon ?? <LinkOutlined />}
                    </div>
                    <div style={{ minWidth: 0, flex: 1 }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'center' }}>
                        <Typography.Text strong style={{ fontSize: 15 }}>
                          {capability.name}
                        </Typography.Text>
                        <StatusBadge value={capability.status} />
                      </div>
                      <div style={{ margin: '6px 0' }}>
                        <PluginTypeTag value={capability.kind} />
                        <Tag color={capability.executable ? 'geekblue' : 'default'}>
                          {capability.executable ? '可执行' : '指令型'}
                        </Tag>
                        <Tag color={capability.ready ? 'green' : 'red'}>{capability.ready ? '契约就绪' : '契约异常'}</Tag>
                      </div>
                      <Typography.Paragraph type="secondary" style={{ fontSize: 13, marginBottom: 8 }} ellipsis={{ rows: 2 }}>
                        {capability.description}
                      </Typography.Paragraph>
                      <div className="mono" style={{ fontSize: 12, color: '#66748c' }}>
                        v{capability.contract_version} · {String(capability.executor.primary ?? 'unknown')}
                      </div>
                      {capability.issues.length > 0 && (
                        <Typography.Text type="danger" style={{ fontSize: 12 }}>
                          {capability.issues.join('；')}
                        </Typography.Text>
                      )}
                    </div>
                  </Space>
                </Card>
              </Col>
            );
          })}
        </Row>
      ) : (
        <EmptyState description="暂无插件" />
      )}
    </div>
  );
}
