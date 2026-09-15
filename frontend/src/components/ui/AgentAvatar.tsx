import { Avatar } from 'antd';
import type { Employee } from '@dwp/shared-schema';

export default function AgentAvatar({
  employee,
  name,
  avatarUrl,
  size = 40,
  className = '',
}: {
  employee?: Employee | null;
  name?: string;
  avatarUrl?: string | null;
  size?: number;
  className?: string;
}) {
  const displayName = employee?.name ?? name ?? '同事';
  const src = employee?.avatar_url ?? avatarUrl ?? undefined;
  return (
    <Avatar
      size={size}
      shape="square"
      src={src ? <img src={src} alt={`${displayName}的卡通头像`} /> : undefined}
      className={`agent-avatar ${className}`.trim()}
    >
      {displayName.slice(0, 1)}
    </Avatar>
  );
}
