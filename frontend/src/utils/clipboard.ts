/**
 * 剪贴板工具：优先 Clipboard API，不可用/失败时降级为隐藏 textarea + execCommand。
 *
 * 原实现内联在 `components/markdown/CodeBlock.tsx`，消息操作条也要复制 Markdown 原文，
 * 故抽出为单一实现（含降级路径），避免两处各写一份导致行为漂移。
 */

/** 降级方案：部分环境（非安全上下文 / 旧内核）没有 `navigator.clipboard`。 */
function fallbackCopy(text: string): boolean {
  if (typeof document.execCommand !== 'function') {
    console.error('复制失败：当前环境不支持剪贴板写入');
    return false;
  }
  const area = document.createElement('textarea');
  area.value = text;
  area.setAttribute('readonly', 'true');
  area.style.position = 'fixed';
  area.style.opacity = '0';
  document.body.appendChild(area);
  area.select();
  const ok = document.execCommand('copy');
  document.body.removeChild(area);
  return ok;
}

/**
 * 复制文本到剪贴板。
 * 返回是否成功；内部吞掉异常（仅 `console.error`），调用方无需 try/catch。
 */
export function copyText(text: string): Promise<boolean> {
  if (navigator.clipboard?.writeText) {
    return navigator.clipboard.writeText(text).then(
      () => true,
      (error: unknown) => {
        console.error('复制失败，降级为 execCommand', error);
        return fallbackCopy(text);
      },
    );
  }
  return Promise.resolve(fallbackCopy(text));
}
