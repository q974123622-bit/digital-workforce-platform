/**
 * 把归一化后的 Markdown 切成三类片段：围栏代码块 / GFM 表格 / 其余正文。
 *
 * 存在的理由：项目当前**不能**离线安装 `remark-gfm`（构建是 `--network=none`，依赖只来自
 * builder 镜像），因此表格必须由自研组件渲染；代码块则自研以便加语言标签与复制按钮。
 * 正文片段仍交给 `react-markdown`（不加插件也能渲染加粗、列表、行内代码、引用）。
 *
 * 输入前提：文本已经过 `normalizeMarkdown`（围栏闭合、表格有分隔行、表格前后有空行），
 * 所以这里的解析只需要单向扫描，不做容错。
 */

import { isDelimiterRow, splitTableRow } from './normalize';

const FENCE_RE = /^ {0,3}(`{3,}|~{3,})(.*)$/;

export type MarkdownBlock =
  | { kind: 'code'; lang: string; code: string }
  | { kind: 'table'; header: string[]; rows: string[][] }
  | { kind: 'text'; text: string };

export function splitBlocks(text: string): MarkdownBlock[] {
  const lines = text.split('\n');
  const blocks: MarkdownBlock[] = [];
  let buffer: string[] = [];
  let index = 0;

  const flushText = () => {
    const chunk = buffer.join('\n').trim();
    if (chunk) blocks.push({ kind: 'text', text: chunk });
    buffer = [];
  };

  while (index < lines.length) {
    const line = lines[index];
    const fence = FENCE_RE.exec(line);

    if (fence) {
      flushText();
      const lang = fence[2].trim().split(/\s+/)[0] ?? '';
      index += 1;
      const body: string[] = [];
      while (index < lines.length && FENCE_RE.exec(lines[index]) === null) {
        body.push(lines[index]);
        index += 1;
      }
      index += 1; // 跳过收尾 fence（normalize 已保证存在）
      blocks.push({ kind: 'code', lang, code: body.join('\n') });
      continue;
    }

    if (line.includes('|') && index + 1 < lines.length && isDelimiterRow(lines[index + 1])) {
      flushText();
      const header = splitTableRow(line);
      index += 2;
      const rows: string[][] = [];
      while (index < lines.length && lines[index].trim() !== '' && lines[index].includes('|')) {
        rows.push(splitTableRow(lines[index]));
        index += 1;
      }
      blocks.push({ kind: 'table', header, rows });
      continue;
    }

    buffer.push(line);
    index += 1;
  }

  flushText();
  return blocks;
}
