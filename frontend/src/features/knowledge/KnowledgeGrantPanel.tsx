import { useCallback, useEffect, useMemo, useState } from 'react';
import { Alert, Button, Card, Checkbox, Empty, Select, Space, Tag, Typography, message } from 'antd';
import type { KnowledgeGrantMatrix } from '@dwp/shared-schema';
import { api } from '../../api/client';

const TYPE_LABEL: Record<string, string> = {
  virtual: 'AI员工',
  twin: '我的分身',
  rpa: '自动化小助手',
};

/**
 * 知识库独占分配：同一个知识库不得出现在两个 AI 员工的授权里。
 * 已被他人占用的库直接置灰不可选；保存时后端会再做一次互斥校验（冲突返回 409）。
 */
export default function KnowledgeGrantPanel() {
  const [matrix, setMatrix] = useState<KnowledgeGrantMatrix | null>(null);
  const [employeeId, setEmployeeId] = useState<string>();
  const [selected, setSelected] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [errorText, setErrorText] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await api.getKnowledgeGrants();
      setMatrix(data);
      // 这里的 updater 由 React 在下一次渲染时调用，不在 try/catch 覆盖范围内：
      // 一旦响应体缺字段就会在渲染期抛错并整页白屏，因此必须写成可选链。
      setEmployeeId((current) => current ?? data?.employees?.[0]?.employee_id);
    } catch (err) {
      message.error(err instanceof Error ? err.message : '知识库分配加载失败');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  useEffect(() => {
    if (!matrix || !employeeId) return;
    const row = matrix?.employees?.find((item) => item.employee_id === employeeId);
    setSelected(row?.knowledge_base_ids ?? []);
    setErrorText('');
  }, [matrix, employeeId]);

  const occupancy = matrix?.occupancy ?? {};
  const knowledgeBases = matrix?.knowledge_bases ?? [];

  const dirty = useMemo(() => {
    const row = matrix?.employees?.find((item) => item.employee_id === employeeId);
    const current = [...(row?.knowledge_base_ids ?? [])].sort();
    const next = [...selected].sort();
    return current.length !== next.length || current.some((id, index) => id !== next[index]);
  }, [matrix, employeeId, selected]);

  const toggle = (kbId: string, checked: boolean) => {
    setSelected((current) => (checked ? [...current, kbId] : current.filter((id) => id !== kbId)));
    setErrorText('');
  };

  const save = async () => {
    if (!employeeId) return;
    setSaving(true);
    setErrorText('');
    try {
      const next = await api.replaceKnowledgeGrants(employeeId, selected);
      setMatrix(next);
      message.success('知识库分配已保存');
    } catch (err) {
      setErrorText(err instanceof Error ? err.message : '保存失败');
    } finally {
      setSaving(false);
    }
  };

  return (
    <Card
      className="mb-4"
      loading={loading}
      title="知识库独占分配"
      extra={
        <Space>
          {errorText && <Typography.Text type="danger" className="!text-xs">{errorText}</Typography.Text>}
          <Button type="primary" loading={saving} disabled={!employeeId || !dirty} onClick={save}>
            保存分配
          </Button>
        </Space>
      }
    >
      <Alert
        className="mb-3"
        type="info"
        showIcon
        message="同一个知识库只能授权给一个 AI 员工"
        description="允许一个员工持有多库，但任意两个员工的库集合不能有重叠。已被其他员工占用的库会置灰；如需改派，先在该员工处取消再分配给新的员工。"
      />
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <Typography.Text type="secondary">选择 AI 员工</Typography.Text>
        <Select
          className="min-w-[240px]"
          value={employeeId}
          onChange={setEmployeeId}
          options={(matrix?.employees ?? []).map((row) => ({
            value: row.employee_id,
            label: `${row.employee_id} ${row.employee_name}（${TYPE_LABEL[row.employee_type] ?? row.employee_type}）`,
          }))}
        />
        <Tag>{selected.length} 个知识库</Tag>
      </div>
      {knowledgeBases.length === 0 ? (
        <Empty description="暂无可分配的知识库" />
      ) : (
        <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
          {knowledgeBases.map((kb) => {
            const owner = occupancy[kb.id];
            const takenByOther = Boolean(owner) && owner !== employeeId;
            const unavailable = kb.available === false;
            return (
              <label
                key={kb.id}
                title={unavailable ? kb.unavailable_reason : undefined}
                className={`flex items-start gap-2 rounded-control border border-line px-3 py-2 ${
                  takenByOther ? 'cursor-not-allowed bg-surface-subtle opacity-70' : 'cursor-pointer'
                }`}
              >
                <Checkbox
                  className="mt-0.5"
                  disabled={takenByOther}
                  checked={selected.includes(kb.id)}
                  onChange={(event) => toggle(kb.id, event.target.checked)}
                />
                <span className="min-w-0">
                  <span className={`block truncate text-sm ${takenByOther || unavailable ? 'text-ink-tertiary' : 'text-ink'}`}>
                    {kb.name}
                  </span>
                  <span className="mt-0.5 flex flex-wrap items-center gap-1 text-xs text-ink-tertiary">
                    <Tag className="!mr-0">{kb.data_level ?? 'L1'}</Tag>
                    {kb.domain}
                    {takenByOther && <span>已被 {owner} 占用</span>}
                    {!takenByOther && unavailable && <span>数据源当前不可用</span>}
                  </span>
                </span>
              </label>
            );
          })}
        </div>
      )}
    </Card>
  );
}
