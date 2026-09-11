import { Alert, Modal, Typography, message } from 'antd';
import { useCallback, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { api } from '../api/client';
import { useAsyncData } from '../hooks/useAsyncData';
import { ErrorState, LoadingState } from '../components/PageState';
import PluginCatalog from '../features/plugins/PluginCatalog';
import SubmissionWizard from '../features/plugins/SubmissionWizard';
import PluginDetailDrawer from '../features/plugins/PluginDetailDrawer';

export default function PluginCenter() {
  const fetcher = useCallback(async () => ({ catalog: await api.getMyPlugins(), submissions: await api.getMyPluginSubmissions() }), []);
  const { data, loading, error, reload } = useAsyncData(fetcher);
  const [open, setOpen] = useState(false);
  const [detailRevision, setDetailRevision] = useState(0);
  const [searchParams, setSearchParams] = useSearchParams();
  const selectedPluginId = searchParams.get('plugin');

  if (loading) return <LoadingState rows={6} />;
  if (error) return <ErrorState onRetry={reload} />;

  const enable = async (pluginId: string, enabled: boolean) => {
    try {
      await api.enableMyPlugin(pluginId, enabled);
      message.success(enabled ? '插件已启用' : '插件已停用');
      reload();
      setDetailRevision((value) => value + 1);
    } catch (err) {
      message.error(`操作失败：${(err as Error).message}`);
    }
  };

  return (
    <div className="page-shell space-y-5">
      <div className="page-heading">
        <div>
          <Typography.Title level={2} className="!mb-1 !text-[26px]">插件中心</Typography.Title>
          <Typography.Text type="secondary">为你的数字分身添加工作方法、知识和业务工具</Typography.Text>
        </div>
        <div className="page-heading-stats">
          <span><b>{data?.catalog.effective.length ?? 0}</b> 已启用</span>
          <span><b>{data?.catalog.available.length ?? 0}</b> 可添加</span>
          <span><b>{data?.submissions.length ?? 0}</b> 我的提交</span>
        </div>
      </div>
      <Alert
        showIcon
        type="info"
        message="插件发布受企业安全规则保护"
        description="个人 L1 工作方法通过安全检查后可直接使用；数据工具、共享插件及 L2/L3 插件需管理员审核发布。"
      />
      <PluginCatalog
        effective={data?.catalog.effective ?? []}
        available={data?.catalog.available ?? []}
        submissions={data?.submissions ?? []}
        onEnable={(pluginId, enabled) => void enable(pluginId, enabled)}
        onUpload={() => setOpen(true)}
        onOpenDetail={(pluginId) => {
          const next = new URLSearchParams(searchParams);
          next.set('plugin', pluginId);
          setSearchParams(next);
        }}
      />
      <PluginDetailDrawer
        pluginId={selectedPluginId}
        refreshToken={detailRevision}
        enabled={(data?.catalog.effective ?? []).some((item) => (item.plugin_id ?? String(item.id ?? '')) === selectedPluginId)}
        onClose={() => {
          const next = new URLSearchParams(searchParams);
          next.delete('plugin');
          setSearchParams(next, { replace: true });
        }}
        onToggle={(pluginId, nextEnabled) => void enable(pluginId, nextEnabled)}
      />
      <Modal title="上传插件" open={open} onCancel={() => setOpen(false)} footer={null} width={560} destroyOnClose>
        <SubmissionWizard onDone={() => { setOpen(false); reload(); }} />
      </Modal>
    </div>
  );
}
