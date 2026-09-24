import { describe, expect, it } from 'vitest';

import { splitBlocks } from './blocks';
import { normalizeMarkdown } from './normalize';

describe('normalizeMarkdown', () => {
  it('未闭合的围栏代码块补上收尾 fence', () => {
    expect(normalizeMarkdown('```python\nprint(1)')).toBe('```python\nprint(1)\n```');
  });

  it('已闭合的围栏不被二次修改', () => {
    const text = '```bash\nls -l\n```';
    expect(normalizeMarkdown(text)).toBe(text);
  });

  it('围栏内的连续空行原样保留（旧 compactLines 会把代码里的空行压掉）', () => {
    const text = '```python\na = 1\n\n\nb = 2\n```';
    expect(normalizeMarkdown(text)).toBe(text);
  });

  it('围栏外的连续空行压到最多 1 个，并去掉首尾空行', () => {
    expect(normalizeMarkdown('\n\n第一段\n\n\n\n第二段\n\n')).toBe('第一段\n\n第二段');
  });

  it('表格缺分隔行时补一行', () => {
    expect(normalizeMarkdown('| A | B |\n| 1 | 2 |')).toBe(
      '| A | B |\n| --- | --- |\n| 1 | 2 |',
    );
  });

  it('表格列数不一致时按最大列数对齐', () => {
    const normalized = normalizeMarkdown('| A | B |\n| --- | --- |\n| 1 | 2 | 3 |');
    expect(normalized.split('\n')).toEqual(['| A | B | |', '| --- | --- | --- |', '| 1 | 2 | 3 |']);
    expect(normalized).toContain('| --- | --- | --- |');
  });

  it('表格前后缺空行时补空行（GFM 表格不能打断段落）', () => {
    const normalized = normalizeMarkdown('看下面这张表\n| A | B |\n| --- | --- |\n| 1 | 2 |\n结尾说明');
    expect(normalized).toBe(
      '看下面这张表\n\n| A | B |\n| --- | --- |\n| 1 | 2 |\n\n结尾说明',
    );
  });

  it('单行竖线的普通文本不会被误判为表格', () => {
    const text = '参数 a | b 表示二选一。\n另一行 c | d 同理。';
    expect(normalizeMarkdown(text)).toBe(text);
  });

  it('空输入原样返回', () => {
    expect(normalizeMarkdown('')).toBe('');
  });
});

describe('splitBlocks', () => {
  it('切出围栏代码块 / 表格 / 正文三类片段', () => {
    const blocks = splitBlocks(normalizeMarkdown(
      '前言\n\n```python\nprint(1)\n```\n\n| A | B |\n| --- | --- |\n| 1 | 2 |',
    ));
    expect(blocks.map((block) => block.kind)).toEqual(['text', 'code', 'table']);
    expect(blocks[1]).toEqual({ kind: 'code', lang: 'python', code: 'print(1)' });
    expect(blocks[2]).toEqual({ kind: 'table', header: ['A', 'B'], rows: [['1', '2']] });
  });

  it('无结构化内容时只有一段正文', () => {
    const blocks = splitBlocks('就是一段普通回复。');
    expect(blocks).toEqual([{ kind: 'text', text: '就是一段普通回复。' }]);
  });
});
