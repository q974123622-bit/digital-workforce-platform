/**
 * Markdown 归一化层：修模型输出的常见缺陷，让后续渲染器（自研 builtin 或 remark-gfm）
 * 都能拿到结构完整的 GFM。
 *
 * 修复内容：
 *  - 未闭合的围栏代码块补一个收尾 fence；
 *  - 表格缺 `| --- |` 分隔行时补一行，列数不一致时对齐补齐；
 *  - 表格前后缺空行时补空行（GFM 表格不能打断段落，必须有空行分隔）；
 *  - 围栏**外**的连续空行压到最多 1 个（替代旧的 compactLines），围栏内空行原样保留。
 *
 * 实现约束：全程逐行状态机，禁止使用跨行贪婪/回溯正则（`[\s\S]*?` 一类）。
 */

/** 围栏行：最多 3 个空格缩进 + 3 个以上反引号或波浪线 + 可选信息串。 */
const FENCE_RE = /^ {0,3}(`{3,}|~{3,})(.*)$/;

/** 行内状态：是否位于围栏代码块内部。 */
type Line = { text: string; fence: boolean };

function pipeCount(text: string): number {
  let count = 0;
  for (const ch of text) {
    if (ch === '|') count += 1;
  }
  return count;
}

/** 按未转义的 `|` 切分表格行，去掉首尾空单元。 */
export function splitTableRow(text: string): string[] {
  let body = text.trim();
  if (body.startsWith('|')) body = body.slice(1);
  if (body.endsWith('|')) body = body.slice(0, -1);
  return body.split(/(?<!\\)\|/).map((cell) => cell.replace(/\\\|/g, '|').trim());
}

/** 判断是否为 GFM 表格分隔行，如 `| --- | :--: |`。 */
export function isDelimiterRow(text: string): boolean {
  const trimmed = text.trim();
  if (!trimmed || !trimmed.includes('|') || !trimmed.includes('-')) return false;
  for (const ch of trimmed) {
    if (!'|-: \t'.includes(ch)) return false;
  }
  return true;
}

/** 按列数生成分隔行。 */
export function delimiterRow(columns: number): string {
  return `| ${Array.from({ length: columns }, () => '---').join(' | ')} |`;
}

/** 把一行补齐到指定列数（多出的单元保留，由调用方保证列数取全块最大值）。 */
export function padTableRow(text: string, columns: number): string {
  const cells = splitTableRow(text);
  while (cells.length < columns) cells.push('');
  return `| ${cells.join(' | ').trimEnd()} |`;
}

/** 第一遍：按行扫描并标记围栏内外；未闭合的围栏在末尾补上收尾 fence。 */
function scanLines(input: string): Line[] {
  const raw = input.replace(/\r\n?/g, '\n').split('\n');
  const lines: Line[] = [];
  let marker = '';

  for (const line of raw) {
    const match = FENCE_RE.exec(line);
    if (!marker) {
      // 反引号围栏的信息串里不能再出现反引号，否则按普通文本处理
      const isOpen = match !== null && !(match[1][0] === '`' && match[2].includes('`'));
      if (isOpen && match) {
        marker = match[1][0].repeat(match[1].length);
        lines.push({ text: line, fence: true });
        continue;
      }
      lines.push({ text: line.replace(/[ \t]+$/, ''), fence: false });
      continue;
    }
    const isClose = match !== null && match[1][0] === marker[0] && match[1].length >= marker.length;
    lines.push({ text: line, fence: true });
    if (isClose) marker = '';
  }

  if (marker) lines.push({ text: marker, fence: true });
  return lines;
}

/** 第二遍：表格结构修复（补分隔行、对齐列数、前后补空行）。 */
function repairTables(lines: Line[]): Line[] {
  const out: Line[] = [];
  let index = 0;

  while (index < lines.length) {
    const current = lines[index];
    const next = lines[index + 1];
    const nextIsDelimiter = next !== undefined && !next.fence && isDelimiterRow(next.text);
    const headerPairOk = next !== undefined && !next.fence
      && pipeCount(current.text) >= 2 && pipeCount(next.text) >= 2;

    // 当前行必须自身含竖线才可能是表头，否则按普通文本处理（避免吞掉分隔行前的正文）
    if (current.fence || pipeCount(current.text) === 0 || (!nextIsDelimiter && !headerPairOk)) {
      out.push(current);
      index += 1;
      continue;
    }

    const block: string[] = [];
    let cursor = index;
    while (cursor < lines.length && !lines[cursor].fence && pipeCount(lines[cursor].text) > 0) {
      block.push(lines[cursor].text);
      cursor += 1;
    }

    const hasDelimiter = block.length > 1 && isDelimiterRow(block[1]);
    const dataRows = hasDelimiter ? block.slice(2) : block.slice(1);
    const columns = Math.max(
      1,
      ...block.filter((row) => !isDelimiterRow(row)).map((row) => splitTableRow(row).length),
    );

    const previous = out[out.length - 1];
    if (previous !== undefined && previous.text.trim() !== '') out.push({ text: '', fence: false });

    out.push({ text: padTableRow(block[0], columns), fence: false });
    out.push({ text: delimiterRow(columns), fence: false });
    for (const row of dataRows) out.push({ text: padTableRow(row, columns), fence: false });

    const following = lines[cursor];
    if (following !== undefined && !following.fence && following.text.trim() !== '') {
      out.push({ text: '', fence: false });
    }
    index = cursor;
  }

  return out;
}

/** 第三遍：围栏外连续空行压到最多 1 个，并去掉首尾空行。 */
function collapseBlankLines(lines: Line[]): string {
  const out: string[] = [];
  let blanks = 0;

  for (const line of lines) {
    if (line.fence) {
      out.push(line.text);
      continue;
    }
    if (line.text.trim() === '') {
      blanks += 1;
      if (blanks > 1) continue;
      out.push('');
      continue;
    }
    blanks = 0;
    out.push(line.text);
  }

  while (out.length > 0 && out[0] === '') out.shift();
  while (out.length > 0 && out[out.length - 1] === '') out.pop();
  return out.join('\n');
}

/** 归一化模型输出的 Markdown；空输入原样返回。 */
export function normalizeMarkdown(input: string): string {
  if (!input || !input.trim()) return input;
  return collapseBlankLines(repairTables(scanLines(input)));
}
