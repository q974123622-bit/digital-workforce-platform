import { ReloadOutlined } from '@ant-design/icons';
import { Button, Card, Empty, Result, Skeleton } from 'antd';

export function LoadingState({ rows = 6 }: { rows?: number }) {
  return <Skeleton active paragraph={{ rows }} />;
}

export function EmptyState({ description = '暂无数据' }: { description?: string }) {
  return (
    <Card>
      <Empty description={description} />
    </Card>
  );
}

export function ErrorState({
  onRetry,
  description = '请确认后端服务已启动（端口 8000）后重试。',
}: {
  onRetry: () => void;
  description?: string;
}) {
  return (
    <Card>
      <Result
        status="error"
        title="数据加载失败"
        subTitle={description}
        extra={
          <Button type="primary" icon={<ReloadOutlined />} onClick={onRetry}>
            重新加载
          </Button>
        }
      />
    </Card>
  );
}
