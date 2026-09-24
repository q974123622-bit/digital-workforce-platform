import { ReloadOutlined, RobotOutlined } from '@ant-design/icons';
import { Alert, Button, Input, Tag, Typography } from 'antd';
import type { McpIntroDraft } from '../../api/client';
import { MCP_CATEGORY_LABEL } from './McpServiceForm';

const { Text } = Typography;

/**
 * 步骤三：AI 简介确认。
 *
 * 大模型产出的是**草稿**：插件中文名、简介、每个工具的中文说明都可以逐项编辑；
 * 不满意时在下方输入自定义提示词点「重新生成」得到新一版（会覆盖当前编辑内容）。
 * 模型不可用时后端降级为「原样透传 + 规整」，此处会给出降级原因。
 */
export default function McpIntroEditor({
  draft,
  name,
  description,
  toolDescriptions,
  toolNames,
  onNameChange,
  onDescriptionChange,
  onToolDescriptionChange,
  prompt,
  onPromptChange,
  onRegenerate,
  regenerating,
}: {
  draft: McpIntroDraft | null;
  name: string;
  description: string;
  toolDescriptions: Record<string, string>;
  toolNames: string[];
  onNameChange: (value: string) => void;
  onDescriptionChange: (value: string) => void;
  onToolDescriptionChange: (toolName: string, value: string) => void;
  prompt: string;
  onPromptChange: (value: string) => void;
  onRegenerate: () => void;
  regenerating: boolean;
}) {
  return (
    <div className="space-y-4">
      {draft && !draft.generated && (
        <Alert
          type="warning"
          showIcon
          message="本版未由大模型生成，已按服务原始说明规整"
          description={draft.degraded_reason ?? '大模型暂时不可用，可稍后点「重新生成」重试。'}
        />
      )}
      {draft?.generated && (
        <Alert
          type="success"
          showIcon
          icon={<RobotOutlined />}
          message="以下内容由平台大模型生成，请确认后发布"
          description="确认前可以自由修改；密钥、地址等敏感信息不会进入模型提示词。"
        />
      )}

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        <div className="sm:col-span-2">
          <div className="mb-1 text-xs text-ink-secondary">插件中文名</div>
          <Input
            aria-label="插件中文名"
            value={name}
            maxLength={60}
            onChange={(event) => onNameChange(event.target.value)}
            placeholder="例如：行情数据查询服务"
          />
        </div>
        <div>
          <div className="mb-1 text-xs text-ink-secondary">分类 / 数据等级建议</div>
          <div className="flex h-8 items-center gap-2">
            <Tag color="blue">{MCP_CATEGORY_LABEL[draft?.mcp_category ?? 'other'] ?? draft?.mcp_category ?? '其他'}</Tag>
            <Tag color={draft?.data_level === 'L3' ? 'red' : draft?.data_level === 'L2' ? 'orange' : 'default'}>
              {draft?.data_level ?? 'L1'}
            </Tag>
          </div>
        </div>
      </div>

      <div>
        <div className="mb-1 text-xs text-ink-secondary">插件简介</div>
        <Input.TextArea
          aria-label="插件简介"
          rows={3}
          maxLength={400}
          showCount
          value={description}
          onChange={(event) => onDescriptionChange(event.target.value)}
          placeholder="说明这个服务能解决什么业务问题"
        />
      </div>

      <div className="rounded-container border border-line bg-surface p-3">
        <div className="mb-2 flex items-center gap-2">
          <Text strong>工具中文说明</Text>
          <Tag>{toolNames.length}</Tag>
        </div>
        <div className="max-h-[280px] space-y-2 overflow-y-auto pr-1">
          {toolNames.map((toolName) => (
            <div key={toolName} className="rounded-control bg-surface-subtle px-3 py-2">
              <div className="mb-1 font-mono text-xs text-ink">{toolName}</div>
              <Input.TextArea
                aria-label={`工具说明 ${toolName}`}
                autoSize={{ minRows: 1, maxRows: 3 }}
                maxLength={200}
                value={toolDescriptions[toolName] ?? ''}
                onChange={(event) => onToolDescriptionChange(toolName, event.target.value)}
                placeholder="说明何时该调用这个工具"
              />
            </div>
          ))}
          {toolNames.length === 0 && <Text type="secondary" className="!text-xs">该服务未暴露工具</Text>}
        </div>
      </div>

      <div className="rounded-container bg-brand-soft p-3">
        <div className="mb-1 text-xs text-ink-secondary">
          不满意？写一段提示词，让大模型按你的口径重新生成一版
        </div>
        <div className="flex flex-col gap-2 sm:flex-row">
          <Input
            aria-label="自定义生成提示词"
            className="flex-1"
            value={prompt}
            maxLength={1000}
            placeholder="例如：突出风险提示，简介控制在 60 字以内"
            onChange={(event) => onPromptChange(event.target.value)}
            onPressEnter={() => { if (!regenerating) onRegenerate(); }}
          />
          <Button icon={<ReloadOutlined />} loading={regenerating} onClick={onRegenerate}>
            重新生成
          </Button>
        </div>
        <div className="mt-1 text-xs text-ink-tertiary">重新生成会覆盖当前已编辑的名称、简介与工具说明。</div>
      </div>
    </div>
  );
}
