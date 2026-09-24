import { Alert, Checkbox, Form, Input, InputNumber, Modal, Select, Typography, message } from 'antd';
import { useState } from 'react';
import { api } from '../../api/client';
import type { McpRegisteredService, McpServiceUpdateInput } from '../../api/client';
import { DATA_LEVEL_OPTIONS, MCP_CATEGORY_OPTIONS } from './McpServiceForm';

/** 编辑表单值：密钥留空 = 沿用已保存的密钥。 */
interface EditValues {
  name: string;
  description?: string;
  mcp_category: string;
  data_level: string;
  timeout_seconds: number;
  url: string;
  auth_header_name?: string;
  api_key?: string;
  clear_api_key?: boolean;
}

/**
 * 编辑已登记的 MCP 服务。
 *
 * 名称 / 简介 / 分类 / 数据等级落在插件本身（授权判定读的就是数据等级）；
 * 地址 / 超时 / 鉴权头 / 密钥落在运行信息里。改地址或密钥后平台立即重测，
 * 工具清单发生变化时后端会自动发布一个新版本，这里如实提示。
 * 密钥一旦保存永不回显，只显示掩码；留空即沿用。
 */
export default function McpServiceEditModal({
  service,
  open,
  onClose,
  onDone,
}: {
  service: McpRegisteredService | null;
  open: boolean;
  onClose: () => void;
  onDone: () => void;
}) {
  const [form] = Form.useForm<EditValues>();
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async () => {
    if (!service) return;
    const values = await form.validateFields();
    setSubmitting(true);
    setError(null);
    const payload: McpServiceUpdateInput = {
      name: values.name.trim(),
      description: (values.description ?? '').trim(),
      mcp_category: values.mcp_category,
      data_level: values.data_level,
      url: values.url.trim(),
      timeout_seconds: values.timeout_seconds,
      auth_header_name: values.auth_header_name?.trim() || 'x-api-key',
    };
    const typedKey = (values.api_key ?? '').trim();
    if (values.clear_api_key) {
      payload.api_key = '';
    } else if (typedKey) {
      payload.api_key = typedKey;
    }
    try {
      const result = await api.updateAdminMcpService(service.plugin_id, payload);
      message.success(result.new_version ? `已保存，并发布新版本 v${result.new_version}` : '已保存');
      onDone();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal
      title={service ? `编辑 ${service.name}` : '编辑 MCP 服务'}
      open={open}
      onCancel={onClose}
      onOk={() => void submit()}
      okText="保存"
      cancelText="取消"
      confirmLoading={submitting}
      width={640}
      destroyOnHidden
    >
      {service && (
        <Form
          form={form}
          layout="vertical"
          initialValues={{
            name: service.name,
            description: service.description,
            mcp_category: service.mcp_category ?? 'other',
            data_level: service.data_level,
            timeout_seconds: service.timeout_seconds ?? 20,
            url: service.url,
            auth_header_name: service.auth_header_name ?? 'x-api-key',
            api_key: '',
            clear_api_key: false,
          }}
        >
          <Form.Item name="name" label="服务名称" rules={[{ required: true, message: '请输入服务名称' }]}>
            <Input maxLength={120} />
          </Form.Item>
          <Form.Item name="description" label="服务简介">
            <Input.TextArea rows={2} maxLength={2000} showCount />
          </Form.Item>

          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <Form.Item name="mcp_category" label="MCP 分类" rules={[{ required: true, message: '请选择分类' }]}>
              <Select options={MCP_CATEGORY_OPTIONS} />
            </Form.Item>
            <Form.Item
              name="data_level"
              label="数据等级"
              tooltip="授权判定读的就是这个等级：L3 表示涉及客户隐私、交易或内部经营数据"
            >
              <Select options={DATA_LEVEL_OPTIONS} />
            </Form.Item>
          </div>

          <Form.Item
            name="url"
            label="服务地址"
            rules={[
              { required: true, message: '请输入 MCP 服务地址' },
              { pattern: /^https?:\/\/.+/i, message: '地址必须以 http:// 或 https:// 开头' },
            ]}
            tooltip="地址变化后平台会立即重新探测"
          >
            <Input maxLength={500} />
          </Form.Item>

          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <Form.Item name="auth_header_name" label="鉴权头名称">
              <Input maxLength={64} />
            </Form.Item>
            <Form.Item name="timeout_seconds" label="请求超时（秒）" tooltip="5~60 秒">
              <InputNumber min={5} max={60} className="w-full" />
            </Form.Item>
          </div>

          <Form.Item
            name="api_key"
            label="鉴权密钥"
            tooltip="留空表示沿用已保存的密钥；保存后只显示掩码，平台不会回显明文"
          >
            <Input.Password
              placeholder={service.authenticated ? `留空沿用已保存密钥（${service.secret_hint ?? '已配置'}）` : '留空表示无鉴权'}
              autoComplete="new-password"
            />
          </Form.Item>
          <Form.Item name="clear_api_key" valuePropName="checked" className="!mb-2">
            <Checkbox>清除已保存的密钥，改为匿名接入</Checkbox>
          </Form.Item>

          {error && (
            <Alert
              type="error"
              showIcon
              message="保存失败"
              description={`${error}（若只是重新测试连接失败，填写的信息已保存，可修正后重试）`}
            />
          )}

          <Typography.Text type="secondary" className="!text-xs">
            密钥只用于构造请求头，不写入日志、不进入提示词；界面上只显示掩码。
          </Typography.Text>
        </Form>
      )}
    </Modal>
  );
}
