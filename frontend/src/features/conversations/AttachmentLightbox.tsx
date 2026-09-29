import { CloseOutlined, DownloadOutlined, LeftOutlined, MinusOutlined, PlusOutlined, ReloadOutlined, RightOutlined } from '@ant-design/icons';
import { useCallback, useEffect, useRef, useState } from 'react';
import type { PointerEvent as ReactPointerEvent } from 'react';
import { createPortal } from 'react-dom';
import { attachmentContentUrl } from './attachmentPreview';
import type { PreviewKind } from './attachmentPreview';

export interface LightboxItem {
  id: string;
  filename: string;
  kind: Exclude<PreviewKind, 'none'>;
}

const MIN_SCALE = 1;
const MAX_SCALE = 6;
const ZOOM_STEP = 0.5;
const DOUBLE_CLICK_SCALE = 2.5;

/**
 * 附件全屏预览灯箱。
 *
 * 自研 + `createPortal(document.body)` + `zIndex: 2000`：portal 壳里 antd 的
 * `Image`/`Modal` 浮层定位不可靠（既有教训），一律不用。
 *
 * 交互：**点图片以外的暗色区域关闭**（遮罩 onClick，图片/工具栏/标题栏各自
 * stopPropagation）、Esc 关闭、左右方向键与左右按钮翻页（只在**同一消息内**的
 * 图片集合里翻）、`i / n` 计数、下载原图。
 *
 * 图片额外支持缩放：滚轮缩放、放大/缩小/还原按钮、双击放大（再双击还原）、
 * 放大后按住拖动平移。切换图片时缩放复位。打开时锁 body 滚动、焦点移入，
 * 关闭还原。
 */
export default function AttachmentLightbox({
  conversationId,
  items,
  index,
  onIndexChange,
  onClose,
}: {
  conversationId: string;
  items: LightboxItem[];
  index: number;
  onIndexChange: (next: number) => void;
  onClose: () => void;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const stageRef = useRef<HTMLDivElement>(null);
  const dragRef = useRef<{ x: number; y: number; ox: number; oy: number } | null>(null);
  const [scale, setScale] = useState(MIN_SCALE);
  const [offset, setOffset] = useState({ x: 0, y: 0 });
  const [dragging, setDragging] = useState(false);

  const count = items.length;
  const current = index >= 0 && index < count ? items[index] : null;
  const open = current !== null;
  const zoomable = current?.kind === 'image';

  // 阻止冒泡到遮罩：点这些地方不该关闭（遮罩上的 onClick 才是「点空白关闭」）。
  const stop = (event: { stopPropagation: () => void }) => event.stopPropagation();

  const resetZoom = useCallback(() => {
    setScale(MIN_SCALE);
    setOffset({ x: 0, y: 0 });
  }, []);

  // 键盘：Esc 关闭、左右翻页。翻页在首尾之间循环，只有一张时不响应左右键。
  useEffect(() => {
    if (!open) return;
    const handleKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        onClose();
        return;
      }
      if (count < 2) return;
      if (event.key === 'ArrowLeft') {
        event.preventDefault();
        onIndexChange((index - 1 + count) % count);
      } else if (event.key === 'ArrowRight') {
        event.preventDefault();
        onIndexChange((index + 1) % count);
      }
    };
    window.addEventListener('keydown', handleKey);
    return () => window.removeEventListener('keydown', handleKey);
  }, [open, count, index, onIndexChange, onClose]);

  // 打开时锁 body 滚动并把焦点移进容器，关闭时还原。
  useEffect(() => {
    if (!open) return;
    const previous = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    containerRef.current?.focus();
    return () => {
      document.body.style.overflow = previous;
    };
  }, [open]);

  // 换图时缩放复位，避免带着上一张的放大倍数与位移。
  useEffect(() => {
    resetZoom();
    dragRef.current = null;
    setDragging(false);
  }, [current?.id, resetZoom]);

  // 缩回 1 倍时把位移归零，防止图片「跑出屏幕」找不回来。
  useEffect(() => {
    if (scale <= MIN_SCALE) setOffset({ x: 0, y: 0 });
  }, [scale]);

  // 滚轮缩放：必须用原生非 passive 监听，React 的 onWheel 无法 preventDefault。
  useEffect(() => {
    const node = stageRef.current;
    if (!node || !open || !zoomable) return;
    const handleWheel = (event: WheelEvent) => {
      event.preventDefault();
      const delta = event.deltaY < 0 ? ZOOM_STEP : -ZOOM_STEP;
      setScale((prev) => Math.min(MAX_SCALE, Math.max(MIN_SCALE, Number((prev + delta).toFixed(2)))));
    };
    node.addEventListener('wheel', handleWheel, { passive: false });
    return () => node.removeEventListener('wheel', handleWheel);
  }, [open, zoomable]);

  const applyScale = useCallback((next: number) => {
    setScale(Math.min(MAX_SCALE, Math.max(MIN_SCALE, Number(next.toFixed(2)))));
  }, []);

  const handlePointerDown = (event: ReactPointerEvent<HTMLImageElement>) => {
    stop(event);
    if (scale <= MIN_SCALE) return;
    dragRef.current = { x: event.clientX, y: event.clientY, ox: offset.x, oy: offset.y };
    setDragging(true);
    try {
      event.currentTarget.setPointerCapture?.(event.pointerId);
    } catch {
      /* jsdom / 老浏览器没有指针捕获，拖动仍可用 */
    }
  };

  const handlePointerMove = (event: ReactPointerEvent<HTMLImageElement>) => {
    const start = dragRef.current;
    if (!start) return;
    stop(event);
    setOffset({
      x: start.ox + (event.clientX - start.x),
      y: start.oy + (event.clientY - start.y),
    });
  };

  const handlePointerUp = (event: ReactPointerEvent<HTMLImageElement>) => {
    if (!dragRef.current) return;
    dragRef.current = null;
    setDragging(false);
    try {
      event.currentTarget.releasePointerCapture?.(event.pointerId);
    } catch {
      /* 同上 */
    }
  };

  if (!current) return null;

  const contentUrl = attachmentContentUrl(conversationId, current.id);
  const downloadUrl = attachmentContentUrl(conversationId, current.id, true);
  const zoomed = scale > MIN_SCALE;

  return createPortal(
    <div
      ref={containerRef}
      role="dialog"
      aria-modal="true"
      aria-label={`预览 ${current.filename}`}
      tabIndex={-1}
      className="fixed inset-0 flex flex-col bg-black/75 outline-none backdrop-blur-sm"
      style={{ zIndex: 2000 }}
      onClick={onClose}
    >
      {/* 标题栏：点标题栏空白不关闭，所以整条都拦冒泡。 */}
      <div className="flex items-center gap-3 px-4 py-3 text-white" onClick={stop}>
        <span className="min-w-0 flex-1 truncate text-sm font-medium" title={current.filename}>
          {current.filename}
        </span>
        {count > 1 && (
          <span className="shrink-0 text-xs text-white/70">
            第 {index + 1} / {count} 张
          </span>
        )}
        <a
          href={downloadUrl}
          className="shrink-0 rounded-control border border-white/40 bg-white/10 px-2.5 py-1.5 text-xs text-white transition-colors hover:bg-white/20"
          aria-label="下载原图"
        >
          <DownloadOutlined className="mr-1" />下载原图
        </a>
        <button
          type="button"
          aria-label="关闭预览"
          className="shrink-0 rounded-control bg-white px-3 py-1.5 text-xs font-semibold text-neutral-900 shadow transition-colors hover:bg-neutral-100"
          onClick={onClose}
        >
          <CloseOutlined className="mr-1" />关闭
        </button>
      </div>

      {/* 舞台：容器本身不拦冒泡 —— 点图片以外的暗色区域就落到遮罩上，退出预览。 */}
      <div
        ref={stageRef}
        className="relative flex min-h-0 flex-1 items-center justify-center overflow-hidden px-4"
      >
        {count > 1 && (
          <button
            type="button"
            aria-label="上一张"
            className="absolute left-3 z-10 rounded-full border border-white/30 bg-black/50 p-2 text-white transition-colors hover:bg-black/70"
            onClick={(event) => {
              stop(event);
              onIndexChange((index - 1 + count) % count);
            }}
          >
            <LeftOutlined />
          </button>
        )}
        {zoomable ? (
          <img
            src={contentUrl}
            alt={current.filename}
            draggable={false}
            className={`max-h-full max-w-full object-contain select-none ${zoomed ? (dragging ? 'cursor-grabbing' : 'cursor-grab') : 'cursor-zoom-in'}`}
            style={{
              transform: `translate(${offset.x}px, ${offset.y}px) scale(${scale})`,
              transition: dragging ? 'none' : 'transform 120ms ease-out',
            }}
            onClick={stop}
            onDoubleClick={(event) => {
              stop(event);
              if (zoomed) resetZoom();
              else applyScale(DOUBLE_CLICK_SCALE);
            }}
            onPointerDown={handlePointerDown}
            onPointerMove={handlePointerMove}
            onPointerUp={handlePointerUp}
            onPointerCancel={handlePointerUp}
          />
        ) : (
          <iframe
            src={contentUrl}
            title={current.filename}
            className="h-full w-full max-w-[1100px] rounded-container border border-line bg-surface"
          />
        )}
        {count > 1 && (
          <button
            type="button"
            aria-label="下一张"
            className="absolute right-3 z-10 rounded-full border border-white/30 bg-black/50 p-2 text-white transition-colors hover:bg-black/70"
            onClick={(event) => {
              stop(event);
              onIndexChange((index + 1) % count);
            }}
          >
            <RightOutlined />
          </button>
        )}
      </div>

      {zoomable && (
        <div className="flex items-center justify-center gap-2 pb-3 pt-1 text-white" onClick={stop}>
          <button
            type="button"
            aria-label="缩小"
            disabled={!zoomed}
            className="rounded-control border border-white/40 bg-white/10 px-2.5 py-1.5 text-xs transition-colors hover:bg-white/20 disabled:cursor-not-allowed disabled:opacity-40"
            onClick={() => applyScale(scale - ZOOM_STEP)}
          >
            <MinusOutlined />
          </button>
          <span className="w-14 text-center text-xs tabular-nums text-white/80">{Math.round(scale * 100)}%</span>
          <button
            type="button"
            aria-label="放大"
            disabled={scale >= MAX_SCALE}
            className="rounded-control border border-white/40 bg-white/10 px-2.5 py-1.5 text-xs transition-colors hover:bg-white/20 disabled:cursor-not-allowed disabled:opacity-40"
            onClick={() => applyScale(scale + ZOOM_STEP)}
          >
            <PlusOutlined />
          </button>
          <button
            type="button"
            aria-label="还原缩放"
            disabled={!zoomed}
            className="rounded-control border border-white/40 bg-white/10 px-2.5 py-1.5 text-xs transition-colors hover:bg-white/20 disabled:cursor-not-allowed disabled:opacity-40"
            onClick={resetZoom}
          >
            <ReloadOutlined className="mr-1" />还原
          </button>
          <span className="ml-1 hidden text-[11px] text-white/60 sm:inline">
            滚轮缩放 · 双击放大 · 放大后按住拖动
          </span>
        </div>
      )}

      {current.kind === 'pdf' && (
        /* 信创环境浏览器可能没有内置 PDF 阅读器：常驻两个兜底入口。 */
        <div className="flex flex-wrap items-center justify-center gap-3 px-4 pb-4 text-xs text-white/70" onClick={stop}>
          <span>若浏览器未内置 PDF 阅读器，请用以下入口打开或下载</span>
          <a
            href={contentUrl}
            target="_blank"
            rel="noreferrer"
            className="rounded-control border border-white/40 bg-white/10 px-2.5 py-1.5 text-white transition-colors hover:bg-white/20"
          >
            在新标签页打开
          </a>
          <a
            href={downloadUrl}
            className="rounded-control border border-white/40 bg-white/10 px-2.5 py-1.5 text-white transition-colors hover:bg-white/20"
          >
            下载
          </a>
        </div>
      )}
    </div>,
    document.body,
  );
}
