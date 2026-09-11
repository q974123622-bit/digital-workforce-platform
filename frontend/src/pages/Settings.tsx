import { Alert, Card, Descriptions, Tag, Typography } from 'antd';
import { useCallback } from 'react';
import { api } from '../api/client';
import { useAsyncData } from '../hooks/useAsyncData';
import { ErrorState, LoadingState } from '../components/PageState';

const { Title, Text } = Typography;

export default function Settings() {
  const fetcher = useCallback(() => api.memoryHealth(), []);
  const { data, loading, error, reload } = useAsyncData(fetcher);

  return (
    <div className="space-y-4">
      <div>
        <Title level={2} className="!mb-1 !text-[26px]">系统设置</Title>
        <Text type="secondary">查看平台运行配置与服务健康状态</Text>
      </div>
      <Alert showIcon type="info" message="部署参数由环境配置维护" description="模型网关、内部知识引擎、MCP Endpoint 与 Secret 暂无在线修改接口，请在堡垒机的本地配置或公司 Secret 系统中维护。" />
      {loading ? <LoadingState rows={4} /> : error ? <ErrorState onRetry={reload} /> : (
        <Card title="记忆服务健康">
          <Descriptions column={2} bordered size="small">
            <Descriptions.Item label="模式">{data?.mode ?? '暂未提供'}</Descriptions.Item>
            <Descriptions.Item label="状态">
              {data ? <Tag color={data.status === 'healthy' ? 'green' : 'red'}>{data.status === 'healthy' ? '正常' : '异常'}</Tag> : '暂未提供'}
            </Descriptions.Item>
            <Descriptions.Item label="记忆总数">{data?.memories ?? '暂未提供'}</Descriptions.Item>
            <Descriptions.Item label="待处理任务">{data?.pending_jobs ?? '暂未提供'}</Descriptions.Item>
            <Descriptions.Item label="失败任务" span={2}>
              {data?.failed_jobs ?? '暂未提供'}
            </Descriptions.Item>
          </Descriptions>
        </Card>
      )}
    </div>
  );
}
