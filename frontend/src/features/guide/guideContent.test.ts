import { describe, expect, it } from 'vitest';
import { countSteps, filterSections, sectionsFor, stepMatches } from './guideContent';
import type { GuideAudience } from './guideContent';

const AUDIENCES: GuideAudience[] = ['employee', 'admin'];

describe('guideContent', () => {
  it('两侧都有章节，且每章都有标题、摘要与步骤', () => {
    for (const audience of AUDIENCES) {
      const sections = sectionsFor(audience);
      expect(sections.length).toBeGreaterThan(0);
      for (const section of sections) {
        expect(section.audience).toBe(audience);
        expect(section.title.trim()).not.toBe('');
        expect(section.summary.trim()).not.toBe('');
        expect(section.steps.length).toBeGreaterThan(0);
        for (const step of section.steps) {
          expect(step.title.trim()).not.toBe('');
          expect(step.detail.trim()).not.toBe('');
        }
      }
      expect(countSteps(sections)).toBeGreaterThanOrEqual(sections.length);
    }
  });

  it('章节 id 全局唯一，两侧内容互不串味', () => {
    const employeeIds = sectionsFor('employee').map((section) => section.id);
    const adminIds = sectionsFor('admin').map((section) => section.id);
    expect(new Set([...employeeIds, ...adminIds]).size).toBe(employeeIds.length + adminIds.length);
    for (const id of employeeIds) expect(adminIds).not.toContain(id);
  });

  it('文案遵守术语约定，不出现已废弃的旧称呼', () => {
    const text = JSON.stringify([...sectionsFor('employee'), ...sectionsFor('admin')]);
    expect(text).not.toContain('数字员工');
    expect(text).not.toContain('虚拟员工');
  });

  it('搜索按标题、摘要与步骤文案过滤', () => {
    const sections = sectionsFor('employee');
    const hit = filterSections(sections, '停止');
    expect(hit.length).toBeGreaterThan(0);
    expect(hit.length).toBeLessThan(sections.length);
    expect(filterSections(sections, '')).toHaveLength(sections.length);
    expect(filterSections(sections, '   ')).toHaveLength(sections.length);
    expect(filterSections(sections, '不存在的关键词')).toHaveLength(0);
  });

  it('步骤命中判定忽略大小写，且空关键词不命中', () => {
    const step = { title: '接入 MCP 服务', detail: '填写服务地址后点「测试连接并解析」' };
    expect(stepMatches(step, 'mcp')).toBe(true);
    expect(stepMatches(step, 'MCP')).toBe(true);
    expect(stepMatches(step, '测试连接')).toBe(true);
    expect(stepMatches(step, '')).toBe(false);
    expect(stepMatches(step, '知识库')).toBe(false);
  });
});
