import { Avatar } from 'antd';
import type { ReactNode } from 'react';

/** 统一头像尺寸：列表 36 / 聊天 32 / 详情 56。 */
export type AvatarSize = 'list' | 'chat' | 'detail';

const SIZE_MAP: Record<AvatarSize, number> = {
  list: 36,
  chat: 32,
  detail: 56,
};

export default function UnifiedAvatar({
  size = 'list',
  label,
  background,
  color,
  icon,
}: {
  size?: AvatarSize;
  label?: string;
  background?: string;
  color?: string;
  icon?: ReactNode;
}) {
  return (
    <Avatar
      size={SIZE_MAP[size]}
      style={{ background: background ?? 'var(--dwp-brand-soft)', color: color ?? 'var(--dwp-brand)' }}
      icon={icon}
    >
      {icon ? null : label}
    </Avatar>
  );
}
