/**
 * 操作指南的数据契约。
 *
 * 拆成独立文件是为了让两套章节数据（员工侧 / 管理侧）只依赖类型、互不引用，
 * 避免「内容文件 import 聚合文件、聚合文件又 import 内容文件」的循环依赖。
 */

/** 指南受众：员工工作台 / 管理后台。两侧内容按角色区分，互不串味。 */
export type GuideAudience = 'employee' | 'admin';

export interface GuideStep {
  /** 步骤标题，一句话说明「做什么」。 */
  title: string;
  /** 操作说明，只描述界面里真实存在的按钮与流程。 */
  detail: string;
  /** 常见坑或注意事项，可选。 */
  tips?: string;
}

export interface GuideSection {
  id: string;
  audience: GuideAudience;
  title: string;
  /** 一句话摘要，显示在内容区标题下方与搜索命中说明里。 */
  summary: string;
  steps: GuideStep[];
  /** 该章节对应的页面路由（管理端为 basename 内的相对路径），用于「去这里」。 */
  link?: string;
}
