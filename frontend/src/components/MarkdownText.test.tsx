import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import MarkdownText from './MarkdownText';
import { normalizeMarkdown } from './markdown/normalize';
import { resolveMarkdownRenderer } from './markdown/variant';

/**
 * 回归背景（2026-09-18）：
 *  - 后端 TONE_RULES 曾禁止一切 Markdown 符号 → 模型把表格用空格平铺成一段、代码挤成一行；
 *  - 前端 MarkdownText 没有 remarkPlugins，且 `components` 只覆盖 p/ul/ol/li/h1-h3，
 *    表格与围栏代码块解析不出来。
 * 这里锁定「表格渲染成 table、围栏块渲染成 pre、段落有间距、行内反引号渲染成 code」四件事。
 */

const TABLE_TEXT = [
  '当前可用的插件如下：',
  '',
  '| 插件名称 | 插件ID | 版本 |',
  '| --- | --- | --- |',
  '| 公网搜索 | internet-search | 1.0.0 |',
].join('\n');

const CODE_TEXT = [
  '画图示例：',
  '',
  '```python',
  'import matplotlib.pyplot as plt',
  '',
  'import numpy as np',
  '```',
].join('\n');

describe('MarkdownText', () => {
  it('渲染 GFM 表格为 table，表头与数据行都可读', () => {
    const { container } = render(<MarkdownText text={TABLE_TEXT} />);
    const table = container.querySelector('table.md-table');
    expect(table).not.toBeNull();
    expect(container.querySelectorAll('thead th')).toHaveLength(3);
    expect(container.querySelectorAll('tbody tr')).toHaveLength(1);
    expect(screen.getByText('插件名称')).toBeInTheDocument();
    expect(screen.getByText('internet-search')).toBeInTheDocument();
    // 表格必须包在横向滚动容器里，否则会撑破气泡
    expect(container.querySelector('.md-table-wrap > table')).not.toBeNull();
  });

  it('表格缺分隔行时自动补齐后仍渲染为 table', () => {
    const { container } = render(<MarkdownText text={'| A | B |\n| 1 | 2 |'} />);
    expect(container.querySelector('table')).not.toBeNull();
    expect(container.querySelectorAll('tbody td')).toHaveLength(2);
  });

  it('围栏代码块渲染为 pre/code，且内部空行原样保留', () => {
    const { container } = render(<MarkdownText text={CODE_TEXT} />);
    const code = container.querySelector('.md-code code');
    expect(code).not.toBeNull();
    expect(code?.textContent).toBe('import matplotlib.pyplot as plt\n\nimport numpy as np');
    expect(container.querySelector('.md-code pre')).not.toBeNull();
    // 语言标注显示在右上角
    expect(screen.getByText('python')).toBeInTheDocument();
  });

  it('模型漏写收尾围栏时仍按代码块渲染，不残留裸 ```', () => {
    const { container } = render(<MarkdownText text={'```python\nprint(1)'} />);
    expect(container.querySelector('.md-code code')?.textContent).toBe('print(1)');
    expect(container.textContent).not.toContain('```');
  });

  it('段落之间不再零间距（旧版内联 margin:0 已移除）', () => {
    const { container } = render(<MarkdownText text={'第一段\n\n第二段'} />);
    const paragraphs = container.querySelectorAll('p');
    expect(paragraphs).toHaveLength(2);
    expect((paragraphs[0] as HTMLElement).style.margin).toBe('');
  });

  it('行内反引号渲染为行内 code', () => {
    const { container } = render(<MarkdownText text={'先执行 `pnpm install` 再启动'} />);
    expect(container.querySelector('code.md-inline-code')?.textContent).toBe('pnpm install');
  });

  it('表格单元格内的行内 Markdown 会被解析（**加粗** / `代码` / 链接）', () => {
    const text = [
      '| 能力类别 | 说明 |',
      '| --- | --- |',
      '| **身份定位** | 使用 `DT-E10281` 身份，详见 [文档](https://example.com) |',
    ].join('\n');
    const { container } = render(<MarkdownText text={text} />);
    const cells = container.querySelectorAll('tbody td');
    expect(cells[0]?.querySelector('strong')?.textContent).toBe('身份定位');
    expect(cells[0]?.textContent).not.toContain('**');
    expect(cells[1]?.querySelector('code.md-inline-code')?.textContent).toBe('DT-E10281');
    expect(cells[1]?.querySelector('a')?.getAttribute('href')).toBe('https://example.com');
    // 单元格内不得出现块级 <p>，否则表格行高被撑开
    expect(cells[0]?.querySelector('p')).toBeNull();
  });

  it('表格单元格内的 <br> 渲染为换行而不是字面量', () => {
    const text = ['| A |', '| --- |', '| 第一行<br>第二行 |'].join('\n');
    const { container } = render(<MarkdownText text={text} />);
    const cell = container.querySelector('tbody td');
    expect(cell?.querySelector('br')).not.toBeNull();
    expect(cell?.textContent).toBe('第一行第二行');
  });

  it('渲染器契约：切换分支后同样满足（分支 A 的 gfm 渲染器复用本断言）', () => {
    const Renderer = resolveMarkdownRenderer();
    const { container } = render(<Renderer text={normalizeMarkdown(`${TABLE_TEXT}\n\n${CODE_TEXT}`)} />);
    expect(container.querySelector('table')).not.toBeNull();
    expect(container.querySelector('.md-code code')).not.toBeNull();
  });
});
