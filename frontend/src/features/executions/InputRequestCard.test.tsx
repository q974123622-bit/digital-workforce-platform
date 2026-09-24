import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import InputRequestCard, { fieldNeedsValue } from './InputRequestCard';
import type { ToolInputField, ToolInputRequest } from '../../api/client';

const secretField = (over: Partial<ToolInputField> = {}): ToolInputField => ({
  key: 'X-MCP-JWT', label: '兴证e家用户令牌', kind: 'secret', required: true,
  hint: '从兴证e家 App 复制你的个人令牌', source: 'user_secret', masked: '', reason: 'missing',
  ...over,
});

const request = (over: Partial<ToolInputRequest> = {}): ToolInputRequest => ({
  id: 'TI-1', conversation_id: 'CONV-1', execution_id: 'EX-1', trace_id: 'T-1',
  plugin_id: 'xyzq-ehome-mcp', tool_name: 'getEmpCardBalance',
  fields: [secretField()], reason: 'missing', status: 'pending',
  submitted_values: {}, resolved_at: null, resolved_by: '', created_at: '2026-09-22T10:00:00',
  ...over,
});

describe('InputRequestCard', () => {
  it('凭据字段默认遮蔽，并提示已保存的掩码', () => {
    render(
      <InputRequestCard
        request={request({ fields: [secretField({ masked: '****abcd' })] })}
        running={false}
        onSubmit={vi.fn()}
      />,
    );
    expect(screen.getByText('需要补充信息')).toBeInTheDocument();
    expect(screen.getByText('X-MCP-JWT')).toBeInTheDocument();
    expect(screen.getByText('已保存 ****abcd')).toBeInTheDocument();
    const input = screen.getByPlaceholderText('留空则沿用已保存的凭据');
    expect(input).toHaveAttribute('type', 'password');
  });

  it('必填项为空时不能提交，填写后才可提交并回传输入值', () => {
    const onSubmit = vi.fn();
    render(<InputRequestCard request={request()} running={false} onSubmit={onSubmit} />);
    const submit = screen.getByRole('button', { name: '提交并继续' });
    expect(submit).toBeDisabled();

    fireEvent.change(screen.getByPlaceholderText('粘贴你的个人凭据'), {
      target: { value: 'jwt-from-employee' },
    });
    expect(submit).toBeEnabled();
    fireEvent.click(submit);
    expect(onSubmit).toHaveBeenCalledWith({ 'X-MCP-JWT': 'jwt-from-employee' });
  });

  it('本轮执行未结束时禁用提交并给出提示', () => {
    render(<InputRequestCard request={request()} running onSubmit={vi.fn()} />);
    expect(screen.getByRole('button', { name: '提交并继续' })).toBeDisabled();
    expect(screen.getByText('本轮结束后可提交')).toBeInTheDocument();
  });

  it('提交中忽略点击，避免重复提交', () => {
    const onSubmit = vi.fn();
    render(
      <InputRequestCard
        request={request({ fields: [secretField({ masked: '****abcd' })] })}
        running={false}
        submitting
        onSubmit={onSubmit}
      />,
    );
    // antd 的 loading 态不加 disabled 属性，只是屏蔽点击（这里按行为断言）
    const button = screen.getByRole('button', { name: /提交并继续/ });
    expect(button).toHaveClass('ant-btn-loading');
    fireEvent.click(button);
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it('参数类字段用普通输入框，值一并提交', () => {
    const onSubmit = vi.fn();
    render(
      <InputRequestCard
        request={request({
          tool_name: 'getXtSubmitList',
          fields: [{
            key: 'keyword', label: '关键字', kind: 'text', required: true,
            hint: '该字段由服务端要求必填', source: '', masked: '', reason: 'tool_error',
          }],
        })}
        running={false}
        onSubmit={onSubmit}
      />,
    );
    const input = screen.getByPlaceholderText('请输入 关键字');
    expect(input).not.toHaveAttribute('type', 'password');
    fireEvent.change(input, { target: { value: '报销' } });
    fireEvent.click(screen.getByRole('button', { name: '提交并继续' }));
    expect(onSubmit).toHaveBeenCalledWith({ keyword: '报销' });
  });

  it('已提交后收起为结果条', () => {
    render(
      <InputRequestCard
        request={request({ status: 'submitted', submitted_values: { 'X-MCP-JWT': '****loyee' } })}
        running={false}
        onSubmit={vi.fn()}
      />,
    );
    expect(screen.getByText('已补充 X-MCP-JWT · 已继续执行')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '提交并继续' })).not.toBeInTheDocument();
  });

  it('忽略后收起为灰字一行', () => {
    render(<InputRequestCard request={request()} running={false} ignored onSubmit={vi.fn()} />);
    expect(screen.getByText(/已忽略本次信息补充/)).toBeInTheDocument();
  });

  it('忽略按钮把请求 id 回传给页面', () => {
    const onIgnore = vi.fn();
    render(<InputRequestCard request={request()} running={false} onSubmit={vi.fn()} onIgnore={onIgnore} />);
    fireEvent.click(screen.getByRole('button', { name: '忽略' }));
    expect(onIgnore).toHaveBeenCalledWith('TI-1');
  });

  it('提交失败时原位显示原因', () => {
    render(
      <InputRequestCard
        request={request()}
        running={false}
        error="当前会话已有任务正在执行"
        onSubmit={vi.fn()}
      />,
    );
    expect(screen.getByText('当前会话已有任务正在执行')).toBeInTheDocument();
  });

  it('已保存且未过期的凭据无需重新填写', () => {
    expect(fieldNeedsValue(secretField({ masked: '****abcd' }))).toBe(false);
    expect(fieldNeedsValue(secretField({ masked: '****abcd', reason: 'expired' }))).toBe(true);
    expect(fieldNeedsValue(secretField({ required: false }))).toBe(false);
  });
});
