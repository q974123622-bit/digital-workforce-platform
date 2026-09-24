import { Alert, Input, Modal, Tag, Typography, message } from 'antd';
import { useCallback, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { api } from '../api/client';
import type { UnifiedPlugin } from '../api/client';
import { useAsyncData } from '../hooks/useAsyncData';
import { ErrorState, LoadingState } from '../components/PageState';
import McpOnboardingWizard from '../features/plugins/McpOnboardingWizard';
import PluginCatalog from '../features/plugins/PluginCatalog';
import SubmissionWizard from '../features/plugins/SubmissionWizard';
import type { PluginTarget } from '../features/plugins/SubmissionWizard';
import PluginDetailDrawer from '../features/plugins/PluginDetailDrawer';
import { PLUGIN_TYPE_LABEL, pluginDescription, pluginScopeLabel } from '../features/plugins/pluginMeta';
import { formatServerTime } from '../utils/serverTime';

/** 申请弹窗的目标：插件 id 必填（列表项来自市场或详情抽屉）。 */
type RequestTarget = UnifiedPlugin & { plugin_id: string };

const pluginIdOf = (item: UnifiedPlugin) => item.plugin_id ?? String(item.id ?? '');

export default function PluginCenter() {
  const fetcher = useCallback(async () => {
    const [catalog, submissions, knowledge, credentials] = await Promise.all([
      api.getMyPlugins(),
      api.getMyPluginSubmissions(),
      api.listMyKnowledgeBases(),
      api.listMyMcpCredentialAlerts(),
    ]);
    return { catalog, submissions, knowledge: knowledge.items, credentials };
  }, []);
  const { data, loading, error, reload } = useAsyncData(fetcher);
  const [open, setOpen] = useState(false);
  // 为已有插件提交新版本：复用同一个上传向导，带上 plugin_id。
  const [newVersionTarget, setNewVersionTarget] = useState<PluginTarget | null>(null);
  const [requestTarget, setRequestTarget] = useState<RequestTarget | null>(null);
  const [requestReason, setRequestReason] = useState('');
  const [requestSubmitting, setRequestSubmitting] = useState(false);
  const [detailRevision, setDetailRevision] = useState(0);
  // 自助接入 MCP：向导需要知道凭据加密密钥是否已配置（未配置时只能接无鉴权服务）。
  const [mcpOpen, setMcpOpen] = useState(false);
  const [mcpEncryptionAvailable, setMcpEncryptionAvailable] = useState(true);
  const [searchParams, setSearchParams] = useSearchParams();
  const selectedPluginId = searchParams.get('plugin');

  if (loading) return <LoadingState rows={6} />;
  if (error) return <ErrorState onRetry={reload} />;

  const openMcpWizard = () => {
    setMcpOpen(true);
    void api.listMyMcpServices()
      .then((result) => setMcpEncryptionAvailable(result.credential_encryption_available))
      .catch((err) => { console.error('读取 MCP 接入配置失败', err); });
  };

  const market = data?.catalog.market ?? [];
  const mcpCount = market.filter((item) => item.plugin_type === 'mcp').length;
  const skillCount = market.filter((item) => item.plugin_type === 'skill').length;
  // 用户级令牌（按人签发、会过期）到期提醒：页头汇总 + 货架行内标签。
  const credentialAlerts = data?.credentials.alerts ?? [];
  const credentialMap = Object.fromEntries(credentialAlerts.map((item) => [item.plugin_id, item]));

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

  const resubmit = async (versionId: number, file: File) => {
    const form = new FormData();
    form.append('file', file);
    try {
      await api.resubmitPlugin(versionId, form);
      message.success('已重新提交，等待管理员审核');
      reload();
    } catch (err) {
      message.error(`重新提交失败：${(err as Error).message}`);
    }
  };

  const openRequest = (pluginId: string) => {
    const item = market.find((row) => pluginIdOf(row) === pluginId);
    setRequestTarget(item ? { ...item, plugin_id: pluginId } : { plugin_id: pluginId, name: pluginId, plugin_type: 'mcp' });
  };

  const closeRequest = () => {
    setRequestTarget(null);
    setRequestReason('');
  };

  const submitRequest = async () => {
    if (!requestTarget) return;
    setRequestSubmitting(true);
    try {
      await api.requestMyPlugin(requestTarget.plugin_id, requestReason.trim());
      message.success('申请已提交，管理员审批通过后即可启用');
      closeRequest();
      reload();
      setDetailRevision((value) => value + 1);
    } catch (err) {
      message.error(`提交申请失败：${(err as Error).message}`);
    } finally {
      setRequestSubmitting(false);
    }
  };

  return (
    <div className="plugin-page page-shell min-h-full space-y-5 pb-24 md:pb-10">
      <div className="page-heading">
        <div>
          <Typography.Title level={2} className="!mb-1 !text-[26px]">插件中心</Typography.Title>
          <Typography.Text type="secondary">三个市场分别装载工具、技能与知识，为你的分身补齐工作能力</Typography.Text>
        </div>
        <div className="page-heading-stats">
          <span><b>{mcpCount}</b> MCP 工具</span>
          <span><b>{skillCount}</b> 技能</span>
          <span><b>{data?.knowledge.length ?? 0}</b> 知识库</span>
          <span><b>{data?.catalog.effective.length ?? 0}</b> 已启用</span>
        </div>
      </div>
      <Alert
        showIcon
        type="info"
        message="市场资源全可见，使用需申请审批"
        description="MCP 工具、技能与知识库都对所有人可见；个人 L1 工作方法通过安全检查后可直接使用，共享资源与 L2/L3 资源需提交申请、管理员审批通过后才生效。知识库为独占资源，被其他 AI 员工占用时会明确标注。"
      />
      {credentialAlerts.length > 0 && (
        <Alert
          showIcon
          type="warning"
          message={`${credentialAlerts.length} 个 MCP 服务的访问令牌需要更新`}
          description={(
            <ul className="m-0 pl-4">
              {credentialAlerts.map((item) => (
                <li key={item.plugin_id}>
                  {item.name}：
                  {item.state === 'expired' ? '已过期' : `${item.days_left} 天后过期`}
                  {item.expires_at ? `（到期时间 ${formatServerTime(item.expires_at)}）` : ''}
                  ，请在服务里重新提供你的访问令牌
                </li>
              ))}
            </ul>
          )}
        />
      )}
      <PluginCatalog
        market={market}
        effective={data?.catalog.effective ?? []}
        submissions={data?.submissions ?? []}
        knowledgeBases={data?.knowledge ?? []}
        credentialAlerts={credentialMap}
        onEnable={(pluginId, enabled) => void enable(pluginId, enabled)}
        onUpload={() => setOpen(true)}
        onResubmit={(versionId, file) => void resubmit(versionId, file)}
        onNewVersion={setNewVersionTarget}
        onRequest={(item) => setRequestTarget({ ...item, plugin_id: pluginIdOf(item) })}
        onKnowledgeChanged={reload}
        onConnectMcp={openMcpWizard}
        onOpenDetail={(pluginId) => {
          const next = new URLSearchParams(searchParams);
          next.set('plugin', pluginId);
          setSearchParams(next);
        }}
      />
      <PluginDetailDrawer
        pluginId={selectedPluginId}
        refreshToken={detailRevision}
        enabled={market.some((item) => pluginIdOf(item) === selectedPluginId && item.state === 'enabled')}
        marketState={market.find((item) => pluginIdOf(item) === selectedPluginId)?.state}
        onClose={() => {
          const next = new URLSearchParams(searchParams);
          next.delete('plugin');
          setSearchParams(next, { replace: true });
        }}
        onToggle={(pluginId, nextEnabled) => void enable(pluginId, nextEnabled)}
        onRequest={openRequest}
      />
      <Modal
        title="申请使用插件"
        open={Boolean(requestTarget)}
        onCancel={closeRequest}
        onOk={() => void submitRequest()}
        okText="提交申请"
        cancelText="取消"
        confirmLoading={requestSubmitting}
        width={520}
        destroyOnClose
      >
        {requestTarget && (
          <div className="space-y-4">
            <div className="rounded-container bg-surface-subtle p-3">
              <Typography.Text strong>{requestTarget.name}</Typography.Text>
              <div className="mt-2 flex flex-wrap gap-1">
                <Tag>{PLUGIN_TYPE_LABEL[requestTarget.plugin_type] ?? requestTarget.plugin_type}</Tag>
                {(requestTarget.version || requestTarget.current_version) && (
                  <Tag color="green">v{requestTarget.version ?? requestTarget.current_version}</Tag>
                )}
                {requestTarget.scope && (
                  <Tag color={requestTarget.scope === 'shared' ? 'blue' : 'default'}>{pluginScopeLabel(requestTarget.scope)}</Tag>
                )}
              </div>
              <p className="mb-0 mt-3 text-xs leading-5 text-ink-secondary">{pluginDescription(requestTarget)}</p>
            </div>
            <div>
              <div className="mb-1 text-xs text-ink-secondary">使用理由（可选，最多 500 字）</div>
              <Input.TextArea
                rows={4}
                maxLength={500}
                showCount
                aria-label="使用理由"
                placeholder="说明你要用它解决什么工作问题，便于管理员判断"
                value={requestReason}
                onChange={(event) => setRequestReason(event.target.value)}
              />
            </div>
            <Typography.Text type="secondary" className="!text-xs">
              审批通过后会自动授权给你的分身，回到本页刷新即可启用。
            </Typography.Text>
          </div>
        )}
      </Modal>
      <Modal title="上传插件" open={open} onCancel={() => setOpen(false)} footer={null} width={560} destroyOnClose>
        <SubmissionWizard onDone={() => { setOpen(false); reload(); }} />
      </Modal>
      <Modal
        title="接入 MCP 服务"
        open={mcpOpen}
        onCancel={() => setMcpOpen(false)}
        footer={null}
        width={760}
        destroyOnClose
      >
        <McpOnboardingWizard
          mode="employee"
          encryptionAvailable={mcpEncryptionAvailable}
          onDone={() => { setMcpOpen(false); reload(); }}
        />
      </Modal>
      <Modal
        title="提交新版本"
        open={Boolean(newVersionTarget)}
        onCancel={() => setNewVersionTarget(null)}
        footer={null}
        width={560}
        destroyOnClose
      >
        {newVersionTarget && (
          <SubmissionWizard
            targetPlugin={newVersionTarget}
            onDone={() => { setNewVersionTarget(null); reload(); }}
          />
        )}
      </Modal>
    </div>
  );
}
