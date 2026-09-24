import { QuestionCircleOutlined } from '@ant-design/icons';

export type GuideButtonVariant = 'rail' | 'sider' | 'floating';

/**
 * 操作指南触发器（纯按钮，不含浮层）。
 *
 * 刻意不用 antd Tooltip / Dropdown：左轨贴在视口最左侧，antd 的 *Right 系列定位会把浮层
 * 推到视口外、bottom 系列还会撑高文档引发整页抖动（见 AccountMenu / ThemeSwitcher 注释）。
 * 提示一律走原生 title + aria-label。
 */
export default function GuideButton({
  onOpen,
  variant = 'rail',
  collapsed = false,
  expanded = false,
}: {
  onOpen: () => void;
  /** rail = 工作台 72px 竖轨；sider = 管理后台左栏；floating = 窄屏左下角悬浮。 */
  variant?: GuideButtonVariant;
  /** 管理后台左栏收起态：只留图标。 */
  collapsed?: boolean;
  expanded?: boolean;
}) {
  const shared = {
    type: 'button' as const,
    onClick: onOpen,
    title: '操作指南',
    'aria-label': '操作指南',
    'aria-haspopup': 'dialog' as const,
    'aria-expanded': expanded,
  };

  if (variant === 'floating') {
    return (
      <button {...shared} className="guide-fab">
        <QuestionCircleOutlined />
      </button>
    );
  }

  if (variant === 'sider') {
    return (
      <button {...shared} className={`guide-sider-entry${collapsed ? ' is-collapsed' : ''}`}>
        <QuestionCircleOutlined />
        {!collapsed && <span>操作指南</span>}
      </button>
    );
  }

  return (
    <button {...shared} className="guide-rail-entry">
      <QuestionCircleOutlined />
      <span>指南</span>
    </button>
  );
}
