import { useCallback, useEffect, useState } from 'react';
import type { GuideAudience } from './guideContent';

/** 两侧分别记忆：员工工作台与管理后台各只自动弹出一次。 */
const SEEN_KEY_PREFIX = 'dwp-guide-seen-v1:';
/** 延时打开，避免与首屏接口请求抢渲染（也让页面先画出来再滑出抽屉）。 */
const AUTO_OPEN_DELAY_MS = 500;

const seenKey = (audience: GuideAudience) => `${SEEN_KEY_PREFIX}${audience}`;

function readSeen(audience: GuideAudience): boolean {
  try {
    return window.localStorage.getItem(seenKey(audience)) === '1';
  } catch {
    // 隐私模式 / 禁用本地存储：当作「已提示过」，不再自动弹，也不阻断页面。
    return true;
  }
}

function writeSeen(audience: GuideAudience, seen: boolean) {
  try {
    if (seen) window.localStorage.setItem(seenKey(audience), '1');
    else window.localStorage.removeItem(seenKey(audience));
  } catch {
    // 同上：本地存储不可用时静默降级。
  }
}

export interface GuideLauncher {
  open: boolean;
  openGuide: () => void;
  closeGuide: () => void;
  /** 当前是否已勾选「不再自动提示」。 */
  notAutoPrompt: boolean;
  setNotAutoPrompt: (value: boolean) => void;
}

/**
 * 指南入口的状态机：抽屉开关 + 首次自动弹出 + 「不再自动提示」标记。
 *
 * 员工工作台有竖轨与窄屏悬浮两个触发器、管理后台有展开与收起两种形态，
 * 它们必须共享同一份 open 状态，所以把状态提到这里，避免在两个 Layout 里复制粘贴。
 */
export function useGuideLauncher(audience: GuideAudience): GuideLauncher {
  const [open, setOpen] = useState(false);
  const [notAutoPrompt, setNotAutoPromptState] = useState(() => readSeen(audience));

  useEffect(() => {
    if (readSeen(audience)) return;
    const timer = window.setTimeout(() => {
      setOpen(true);
      // 打开即写标记：刷新页面不会反复弹出，勾选框状态也与实际行为一致。
      writeSeen(audience, true);
      setNotAutoPromptState(true);
    }, AUTO_OPEN_DELAY_MS);
    return () => window.clearTimeout(timer);
  }, [audience]);

  const openGuide = useCallback(() => setOpen(true), []);
  const closeGuide = useCallback(() => setOpen(false), []);
  const setNotAutoPrompt = useCallback((value: boolean) => {
    setNotAutoPromptState(value);
    writeSeen(audience, value);
  }, [audience]);

  return { open, openGuide, closeGuide, notAutoPrompt, setNotAutoPrompt };
}
