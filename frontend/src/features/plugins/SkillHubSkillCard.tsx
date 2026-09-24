import { CheckCircleOutlined } from '@ant-design/icons';
import { Checkbox, Tag, Typography } from 'antd';
import type { SkillHubSkill } from '../../api/client';

const { Text } = Typography;

/** 管理端技能市场里的目录卡片：已导入的置灰且不可勾选。 */
export default function SkillHubSkillCard({
  skill, checked, onToggle,
}: {
  skill: SkillHubSkill;
  checked: boolean;
  onToggle: (slug: string, next: boolean) => void;
}) {
  const imported = Boolean(skill.imported);
  return (
    <article
      className={`plugin-card flex min-h-[172px] flex-col rounded-container p-4 ${imported ? 'bg-surface-subtle' : 'bg-surface'}`}
    >
      <div className="flex items-start gap-3">
        <Checkbox
          checked={checked}
          disabled={imported}
          aria-label={`选择技能 ${skill.display_name || skill.slug}`}
          onChange={(event) => onToggle(skill.slug, event.target.checked)}
        />
        <div className="min-w-0 flex-1">
          <div className="flex items-start justify-between gap-2">
            <Text strong className="block truncate">{skill.display_name || skill.slug}</Text>
            {imported
              ? <Tag color="green" className="!mr-0 shrink-0">已导入</Tag>
              : <Tag className="!mr-0 shrink-0">{skill.version ? `v${skill.version}` : '未标注版本'}</Tag>}
          </div>
          <div className="mt-1.5 flex flex-wrap gap-1">
            {skill.featured && <Tag color="gold" className="!mr-0">精选</Tag>}
            <Tag>{skill.slug}</Tag>
            {skill.category && <Tag color="blue">{skill.category}</Tag>}
            {skill.author && <Tag>{skill.author}</Tag>}
            <Tag>{skill.downloads ?? 0} 次下载</Tag>
            {skill.download_disabled && <Tag color="default">不可下载</Tag>}
          </div>
          <p className="mb-0 mt-3 line-clamp-3 text-xs leading-5 text-ink-secondary">
            {skill.summary || '该技能未提供说明。'}
          </p>
        </div>
      </div>
      <div className="mt-auto flex items-center justify-between gap-3 pt-3">
        <span className="text-xs leading-5 text-ink-tertiary">
          {imported
            ? `已导入平台货架${skill.imported_version ? ` · v${skill.imported_version}` : ''}`
            : '尚未导入，勾选后可批量导入平台货架'}
        </span>
        {imported && <CheckCircleOutlined className="text-[var(--dwp-success)]" />}
      </div>
    </article>
  );
}
