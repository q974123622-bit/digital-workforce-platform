import { SendOutlined } from '@ant-design/icons';
import { Button, Input } from 'antd';
import { useState } from 'react';
import type { KeyboardEvent } from 'react';

/**
 * 多行输入区：Enter 发送、Shift+Enter 换行，正确处理中文输入法组合态。
 * 受控组件，草稿按会话持久化由调用方负责（见 onSend 与 value/onChange）。
 */
export default function Composer({
  value,
  onChange,
  onSend,
  disabled = false,
  sending = false,
  placeholder = '发消息…',
}: {
  value: string;
  onChange: (value: string) => void;
  onSend: () => void;
  disabled?: boolean;
  sending?: boolean;
  placeholder?: string;
}) {
  const [composing, setComposing] = useState(false);

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    // 中文输入法组合态（拼音候选）下按 Enter 仅确认候选词，不触发发送。
    if (e.key === 'Enter' && !e.shiftKey && !composing && !e.nativeEvent.isComposing) {
      e.preventDefault();
      onSend();
    }
  };

  return (
    <div className="composer-box mx-auto w-full max-w-[960px] rounded-[14px] border border-line bg-surface p-2 transition-shadow focus-within:border-brand">
      <Input.TextArea
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={handleKeyDown}
        onCompositionStart={() => setComposing(true)}
        onCompositionEnd={() => setComposing(false)}
        placeholder={placeholder}
        autoSize={{ minRows: 1, maxRows: 4 }}
        disabled={disabled}
        variant="borderless"
        className="w-full resize-none !px-2 !py-1.5"
      />
      <div className="mt-1 flex items-center justify-between pl-2">
        <span className="text-[11px] text-ink-tertiary">Enter 发送 · Shift + Enter 换行</span>
        <Button
          type="primary"
          shape="circle"
          icon={<SendOutlined />}
          onClick={onSend}
          loading={sending}
          disabled={disabled || !value.trim()}
          aria-label="发送消息"
        />
      </div>
    </div>
  );
}
