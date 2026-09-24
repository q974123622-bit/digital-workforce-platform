import { Alert, Form, Input, InputNumber, Select, Typography } from 'antd';
import type { FormInstance } from 'antd';
import { MCP_CATEGORY_LABELS } from './pluginMeta';

/** 步骤一提交的表单值：地址 + 鉴权 + 分类/等级/范围。 */
export interface McpServiceFormValues {
  url: string;
  auth_header_name?: string;
  api_key?: string;
  timeout_seconds?: number;
  mcp_category?: string;
  data_level?: string;
  scope?: string;
}

/**
 * 分类选项从 `pluginMeta.MCP_CATEGORY_LABELS` 派生，不再各写一份
 * ——之前两处枚举不同步，导致 `employee_service` 在市场里显示英文原值。
 */
export const MCP_CATEGORY_OPTIONS = Object.entries(MCP_CATEGORY_LABELS).map(([value, label]) => ({ value, label }));

export const MCP_CATEGORY_LABEL: Record<string, string> = MCP_CATEGORY_LABELS;

export const DATA_LEVEL_OPTIONS = ['L1', 'L2', 'L3'].map((value) => ({ value }));

/**
 * 步骤一：填写 MCP 服务地址与访问方式。
 *
 * 鉴权只支持 API Key（自定义头名 + 值）；头名默认 `x-api-key`，覆盖盈米等既有场景，
 * 无鉴权服务把密钥留空即可。密钥一旦保存永不回显，界面上只会出现掩码。
 */
export default function McpServiceForm({
  form,
  scopeLocked,
  encryptionAvailable,
}: {
  form: FormInstance<McpServiceFormValues>;
  /** 员工端锁定为个人范围；管理端只能登记共享服务。 */
  scopeLocked: boolean;
  /** 凭据加密密钥是否已配置；未配置时禁止填写密钥（后端会拒绝保存）。 */
  encryptionAvailable: boolean;
}) {
  return (
    <Form
      form={form}
      layout="vertical"
      initialValues={{
        auth_header_name: 'x-api-key',
        timeout_seconds: 20,
        mcp_category: 'other',
        data_level: 'L1',
        scope: scopeLocked ? 'personal' : 'shared',
      }}
    >
      <Form.Item
        name="url"
        label="MCP 服务地址"
        rules={[
          { required: true, message: '请输入 MCP 服务地址' },
          { pattern: /^https?:\/\/.+/i, message: '地址必须以 http:// 或 https:// 开头' },
        ]}
        tooltip="支持内网地址；平台会按 Streamable HTTP 协议完成握手并解析工具清单"
      >
        <Input placeholder="例如：http://10.56.35.240:9100/mcp" maxLength={500} allowClear />
      </Form.Item>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <Form.Item
          name="auth_header_name"
          label="鉴权头名称"
          tooltip="无鉴权服务可忽略；盈米等平台使用 x-api-key"
        >
          <Input placeholder="x-api-key" maxLength={64} />
        </Form.Item>
        <Form.Item
          name="api_key"
          label="鉴权密钥"
          tooltip="留空表示匿名接入；保存后只显示掩码，平台不会回显明文"
        >
          <Input.Password placeholder="留空表示无鉴权" autoComplete="new-password" />
        </Form.Item>
      </div>

      {!encryptionAvailable && (
        <Alert
          className="mb-4"
          type="warning"
          showIcon
          message="未配置凭据加密密钥，只能接入无鉴权服务"
          description="请联系运维在部署配置里设置 DWP_MCP_CREDENTIAL_KEY（或放置 /run/secrets/mcp_credential_key），再登记需要鉴权的 MCP 服务。"
        />
      )}

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        <Form.Item name="mcp_category" label="MCP 分类" rules={[{ required: true, message: '请选择分类' }]}>
          <Select options={MCP_CATEGORY_OPTIONS} />
        </Form.Item>
        <Form.Item name="data_level" label="数据等级" tooltip="L3 表示涉及客户隐私、交易或内部经营数据">
          <Select options={DATA_LEVEL_OPTIONS} />
        </Form.Item>
        <Form.Item name="timeout_seconds" label="请求超时（秒）" tooltip="5~60 秒">
          <InputNumber min={5} max={60} className="w-full" />
        </Form.Item>
      </div>

      <Form.Item
        name="scope"
        label="插件范围"
        tooltip={scopeLocked ? '员工自助登记只能给自己分身使用' : '管理端登记企业级共享服务，再授权给指定AI员工'}
      >
        <Select
          disabled={scopeLocked}
          options={
            scopeLocked
              ? [{ value: 'personal', label: '个人（仅自己分身可用）' }]
              : [{ value: 'shared', label: '共享（可授权给任意AI员工）' }]
          }
        />
      </Form.Item>

      <Typography.Text type="secondary" className="!text-xs">
        平台会向该地址发送 MCP <code>initialize</code> 与 <code>tools/list</code> 请求以解析工具清单；
        密钥只用于构造请求头，不写入日志、不进入提示词。
      </Typography.Text>
    </Form>
  );
}
