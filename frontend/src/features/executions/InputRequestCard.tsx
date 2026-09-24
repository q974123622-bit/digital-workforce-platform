import { useState } from 'react';
import { Alert, Button, Input, Tag } from 'antd';
import { CheckCircleOutlined, KeyOutlined, LoadingOutlined } from '@ant-design/icons';
import type { ToolInputField, ToolInputRequest } from '../../api/client';

/** 该字段是否必须由用户重新填写：非必填、或已保存过且未过期的凭据都算「无需填写」。 */
export function fieldNeedsValue(field: ToolInputField): boolean {
  if (!field.required) return false;
  if (field.reason === 'expired') return true;
  return !(field.masked ?? '').trim();
}

/**
 * MCP 工具「缺失信息补全」气泡。
 *
 * 形态是执行轨迹里的**内联卡片**（不是弹窗）：工具缺鉴权（key / JWT / token）或缺必填
 * 业务参数时出现，逐条列出缺失字段，用户填完点「提交并继续」即由后端加密保存并自动
 * 续跑一轮。凭据类字段默认遮蔽，只回显掩码，明文既不入对话也不入审计。
 */
export default function InputRequestCard({
  request,
  running,
  submitting = false,
  error,
  ignored = false,
  onSubmit,
  onIgnore,
}: {
  request: ToolInputRequest;
  /** 本轮执行是否仍在进行：进行中禁用提交（后端同会话串行，抢跑只会 409）。 */
  running: boolean;
  submitting?: boolean;
  error?: string;
  /** 是否已被用户忽略（状态由页面持有，切会话再回来仍是收起态）。 */
  ignored?: boolean;
  onSubmit: (values: Record<string, string>) => void;
  onIgnore?: (requestId: string) => void;
}) {
  const [values, setValues] = useState<Record<string, string>>({});

  const fields = request.fields ?? [];
  const keys = fields.map((field) => field.key).join('、') || request.tool_name;
  const submitted = request.status === 'submitted';

  if (submitted) {
    return (
      <div className="mt-2 flex items-center gap-2 rounded-container border border-line bg-[var(--dwp-success-soft)] px-3 py-2">
        <CheckCircleOutlined style={{ color: 'var(--dwp-success)' }} />
        <span className="text-xs text-ink-secondary">已补充 {keys} · 已继续执行</span>
      </div>
    );
  }

  if (ignored) {
    return (
      <div className="mt-2 rounded-container border border-line bg-surface-subtle px-3 py-2 text-xs text-ink-tertiary">
        已忽略本次信息补充（{keys}），该工具本次未执行
      </div>
    );
  }

  const missing = fields.filter((field) => fieldNeedsValue(field) && !(values[field.key] ?? '').trim());
  const expired = fields.some((field) => field.reason === 'expired');
  const subtitle = expired
    ? '之前保存的凭据已过期，需要重新提供'
    : fields.some((field) => field.kind === 'secret')
      ? '需要你的个人凭据才能继续这次查询'
      : '该工具还需要补充必填参数';

  const setValue = (key: string, value: string) =>
    setValues((current) => ({ ...current, [key]: value }));

  return (
    <div className="mt-2 rounded-container border border-line bg-[var(--dwp-surface-elevated)] p-3 shadow-sm">
      <div className="flex items-start gap-2.5">
        <span className="grid h-7 w-7 shrink-0 place-items-center rounded-control bg-brand-soft text-brand">
          {submitting ? <LoadingOutlined /> : <KeyOutlined />}
        </span>
        <div className="min-w-0 flex-1">
          <div className="text-sm font-semibold text-ink">需要补充信息</div>
          <div className="text-xs text-ink-secondary">{subtitle}</div>
        </div>
      </div>

      <div className="mt-2.5 space-y-2.5">
        {fields.map((field) => (
          <div key={field.key} className="border-l-2 border-danger pl-2.5">
            <div className="flex flex-wrap items-center gap-2">
              <code className="rounded bg-surface-subtle px-1.5 py-0.5 font-mono text-xs text-ink">{field.key}</code>
              {field.required && <Tag className="!m-0" color="error">必填</Tag>}
              {field.masked
                ? <span className="text-[11px] text-ink-tertiary">已保存 {field.masked}</span>
                : null}
            </div>
            {field.hint && <div className="mt-1 text-xs text-ink-secondary">{field.hint}</div>}
            <div className="mt-1.5">
              {field.kind === 'secret' ? (
                <Input.Password
                  value={values[field.key] ?? ''}
                  onChange={(event) => setValue(field.key, event.target.value)}
                  placeholder={field.masked && field.reason !== 'expired' ? '留空则沿用已保存的凭据' : '粘贴你的个人凭据'}
                  autoComplete="new-password"
                  disabled={running || submitting}
                />
              ) : (
                <Input
                  value={values[field.key] ?? ''}
                  onChange={(event) => setValue(field.key, event.target.value)}
                  placeholder={`请输入 ${field.label || field.key}`}
                  disabled={running || submitting}
                />
              )}
            </div>
          </div>
        ))}
      </div>

      {error && <Alert type="error" showIcon className="mt-2.5" message={error} />}

      <div className="mt-2.5 flex items-center justify-end gap-2">
        {running && <span className="mr-auto text-[11px] text-ink-tertiary">本轮结束后可提交</span>}
        <Button
          size="small"
          type="text"
          disabled={submitting}
          onClick={() => onIgnore?.(request.id)}
        >
          忽略
        </Button>
        <Button
          size="small"
          type="primary"
          loading={submitting}
          disabled={running || missing.length > 0}
          onClick={() => onSubmit(values)}
        >
          提交并继续
        </Button>
      </div>
    </div>
  );
}
