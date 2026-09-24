import { useEffect, useRef, useState } from 'react';
import { copyText } from '../../utils/clipboard';

/**
 * 围栏代码块：等宽字体 + 右上角语言标签与复制按钮（悬停显形，默认半透明不抢注意力）。
 * 复制走公共 `utils/clipboard.ts`（Clipboard API → execCommand 降级）。
 */
export default function CodeBlock({ code, lang }: { code: string; lang?: string }) {
  const [copied, setCopied] = useState(false);
  const timer = useRef<number | null>(null);

  useEffect(() => () => {
    if (timer.current !== null) window.clearTimeout(timer.current);
  }, []);

  const flash = () => {
    setCopied(true);
    if (timer.current !== null) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setCopied(false), 1500);
  };

  const handleCopy = () => {
    void copyText(code).then((ok) => {
      if (ok) flash();
    });
  };

  return (
    <div className="md-code">
      <div className="md-code-head">
        <span className="md-code-lang">{lang || 'text'}</span>
        <button type="button" className="md-code-copy" onClick={handleCopy}>
          {copied ? '已复制' : '复制'}
        </button>
      </div>
      <pre>
        <code>{code}</code>
      </pre>
    </div>
  );
}
