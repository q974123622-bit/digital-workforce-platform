import { LogoutOutlined } from '@ant-design/icons';
import { Avatar } from 'antd';
import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { useAuth } from '../../context/AuthContext';

const GAP = 8;
const MARGIN = 8;

/**
 * 左下角头像菜单。
 *
 * 不用 antd Dropdown：头像贴在视口最左侧（x ≈ 14~58px），Dropdown 的 *Right 系列定位
 * 会把浮层推到视口外（left 为负）导致「点了没反应」；bottom 系列还可能撑高文档、
 * 出现/消失滚动条，从而引发整页抖动。
 *
 * 这里沿用 ThemeSwitcher 的成熟做法：portal + 手工定位 + 视口内夹取 + position:fixed，
 * 既不会被 body{overflow:hidden} 裁剪，也不会撑出滚动条。
 */
export default function AccountMenu() {
  const { account, logout } = useAuth();
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState({ top: 0, left: 0 });
  const triggerRef = useRef<HTMLButtonElement>(null);
  const popupRef = useRef<HTMLDivElement>(null);

  // 定位：开在头像右侧、底边对齐，并强制落在视口内
  useLayoutEffect(() => {
    if (!open || !triggerRef.current || !popupRef.current) return;
    const trigger = triggerRef.current.getBoundingClientRect();
    const { offsetWidth: width, offsetHeight: height } = popupRef.current;
    const left = Math.max(MARGIN, Math.min(trigger.right + GAP, window.innerWidth - width - MARGIN));
    const top = Math.max(MARGIN, Math.min(trigger.bottom - height, window.innerHeight - height - MARGIN));
    setPos({ top, left });
  }, [open]);

  // 关闭：点击外部 / Esc / 窗口尺寸变化 / 滚动
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent | TouchEvent) => {
      const target = e.target as Node;
      if (triggerRef.current?.contains(target)) return;
      if (popupRef.current?.contains(target)) return;
      setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false);
    };
    const onClose = () => setOpen(false);
    document.addEventListener('mousedown', onDown);
    document.addEventListener('touchstart', onDown);
    document.addEventListener('keydown', onKey);
    window.addEventListener('resize', onClose);
    window.addEventListener('scroll', onClose, true);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('touchstart', onDown);
      document.removeEventListener('keydown', onKey);
      window.removeEventListener('resize', onClose);
      window.removeEventListener('scroll', onClose, true);
    };
  }, [open]);

  if (!account) return null;

  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        className="grid h-11 w-11 cursor-pointer place-items-center rounded-control border-0 bg-transparent transition-colors hover:bg-surface-subtle"
        aria-label="账号菜单"
        aria-haspopup="menu"
        aria-expanded={open}
        title="账号菜单"
        onClick={() => setOpen((v) => !v)}
      >
        <Avatar
          size={32}
          src={account.avatar_url ?? undefined}
          style={{ background: 'var(--dwp-brand)' }}
        >
          {account.name.slice(0, 1)}
        </Avatar>
      </button>
      {createPortal(
        open ? (
          <div
            ref={popupRef}
            role="menu"
            className="w-[188px] overflow-hidden rounded-container border border-line bg-surface p-1 shadow-lg"
            style={{ position: 'fixed', top: pos.top, left: pos.left, zIndex: 2000 }}
            onClick={(e) => e.stopPropagation()}
          >
            <div className="px-3 py-2">
              <div className="truncate text-sm font-medium text-ink">{account.name}</div>
              <div className="truncate text-xs text-ink-secondary">
                {account.department} · {account.employee_no}
              </div>
            </div>
            <div className="my-1 h-px bg-line-light" />
            <button
              type="button"
              role="menuitem"
              className="flex w-full cursor-pointer items-center gap-2 rounded-control border-0 bg-transparent px-3 py-2 text-left text-sm text-ink transition-colors hover:bg-surface-subtle"
              onClick={() => {
                setOpen(false);
                void logout();
              }}
            >
              <LogoutOutlined />
              退出登录
            </button>
          </div>
        ) : null,
        document.body,
      )}
    </>
  );
}
