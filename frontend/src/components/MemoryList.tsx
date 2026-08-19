import { useCallback } from 'react';
import { Empty, List, Typography } from 'antd';
import { DeleteOutlined, HistoryOutlined } from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import { api } from '../api/client';
import type { SessionSummary } from '../api/client';
import { useAsyncData } from '../hooks/useAsyncData';

/** 展示某个数字员工的会话记录（对话历史），点击可跳转到对应会话继续聊 */
export default function MemoryList({ employeeNo }: { employeeNo: string }) {
  const navigate = useNavigate();
  const fetcher = useCallback(() => api.listSessions(employeeNo), [employeeNo]);
  const { data: sessions, loading, reload } = useAsyncData<SessionSummary[]>(fetcher);

  const removeSession = async (sid: string) => {
    try {
      await api.deleteSession(sid);
      reload();
    } catch {
      // 删除失败静默
    }
  };

  if (!loading && (!sessions || sessions.length === 0)) {
    return <Empty description="暂无会话记录" />;
  }

  return (
    <div>
      <Typography.Paragraph type="secondary" style={{ fontSize: 13 }}>
        与该员工的历史会话（点击进入继续对话，右侧可删除）
      </Typography.Paragraph>
      <List
        size="small"
        loading={loading}
        dataSource={sessions ?? []}
        renderItem={(s) => (
          <List.Item
            style={{ cursor: 'pointer' }}
            onClick={() => navigate(`/employees/${employeeNo}/chat?session=${s.session_id}`)}
            actions={[
              <DeleteOutlined
                key="del"
                onClick={(e) => {
                  e.stopPropagation();
                  void removeSession(s.session_id);
                }}
                style={{ color: '#999' }}
              />,
            ]}
          >
            <List.Item.Meta
              avatar={<HistoryOutlined style={{ fontSize: 18, color: '#1677ff' }} />}
              title={<Typography.Text>{s.title || '（新会话）'}</Typography.Text>}
              description={new Date(s.created_at).toLocaleString()}
            />
          </List.Item>
        )}
      />
    </div>
  );
}
