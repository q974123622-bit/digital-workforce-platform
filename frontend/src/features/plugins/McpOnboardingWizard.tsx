import { CheckCircleOutlined, CloudServerOutlined, ReloadOutlined } from '@ant-design/icons';
import { Alert, Button, Descriptions, Form, Result, Space, Steps, Tag, Typography, message } from 'antd';
import { useState } from 'react';
import { api } from '../../api/client';
import type {
  McpCreateInput,
  McpIntroDraft,
  McpProbeResult,
  McpRegisteredService,
  McpToolSummary,
} from '../../api/client';
import McpIntroEditor from './McpIntroEditor';
import McpServiceForm from './McpServiceForm';
import type { McpServiceFormValues } from './McpServiceForm';
import { MCP_CATEGORY_LABEL } from './McpServiceForm';
import McpToolPreviewTable from './McpToolPreviewTable';

/** `admin` = 管理端登记即发布；`employee` = 员工自助登记，提交后等待管理员审核。 */
export type McpOnboardingMode = 'admin' | 'employee';

const STEP_TITLES = ['服务信息', '解析工具', '简介确认', '提交'];

/**
 * MCP 服务自助接入向导（四步）。
 *
 * 步骤一填地址与鉴权 → 步骤二看解析结果 → 步骤三确认 AI 补写的中文简介
 * → 步骤四提交（管理端立即发布，员工端进审核队列）。
 * 步骤间可回退且保留已填数据；任一步失败都在原位给出可读错误。
 */
export default function McpOnboardingWizard({
  mode,
  encryptionAvailable = true,
  onDone,
}: {
  mode: McpOnboardingMode;
  /** 管理端从服务列表带入；员工端默认 true（后端仍会兜底拒绝）。 */
  encryptionAvailable?: boolean;
  onDone: () => void;
}) {
  const [step, setStep] = useState(0);
  const [form] = Form.useForm<McpServiceFormValues>();
  const [probe, setProbe] = useState<McpProbeResult | null>(null);
  const [probing, setProbing] = useState(false);
  const [probeError, setProbeError] = useState<string | null>(null);
  const [draft, setDraft] = useState<McpIntroDraft | null>(null);
  const [drafting, setDrafting] = useState(false);
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [toolDescriptions, setToolDescriptions] = useState<Record<string, string>>({});
  const [prompt, setPrompt] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [created, setCreated] = useState<McpRegisteredService | null>(null);

  const scopeLocked = mode === 'employee';
  const toolNames = (probe?.tools ?? []).map((tool) => tool.name);

  const runProbe = async () => {
    const values = await form.validateFields();
    setProbing(true);
    setProbeError(null);
    try {
      const payload = {
        url: values.url.trim(),
        auth_header_name: values.auth_header_name?.trim() || 'x-api-key',
        api_key: values.api_key?.trim() ?? '',
        timeout_seconds: values.timeout_seconds ?? 20,
      };
      const result = await (mode === 'admin' ? api.probeAdminMcp : api.probeMyMcp)(payload);
      setProbe(result);
      setName(String(result.server_info?.title || result.server_info?.name || ''));
      setToolDescriptions(Object.fromEntries(result.tools.map((tool) => [tool.name, tool.description])));
      setDraft(null);
      setStep(1);
    } catch (err) {
      setProbeError((err as Error).message);
    } finally {
      setProbing(false);
    }
  };

  const generate = async (customPrompt: string | null) => {
    if (!probe) return;
    setDrafting(true);
    try {
      const result = await (mode === 'admin' ? api.draftAdminMcp : api.draftMyMcp)({
        url: probe.url,
        server_info: probe.server_info,
        tools: probe.tools,
        prompt: customPrompt,
      });
      setDraft(result);
      setName(result.name);
      setDescription(result.description);
      setToolDescriptions((current) => {
        const next = { ...current };
        result.tools.forEach((tool) => { next[tool.name] = tool.description; });
        return next;
      });
    } catch (err) {
      message.error(`生成简介失败：${(err as Error).message}`);
    } finally {
      setDrafting(false);
    }
  };

  const openIntro = async () => {
    setStep(2);
    await generate(null);
  };

  const submit = async () => {
    if (!probe) return;
    const values = form.getFieldsValue();
    setSubmitting(true);
    setSubmitError(null);
    const tools: McpToolSummary[] = probe.tools.map((tool) => ({
      ...tool,
      description: toolDescriptions[tool.name] ?? tool.description,
    }));
    const payload: McpCreateInput = {
      url: probe.url,
      name: name.trim(),
      description: description.trim(),
      auth_header_name: values.auth_header_name?.trim() || 'x-api-key',
      api_key: values.api_key?.trim() ?? '',
      timeout_seconds: values.timeout_seconds ?? 20,
      mcp_category: draft?.mcp_category ?? values.mcp_category ?? 'other',
      data_level: draft?.data_level ?? values.data_level ?? 'L1',
      scope: scopeLocked ? 'personal' : 'shared',
      version: '1.0.0',
      tools,
      server_info: probe.server_info,
      latency_ms: probe.latency_ms,
    };
    try {
      const result = await (mode === 'admin' ? api.createAdminMcpService : api.createMyMcpService)(payload);
      setCreated(result);
      setStep(4);
    } catch (err) {
      setSubmitError((err as Error).message);
    } finally {
      setSubmitting(false);
    }
  };

  if (step === 4 && created) {
    return (
      <Result
        status="success"
        title={mode === 'admin' ? 'MCP 服务已接入并发布' : 'MCP 服务已提交，等待管理员审核'}
        subTitle={
          mode === 'admin'
            ? '下一步到「AI员工插件配置」把该服务授权给指定AI员工，授权后会话里即可真实调用。'
            : '管理员审核通过并发布后，可在插件中心「可添加」里自助启用。'
        }
        extra={
          <Space direction="vertical" size={4} className="text-left">
            <div>服务名称：{created.name}</div>
            <div>插件 ID：<span className="font-mono text-xs">{created.plugin_id}</span></div>
            <div>工具前缀：<span className="font-mono text-xs">{created.tool_prefix ?? '—'}</span></div>
            <div>工具数量：{created.tools_count ?? 0}</div>
            <div>当前版本：{created.current_version ? `v${created.current_version}` : '待审核，尚未发布'}</div>
            <Button type="primary" className="mt-2" onClick={onDone}>完成</Button>
          </Space>
        }
      />
    );
  }

  return (
    <div>
      <Steps size="small" current={step} className="mb-5" items={STEP_TITLES.map((title) => ({ title }))} />

      {step === 0 && (
        <div>
          <McpServiceForm form={form} scopeLocked={scopeLocked} encryptionAvailable={encryptionAvailable} />
          {probeError && (
            <Alert
              className="mb-3"
              type="error"
              showIcon
              message="测试连接失败"
              description={probeError}
            />
          )}
        </div>
      )}

      {step === 1 && probe && (
        <div>
          <McpToolPreviewTable probe={probe} />
          {probeError && (
            <Alert className="mt-3" type="error" showIcon message="重新测试失败" description={probeError} />
          )}
        </div>
      )}

      {step === 2 && (
        <McpIntroEditor
          draft={draft}
          name={name}
          description={description}
          toolDescriptions={toolDescriptions}
          toolNames={toolNames}
          onNameChange={setName}
          onDescriptionChange={setDescription}
          onToolDescriptionChange={(toolName, value) =>
            setToolDescriptions((current) => ({ ...current, [toolName]: value }))}
          prompt={prompt}
          onPromptChange={setPrompt}
          onRegenerate={() => void generate(prompt.trim() || null)}
          regenerating={drafting}
        />
      )}

      {step === 3 && probe && (
        <div className="space-y-3">
          <Alert
            type="info"
            showIcon
            icon={<CloudServerOutlined />}
            message={mode === 'admin' ? '提交后立即发布' : '提交后等待管理员审核'}
            description={
              mode === 'admin'
                ? '管理员登记本身即授权行为；发布后需在「AI员工插件配置」里授权给指定AI员工才会对会话生效。'
                : '审核通过并发布后，可在插件中心「可添加」里自助启用。'
            }
          />
          <Descriptions bordered size="small" column={1}>
            <Descriptions.Item label="服务地址">
              <span className="break-all font-mono text-xs">{probe.url}</span>
            </Descriptions.Item>
            <Descriptions.Item label="插件名称">{name || '—'}</Descriptions.Item>
            <Descriptions.Item label="插件简介">{description || '—'}</Descriptions.Item>
            <Descriptions.Item label="鉴权方式">
              {probe.authenticated
                ? <Tag color="green">已携带 {probe.auth_header_name}（保存后仅显示掩码）</Tag>
                : <Tag>匿名接入</Tag>}
            </Descriptions.Item>
            <Descriptions.Item label="工具数量">{probe.tools.length}</Descriptions.Item>
            <Descriptions.Item label="分类 / 数据等级">
              <Tag color="blue">{MCP_CATEGORY_LABEL[draft?.mcp_category ?? 'other'] ?? '其他'}</Tag>
              <Tag>{draft?.data_level ?? 'L1'}</Tag>
            </Descriptions.Item>
            <Descriptions.Item label="插件范围">{scopeLocked ? '个人（仅自己分身）' : '共享'}</Descriptions.Item>
          </Descriptions>
          {submitError && <Alert type="error" showIcon message="提交失败" description={submitError} />}
          <Typography.Text type="secondary" className="!text-xs">
            <CheckCircleOutlined className="mr-1" />
            密钥只用于构造请求头，不会回显、不会写入日志或提示词。
          </Typography.Text>
        </div>
      )}

      <div className="mt-5 flex justify-end gap-2">
        {step > 0 && step < 4 && (
          <Button onClick={() => { setProbeError(null); setStep(step - 1); }}>上一步</Button>
        )}
        {step === 0 && (
          <Button type="primary" loading={probing} onClick={() => void runProbe()}>
            测试连接并解析
          </Button>
        )}
        {step === 1 && (
          <Button icon={<ReloadOutlined />} loading={probing} onClick={() => void runProbe()}>
            重新测试连接
          </Button>
        )}
        {step === 1 && (
          <Button type="primary" loading={drafting} onClick={() => void openIntro()}>
            生成中文简介
          </Button>
        )}
        {step === 2 && (
          <Button type="primary" onClick={() => setStep(3)}>下一步</Button>
        )}
        {step === 3 && (
          <Button type="primary" loading={submitting} onClick={() => void submit()}>
            {mode === 'admin' ? '确认接入并发布' : '确认提交审核'}
          </Button>
        )}
      </div>
    </div>
  );
}
