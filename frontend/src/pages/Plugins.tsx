import { useCallback } from 'react';
import { LinkOutlined } from '@ant-design/icons';
import { Card, Col, Row, Space, Tag, Typography } from 'antd';
import type { Plugin } from '@dwp/shared-schema';
import { api } from '../api/client';
import { LevelTag, PluginTypeTag, PLUGIN_TYPE_META, StatusBadge } from '../components/tags';
import { EmptyState, ErrorState, LoadingState } from '../components/PageState';
import { useAsyncData } from '../hooks/useAsyncData';

export default function Plugins() {
  const fetcher = useCallback(() => api.listPlugins(), []);
  const { data: plugins, loading, error, reload } = useAsyncData<Plugin[]>(fetcher);

  if (loading) return <LoadingState rows={6} />;
  if (error) return <ErrorState onRetry={reload} />;

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, marginBottom: 16, flexWrap: 'wrap' }}>
        <div>
          <Typography.Title level={3} style={{ margin: 0 }}>
            插件中心
          </Typography.Title>
          <Typography.Text type="secondary">插件统一登记，权限与调用经 Plugin Gateway 生效</Typography.Text>
        </div>
        <Tag color="blue" style={{ borderRadius: 12, padding: '2px 12px' }}>
          共 {(plugins ?? []).length} 个插件
        </Tag>
      </div>

      {plugins && plugins.length > 0 ? (
        <Row gutter={[16, 16]}>
          {plugins.map((plugin) => {
            const meta = PLUGIN_TYPE_META[plugin.type];
            return (
              <Col xs={24} md={12} xl={8} key={plugin.id}>
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
                          {plugin.name}
                        </Typography.Text>
                        <StatusBadge value={plugin.status} />
                      </div>
                      <div style={{ margin: '6px 0' }}>
                        <PluginTypeTag value={plugin.type} /> <LevelTag value={plugin.data_level} />
                      </div>
                      <Typography.Paragraph type="secondary" style={{ fontSize: 13, marginBottom: 8 }} ellipsis={{ rows: 2 }}>
                        {plugin.description}
                      </Typography.Paragraph>
                      <div className="mono" style={{ fontSize: 12, color: '#66748c' }}>
                        {plugin.endpoint_ref}
                      </div>
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
