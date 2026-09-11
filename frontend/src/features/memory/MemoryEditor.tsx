import { Input, Modal, message } from 'antd';
import { useEffect, useState } from 'react';
import { api } from '../../api/client';
import type { MemoryItem } from '../../api/client';

/** 记忆编辑器：编辑内容并保存（PUT）。 */
export default function MemoryEditor({
  agentId,
  memory,
  open,
  onClose,
  onSaved,
}: {
  agentId: string;
  memory: MemoryItem | null;
  open: boolean;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [content, setContent] = useState('');
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (open && memory) setContent(memory.content);
  }, [open, memory]);

  const save = async () => {
    if (!memory || !content.trim()) return;
    setSaving(true);
    try {
      await api.updateMyMemory(agentId, memory.id, content.trim());
      message.success('记忆已更新');
      onSaved();
      onClose();
    } catch (err) {
      message.error(`保存失败：${(err as Error).message}`);
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal
      title="编辑记忆"
      open={open}
      onCancel={onClose}
      onOk={() => void save()}
      okText="保存"
      confirmLoading={saving}
      okButtonProps={{ disabled: !content.trim() }}
      destroyOnClose
    >
      <Input.TextArea
        rows={6}
        value={content}
        onChange={(e) => setContent(e.target.value)}
        placeholder="编辑记忆内容"
        maxLength={4000}
        showCount
      />
    </Modal>
  );
}
