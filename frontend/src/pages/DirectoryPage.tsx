import { useCallback } from 'react';
import { message } from 'antd';
import { useNavigate } from 'react-router-dom';
import type { WorkplaceHome } from '@dwp/shared-schema';
import { api } from '../api/client';
import { useCurrentUser } from '../context/CurrentUserContext';
import { useAsyncData } from '../hooks/useAsyncData';
import { ErrorState, LoadingState } from '../components/PageState';
import Directory from '../features/agents/Directory';

export default function DirectoryPage() {
  const { actor } = useCurrentUser();
  const actorNo = actor.employee_no;
  const navigate = useNavigate();
  const fetcher = useCallback(() => api.getWorkplace(actorNo), [actorNo]);
  const { data: home, loading, error, reload } = useAsyncData<WorkplaceHome>(fetcher);

  const openChat = async (employeeNo: string) => {
    try {
      const existing = await api.listConversations(actorNo).then((list) =>
        list.find((c) => c.kind === 'direct' && c.participants.some((p) => p.employee_no === employeeNo)),
      );
      const conv = existing ?? await api.createConversation({
        actor_no: actorNo,
        kind: 'direct',
        participant_employee_nos: [employeeNo],
      });
      navigate(`/workplace?conversation=${encodeURIComponent(conv.id)}`);
    } catch (err) {
      message.error(`发起会话失败：${(err as Error).message}`);
    }
  };

  if (loading) return <LoadingState rows={8} />;
  if (error || !home) return <ErrorState onRetry={reload} />;

  return (
    <div className="directory-page flex h-full flex-col">
      <div className="directory-page-heading">
        <div>
          <div className="editorial-title">同事目录</div>
          <div className="mt-1 text-sm text-ink-secondary">按部门查找数字搭档，查看职责、插件与知识范围</div>
        </div>
      </div>
      <div className="min-h-0 flex-1">
        <Directory
          twin={home.twin}
          employees={home.available_employees}
          onOpenChat={(no) => void openChat(no)}
        />
      </div>
    </div>
  );
}
