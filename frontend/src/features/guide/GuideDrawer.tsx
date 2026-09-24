import { SearchOutlined } from '@ant-design/icons';
import { Checkbox, Drawer, Empty, Input } from 'antd';
import { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { countSteps, filterSections, sectionsFor, stepMatches } from './guideContent';
import type { GuideAudience } from './guideContent';

/**
 * 右侧抽屉式操作指南：左侧章节目录 + 右侧步骤卡片，可反复查阅。
 *
 * 不用 `destroyOnHidden`：内容量很小（只渲染当前章节），保留挂载状态才能实现
 * 「关闭后再次打开仍停在上次看的章节」。首次打开前 antd 不会渲染内容，没有额外开销。
 */
export default function GuideDrawer({
  open,
  onClose,
  audience,
  notAutoPrompt,
  onNotAutoPromptChange,
}: {
  open: boolean;
  onClose: () => void;
  audience: GuideAudience;
  notAutoPrompt: boolean;
  onNotAutoPromptChange: (value: boolean) => void;
}) {
  const navigate = useNavigate();
  const sections = useMemo(() => sectionsFor(audience), [audience]);
  const [keyword, setKeyword] = useState('');
  const [activeId, setActiveId] = useState(() => sections[0]?.id ?? '');

  const filtered = useMemo(() => filterSections(sections, keyword), [sections, keyword]);
  // 命中过滤后若原章节被筛掉，自动回落到第一条命中章节（无需额外 effect）。
  const active = filtered.find((section) => section.id === activeId) ?? filtered[0];

  return (
    <Drawer
      className="guide-drawer"
      width={720}
      open={open}
      onClose={onClose}
      title={
        <div className="guide-head">
          <h2 className="guide-head-title" id="guide-drawer-title">操作指南</h2>
          <span className="guide-head-sub">按角色整理的操作步骤，可随时回来查阅</span>
        </div>
      }
      extra={
        <Input
          allowClear
          aria-label="搜索章节或步骤"
          placeholder="搜索章节或步骤"
          prefix={<SearchOutlined className="text-ink-tertiary" />}
          value={keyword}
          onChange={(event) => setKeyword(event.target.value)}
          className="guide-search"
        />
      }
      footer={
        <div className="guide-foot">
          <Checkbox
            checked={notAutoPrompt}
            title="取消勾选后，下次进入会自动打开这份指南"
            onChange={(event) => onNotAutoPromptChange(event.target.checked)}
          >
            不再自动提示
          </Checkbox>
          <span className="guide-foot-stats">
            共 {sections.length} 章 · {countSteps(sections)} 步
          </span>
        </div>
      }
    >
      <div className="guide-body">
        <nav className="guide-nav" aria-label="指南章节">
          {filtered.map((section) => {
            const isActive = section.id === active?.id;
            return (
              <button
                key={section.id}
                type="button"
                className={`guide-nav-item${isActive ? ' is-active' : ''}`}
                aria-current={isActive ? 'true' : undefined}
                onClick={() => setActiveId(section.id)}
              >
                <span className="guide-nav-label">{section.title}</span>
                <span className="guide-nav-count">{section.steps.length}</span>
              </button>
            );
          })}
          {!filtered.length && <p className="guide-nav-empty">没有匹配的章节</p>}
        </nav>

        <section className="guide-content" aria-labelledby="guide-drawer-title">
          {active ? (
            <article key={active.id} className="guide-article">
              <header className="guide-article-head">
                <h3>{active.title}</h3>
                {active.link && (
                  <button
                    type="button"
                    className="guide-goto"
                    onClick={() => { onClose(); navigate(active.link as string); }}
                  >
                    去这里
                  </button>
                )}
              </header>
              <p className="guide-article-summary">{active.summary}</p>
              <ol className="guide-steps">
                {active.steps.map((step, index) => (
                  <li key={step.title} className={`guide-step${stepMatches(step, keyword) ? ' is-hit' : ''}`}>
                    <span className="guide-step-index" aria-hidden="true">{index + 1}</span>
                    <div className="guide-step-body">
                      <h4>{step.title}</h4>
                      <p>{step.detail}</p>
                      {step.tips && <p className="guide-step-tips">{step.tips}</p>}
                    </div>
                  </li>
                ))}
              </ol>
            </article>
          ) : (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="没有匹配的内容，换个关键词试试" />
          )}
        </section>
      </div>
    </Drawer>
  );
}
