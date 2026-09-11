import { AppstoreAddOutlined, CloudServerOutlined, FileMarkdownOutlined } from '@ant-design/icons';
import { Button, Form, Input, Select, Steps, Upload, message } from 'antd';
import { useState } from 'react';
import type { UploadFile } from 'antd';
import { api } from '../../api/client';

type PluginType = 'skill' | 'mcp';

const MCP_CATEGORIES = [
  { value: 'knowledge', label: '知识库' },
  { value: 'cloud_information', label: '云端资讯' },
  { value: 'fund', label: '基金' },
  { value: 'internal_system', label: '企业内部系统' },
  { value: 'other', label: '其他' },
];

/** 分步上传表单：选择类型 → 文件与基本信息 → 确认提交；切换类型时重置依赖字段。 */
export default function SubmissionWizard({ onDone }: { onDone: () => void }) {
  const [step, setStep] = useState(0);
  const [pluginType, setPluginType] = useState<PluginType>('skill');
  const [files, setFiles] = useState<UploadFile[]>([]);
  const [form] = Form.useForm();

  const chooseType = (type: PluginType) => {
    setPluginType(type);
    // 切换类型重置依赖字段，避免残留另一类型的校验值。
    form.setFieldsValue({
      plugin_type: type,
      mcp_category: undefined,
      deployment_mode: type === 'skill' ? 'instruction' : undefined,
      scope: 'personal',
      data_level: 'L1',
    });
    setStep(1);
  };

  const next = async () => {
    if (step === 1) {
      await form.validateFields(['name', 'plugin_type']);
      if (pluginType === 'mcp') await form.validateFields(['mcp_category', 'deployment_mode']);
      const file = files[0]?.originFileObj;
      if (!file) { message.error('请选择 ZIP 文件'); return; }
    }
    setStep(step + 1);
  };

  const submit = async () => {
    const values = await form.validateFields();
    const file = files[0]?.originFileObj;
    if (!file) { message.error('请选择 ZIP 文件'); return; }
    const body = new FormData();
    Object.entries(values).forEach(([key, value]) => {
      if (value !== undefined && value !== null) body.append(key, String(value));
    });
    body.append('file', file);
    await api.submitPlugin(body);
    message.success('插件已提交，等待审核');
    form.resetFields();
    setFiles([]);
    setStep(0);
    onDone();
  };

  return (
    <div>
      <Steps
        size="small"
        current={step}
        className="mb-5"
        items={[{ title: '选择类型' }, { title: '文件与信息' }, { title: '确认提交' }]}
      />
      {step === 0 && (
        <div className="grid gap-3 sm:grid-cols-2">
          <button
            type="button"
            onClick={() => chooseType('skill')}
            className="flex cursor-pointer flex-col items-center gap-3 rounded-container border border-line bg-surface p-6 text-center transition-colors hover:border-brand hover:bg-brand-soft"
          >
            <FileMarkdownOutlined className="text-3xl text-brand" />
            <div>
              <div className="font-medium text-ink">Skill · 工作方法</div>
              <div className="text-xs text-ink-secondary">个人指令与工作方法，L1 可自动发布</div>
            </div>
          </button>
          <button
            type="button"
            onClick={() => chooseType('mcp')}
            className="flex cursor-pointer flex-col items-center gap-3 rounded-container border border-line bg-surface p-6 text-center transition-colors hover:border-brand hover:bg-brand-soft"
          >
            <CloudServerOutlined className="text-3xl text-brand" />
            <div>
              <div className="font-medium text-ink">MCP · 数据与工具</div>
              <div className="text-xs text-ink-secondary">连接数据源或工具服务，需管理员审核发布</div>
            </div>
          </button>
        </div>
      )}

      {step === 1 && (
        <Form form={form} layout="vertical" initialValues={{ plugin_type: pluginType, scope: 'personal', deployment_mode: pluginType === 'skill' ? 'instruction' : undefined, data_level: 'L1', version: '1.0.0' }}>
          <Form.Item name="plugin_type" hidden><Input /></Form.Item>
          <Form.Item name="name" label="插件名称" rules={[{ required: true, message: '请输入插件名称' }]}>
            <Input placeholder="例如：报销制度速答" maxLength={100} />
          </Form.Item>
          <div className="grid grid-cols-2 gap-3">
            <Form.Item name="scope" label="范围">
              <Select options={[{ value: 'personal', label: '个人' }, { value: 'shared', label: '共享' }]} />
            </Form.Item>
            <Form.Item name="data_level" label="数据等级">
              <Select options={['L1', 'L2', 'L3'].map((value) => ({ value }))} />
            </Form.Item>
          </div>
          {pluginType === 'mcp' && (
            <div className="grid grid-cols-2 gap-3">
              <Form.Item name="mcp_category" label="MCP 分类" rules={[{ required: true, message: '请选择分类' }]}>
                <Select options={MCP_CATEGORIES} />
              </Form.Item>
              <Form.Item name="deployment_mode" label="部署模式" rules={[{ required: true, message: '请选择部署模式' }]}>
                <Select options={[{ value: 'external', label: 'External · 外部服务引用' }, { value: 'hosted', label: 'Hosted · 平台托管源码' }]} />
              </Form.Item>
            </div>
          )}
          <Form.Item name="version" label="版本"><Input /></Form.Item>
          <Upload beforeUpload={() => false} accept=".zip" maxCount={1} fileList={files} onChange={({ fileList }) => setFiles(fileList)}>
            <Button icon={<AppstoreAddOutlined />}>选择 ZIP 文件</Button>
          </Upload>
        </Form>
      )}

      {step === 2 && (
        <div className="rounded-container bg-surface-subtle p-4">
          <div className="mb-2 flex items-center gap-2">
            <AppstoreAddOutlined className="text-brand" />
            <span className="font-medium text-ink">确认提交信息</span>
          </div>
          <Form form={form} component={false}>
            <Form.Item noStyle shouldUpdate>
              {({ getFieldsValue }) => {
                const values = getFieldsValue();
                return (
                  <div className="space-y-1.5 text-sm">
                    <div>类型：{pluginType === 'skill' ? 'Skill · 工作方法' : 'MCP · 数据与工具'}</div>
                    <div>名称：{values.name ?? '—'}</div>
                    <div>范围：{values.scope === 'shared' ? '共享' : '个人'}</div>
                    <div>数据等级：{values.data_level}</div>
                    {pluginType === 'mcp' && (
                      <>
                        <div>MCP 分类：{MCP_CATEGORIES.find((c) => c.value === values.mcp_category)?.label ?? values.mcp_category}</div>
                        <div>部署模式：{values.deployment_mode === 'hosted' ? 'Hosted · 平台托管源码' : 'External · 外部服务引用'}</div>
                      </>
                    )}
                    <div>版本：{values.version}</div>
                    <div>文件：{files[0]?.name ?? '未选择'}</div>
                  </div>
                );
              }}
            </Form.Item>
          </Form>
        </div>
      )}

      <div className="mt-5 flex justify-end gap-2">
        {step > 0 && <Button onClick={() => setStep(step - 1)}>上一步</Button>}
        {step < 2 && <Button type="primary" onClick={() => void next()}>下一步</Button>}
        {step === 2 && <Button type="primary" onClick={() => void submit()}>提交</Button>}
      </div>
    </div>
  );
}
