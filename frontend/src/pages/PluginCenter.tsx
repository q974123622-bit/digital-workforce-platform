import { AppstoreAddOutlined, CloudServerOutlined, FileMarkdownOutlined, UploadOutlined } from '@ant-design/icons';
import { Alert, Button, Card, Form, Input, Modal, Select, Space, Table, Tag, Typography, Upload, message } from 'antd';
import { useCallback, useState } from 'react';
import type { UploadFile } from 'antd';
import { api } from '../api/client';
import { useAsyncData } from '../hooks/useAsyncData';
import { ErrorState, LoadingState } from '../components/PageState';

export default function PluginCenter() {
  const fetcher = useCallback(async () => ({ catalog: await api.getMyPlugins(), submissions: await api.getMyPluginSubmissions() }), []);
  const { data, loading, error, reload } = useAsyncData(fetcher);
  const [open, setOpen] = useState(false);
  const [files, setFiles] = useState<UploadFile[]>([]);
  const [form] = Form.useForm();
  if (loading) return <LoadingState rows={6} />;
  if (error) return <ErrorState onRetry={reload} />;
  const submit = async () => {
    const values = await form.validateFields();
    const file = files[0]?.originFileObj;
    if (!file) { message.error('请选择 ZIP 文件'); return; }
    const body = new FormData();
    Object.entries(values).forEach(([key, value]) => body.append(key, String(value)));
    body.append('file', file);
    await api.submitPlugin(body); message.success('插件已提交'); setOpen(false); form.resetFields(); setFiles([]); reload();
  };
  return <div className="space-y-4">
    <div className="flex items-start justify-between"><div><Typography.Title level={3} className="!mb-1">我的插件</Typography.Title><Typography.Text type="secondary">插件分为 Skill 工作方法和 MCP 数据/工具服务</Typography.Text></div><Button type="primary" icon={<UploadOutlined />} onClick={() => setOpen(true)}>上传插件</Button></div>
    <Alert showIcon type="info" message="权限边界" description="个人 L1 安全 Skill 可自动发布；MCP、共享插件及 L2/L3 插件必须由管理员审核并手动发布。" />
    <Card title="当前数字分身已装载">
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">{(data?.catalog.effective ?? []).map((item) => <Card size="small" key={item.plugin_id}><Space align="start"><div className={`rounded-lg p-2 ${item.plugin_type === 'skill' ? 'bg-[#e8f3ff] text-[#165dff]' : 'bg-[#e8ffea] text-[#00b42a]'}`}>{item.plugin_type === 'skill' ? <FileMarkdownOutlined /> : <CloudServerOutlined />}</div><div><Typography.Text strong>{item.name}</Typography.Text><div className="mt-2"><Tag>{item.plugin_type === 'skill' ? 'Skill' : 'MCP'}</Tag><Tag color="green">v{item.version}</Tag></div></div></Space></Card>)}</div>
      {!data?.catalog.effective.length && <Typography.Text type="secondary">暂未装载插件</Typography.Text>}
    </Card>
    <Card title="我的提交"><Table rowKey="id" pagination={false} dataSource={data?.submissions} columns={[{ title: '插件', dataIndex: 'name' }, { title: '类型', dataIndex: 'plugin_type', render: (v) => <Tag>{v === 'skill' ? 'Skill' : 'MCP'}</Tag> }, { title: '版本', dataIndex: 'version' }, { title: '审核', dataIndex: 'review_status' }, { title: '发布', dataIndex: 'publish_status' }]} /></Card>
    <Modal title={<Space><AppstoreAddOutlined />提交插件</Space>} open={open} onCancel={() => setOpen(false)} onOk={submit} okText="提交">
      <Form form={form} layout="vertical" initialValues={{ plugin_type: 'skill', scope: 'personal', deployment_mode: 'instruction', data_level: 'L1', version: '1.0.0' }}>
        <Form.Item name="name" label="插件名称" rules={[{ required: true }]}><Input /></Form.Item>
        <Form.Item name="plugin_type" label="插件类型"><Select options={[{ value: 'skill', label: 'Skill · 工作方法与指令' }, { value: 'mcp', label: 'MCP · 数据与工具服务' }]} /></Form.Item>
        <div className="grid grid-cols-2 gap-3"><Form.Item name="scope" label="范围"><Select options={[{ value: 'personal', label: '个人' }, { value: 'shared', label: '共享' }]} /></Form.Item><Form.Item name="data_level" label="数据等级"><Select options={['L1','L2','L3'].map((value) => ({ value }))} /></Form.Item></div>
        <Form.Item noStyle shouldUpdate>{({ getFieldValue }) => getFieldValue('plugin_type') === 'mcp' ? <><Form.Item name="mcp_category" label="MCP 分类" initialValue="other"><Select options={[['knowledge','知识库'],['cloud_information','云端资讯'],['fund','基金'],['internal_system','企业内部系统'],['other','其他']].map(([value,label]) => ({ value,label }))} /></Form.Item><Form.Item name="deployment_mode" label="部署模式"><Select options={[{ value: 'external', label: 'External · 外部服务引用' }, { value: 'hosted', label: 'Hosted · 平台托管源码' }]} /></Form.Item></> : null}</Form.Item>
        <Form.Item name="version" label="版本"><Input /></Form.Item>
        <Upload beforeUpload={() => false} accept=".zip" maxCount={1} fileList={files} onChange={({ fileList }) => setFiles(fileList)}><Button icon={<UploadOutlined />}>选择 ZIP</Button></Upload>
      </Form>
    </Modal>
  </div>;
}
