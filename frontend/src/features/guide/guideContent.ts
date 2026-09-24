import { GUIDE_ADMIN_SECTIONS } from './guideAdmin';
import { GUIDE_EMPLOYEE_SECTIONS } from './guideEmployee';
import type { GuideAudience, GuideSection, GuideStep } from './guideTypes';

export type { GuideAudience, GuideSection, GuideStep } from './guideTypes';

/** 章节数据是模块级常量：同一份内容支撑两侧入口，按 audience 过滤后互不串味。 */
const SECTIONS: Record<GuideAudience, GuideSection[]> = {
  employee: GUIDE_EMPLOYEE_SECTIONS,
  admin: GUIDE_ADMIN_SECTIONS,
};

export function sectionsFor(audience: GuideAudience): GuideSection[] {
  return SECTIONS[audience] ?? [];
}

/** 统一关键词口径：去首尾空格 + 忽略大小写（英文标识符如 MCP / Skill 也能小写命中）。 */
const normalize = (value: string) => value.trim().toLowerCase();

/** 步骤是否命中关键词；空关键词一律视为不命中（避免整篇都被标成高亮）。 */
export function stepMatches(step: GuideStep, keyword: string): boolean {
  const needle = normalize(keyword);
  if (!needle) return false;
  return `${step.title} ${step.detail} ${step.tips ?? ''}`.toLowerCase().includes(needle);
}

/** 章节是否命中关键词（标题、摘要或任一步骤文案）。 */
export function sectionMatches(section: GuideSection, keyword: string): boolean {
  const needle = normalize(keyword);
  if (!needle) return true;
  if (`${section.title} ${section.summary}`.toLowerCase().includes(needle)) return true;
  return section.steps.some((step) => stepMatches(step, needle));
}

/** 过滤章节；关键词为空时原样返回，保证目录顺序稳定。 */
export function filterSections(sections: GuideSection[], keyword: string): GuideSection[] {
  const needle = normalize(keyword);
  if (!needle) return sections;
  return sections.filter((section) => sectionMatches(section, needle));
}

/** 章节与步骤总数，用于抽屉底部的统计文案。 */
export function countSteps(sections: GuideSection[]): number {
  return sections.reduce((total, section) => total + section.steps.length, 0);
}
