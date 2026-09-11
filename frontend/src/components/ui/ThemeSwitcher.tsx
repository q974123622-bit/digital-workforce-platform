import { BgColorsOutlined, CheckOutlined } from '@ant-design/icons';
import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { useTheme } from '../../context/ThemeContext';

type Placement =
  | 'top' | 'bottom' | 'left' | 'right'
  | 'topLeft' | 'topRight' | 'bottomLeft' | 'bottomRight'
  | 'leftTop' | 'leftBottom' | 'rightTop' | 'rightBottom';

interface Position { top: number; left: number; }

const GAP = 8;
const MARGIN = 8;

function computePosition(triggerRect: DOMRect, popupSize: { width: number; height: number }, placement: Placement): Position {
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  let top = 0;
  let left = 0;

  if (placement.startsWith('right')) {
    left = triggerRect.right + GAP;
    if (placement === 'rightTop') top = triggerRect.top;
    else if (placement === 'rightBottom') top = triggerRect.bottom - popupSize.height;
    else top = triggerRect.top + (triggerRect.height - popupSize.height) / 2;
  } else if (placement.startsWith('left')) {
    left = triggerRect.left - GAP - popupSize.width;
    if (placement === 'leftTop') top = triggerRect.top;
    else if (placement === 'leftBottom') top = triggerRect.bottom - popupSize.height;
    else top = triggerRect.top + (triggerRect.height - popupSize.height) / 2;
  } else if (placement.startsWith('top')) {
    top = triggerRect.top - GAP - popupSize.height;
    if (placement === 'topLeft') left = triggerRect.left;
    else if (placement === 'topRight') left = triggerRect.right - popupSize.width;
    else left = triggerRect.left + (triggerRect.width - popupSize.width) / 2;
  } else {
    // bottom / bottomLeft / bottomRight
    top = triggerRect.bottom + GAP;
    if (placement === 'bottomLeft') left = triggerRect.left;
    else if (placement === 'bottomRight') left = triggerRect.right - popupSize.width;
    else left = triggerRect.left + (triggerRect.width - popupSize.width) / 2;
  }

  // 限制在视口内
  left = Math.max(MARGIN, Math.min(left, vw - popupSize.width - MARGIN));
  top = Math.max(MARGIN, Math.min(top, vh - popupSize.height - MARGIN));
  return { top, left };
}

export default function ThemeSwitcher({ placement = 'rightBottom', showLabel = false }: { placement?: Placement; showLabel?: boolean }) {
  const { themeId, themes, setTheme } = useTheme();
  const current = themes.find((item) => item.id === themeId) ?? themes[0];
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState<Position>({ top: 0, left: 0 });
  const triggerRef = useRef<HTMLButtonElement>(null);
  const popupRef = useRef<HTMLDivElement>(null);

  useLayoutEffect(() => {
    if (!open || !triggerRef.current || !popupRef.current) return;
    const triggerRect = triggerRef.current.getBoundingClientRect();
    const popup = popupRef.current;
    const size = { width: popup.offsetWidth, height: popup.offsetHeight };
    setPos(computePosition(triggerRect, size, placement));
  }, [open, placement]);

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

  const picker = (
    <div className="theme-picker" role="radiogroup" aria-label="界面色调">
      <div className="theme-picker-heading">界面色调</div>
      <div className="theme-picker-grid">
        {themes.map((item) => (
          <button
            type="button"
            role="radio"
            aria-checked={item.id === themeId}
            className={`theme-option ${item.id === themeId ? 'selected' : ''}`}
            key={item.id}
            onClick={() => { setTheme(item.id); setOpen(false); }}
          >
            <span className="theme-swatch" aria-hidden="true">{item.preview.map((color) => <i key={color} style={{ backgroundColor: color }} />)}</span>
            <span className="theme-option-copy"><b>{item.name}</b><small>{item.description}</small></span>
            {item.id === themeId && <CheckOutlined className="theme-option-check" />}
          </button>
        ))}
      </div>
    </div>
  );

  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        className={`theme-trigger ${showLabel ? 'with-label' : ''}`}
        aria-label={`切换界面色调，当前为${current.name}`}
        aria-haspopup="true"
        aria-expanded={open}
        title={`界面色调 · ${current.name}`}
        onClick={() => setOpen((v) => !v)}
      >
        <BgColorsOutlined />
        {showLabel && <span>界面色调 · {current.name}</span>}
      </button>
      {createPortal(
        open ? (
          <div
            ref={popupRef}
            className="theme-popover"
            role="dialog"
            style={{ position: 'fixed', top: pos.top, left: pos.left, zIndex: 2000 }}
            onClick={(e) => e.stopPropagation()}
          >
            {picker}
          </div>
        ) : null,
        document.body,
      )}
    </>
  );
}
