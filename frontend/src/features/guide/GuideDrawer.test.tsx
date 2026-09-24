import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import GuideDrawer from './GuideDrawer';
import type { GuideAudience } from './guideContent';
import { useGuideLauncher } from './useGuideLauncher';

const noop = () => undefined;

function renderDrawer(audience: GuideAudience, props: Partial<Parameters<typeof GuideDrawer>[0]> = {}) {
  return render(
    <MemoryRouter>
      <GuideDrawer
        open
        onClose={noop}
        audience={audience}
        notAutoPrompt={false}
        onNotAutoPromptChange={noop}
        {...props}
      />
    </MemoryRouter>,
  );
}

beforeEach(() => window.localStorage.clear());
afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe('GuideDrawer', () => {
  it('默认展示第一章，点击目录可切换章节', () => {
    renderDrawer('employee');

    // 章节标题同时出现在目录项与内容区标题，所以各出现两次。
    expect(screen.getAllByText('密钥登录与退出')).toHaveLength(2);
    expect(screen.getByText('用访问密钥进入工作台，并按角色自动分流。')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /插件中心与使用申请/ }));

    expect(screen.getAllByText('插件中心与使用申请')).toHaveLength(2);
    expect(screen.getByText('浏览企业插件、启用已授权插件、提交使用申请。')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '去这里' })).toBeInTheDocument();
  });

  it('搜索只保留命中章节，并给命中的步骤加高亮标记', () => {
    renderDrawer('employee');

    fireEvent.change(screen.getByLabelText('搜索章节或步骤'), { target: { value: '停止' } });

    expect(screen.getByRole('button', { name: /协作会话与停止/ })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /密钥登录与退出/ })).toBeNull();
    expect(screen.getByText('停止执行').closest('li')).toHaveClass('is-hit');

    fireEvent.change(screen.getByLabelText('搜索章节或步骤'), { target: { value: '不存在的关键词' } });
    expect(screen.getByText('没有匹配的章节')).toBeInTheDocument();
    expect(screen.getByText('没有匹配的内容，换个关键词试试')).toBeInTheDocument();
  });

  it('按受众过滤内容：管理侧不出现员工侧章节，反之亦然', () => {
    const admin = renderDrawer('admin');
    expect(screen.getByRole('button', { name: /插件治理与授权/ })).toBeInTheDocument();
    expect(screen.queryByText('密钥登录与退出')).toBeNull();
    admin.unmount();

    renderDrawer('employee');
    expect(screen.getByRole('button', { name: /密钥登录与退出/ })).toBeInTheDocument();
    expect(screen.queryByText('插件治理与授权')).toBeNull();
  });

  it('「不再自动提示」开关把勾选结果回传出去', () => {
    const onNotAutoPromptChange = vi.fn();
    renderDrawer('employee', { onNotAutoPromptChange });

    fireEvent.click(screen.getByRole('checkbox', { name: '不再自动提示' }));

    expect(onNotAutoPromptChange).toHaveBeenCalledWith(true);
  });
});

describe('useGuideLauncher', () => {
  function Harness({ audience }: { audience: GuideAudience }) {
    const guide = useGuideLauncher(audience);
    return (
      <div>
        <span data-testid="state">{`${guide.open ? 'open' : 'closed'}:${guide.notAutoPrompt ? 'muted' : 'prompt'}`}</span>
        <button type="button" onClick={guide.openGuide}>手动打开</button>
        <button type="button" onClick={() => guide.setNotAutoPrompt(false)}>重新开启提示</button>
      </div>
    );
  }

  it('首次进入延时自动弹出一次并写入标记', () => {
    vi.useFakeTimers();
    render(<Harness audience="employee" />);

    expect(screen.getByTestId('state')).toHaveTextContent('closed:prompt');

    act(() => { vi.advanceTimersByTime(600); });

    expect(screen.getByTestId('state')).toHaveTextContent('open:muted');
    expect(window.localStorage.getItem('dwp-guide-seen-v1:employee')).toBe('1');
  });

  it('已有标记时不再自动弹出，两侧标记互不影响', () => {
    window.localStorage.setItem('dwp-guide-seen-v1:admin', '1');
    vi.useFakeTimers();
    render(<Harness audience="admin" />);

    act(() => { vi.advanceTimersByTime(600); });

    expect(screen.getByTestId('state')).toHaveTextContent('closed:muted');
    expect(window.localStorage.getItem('dwp-guide-seen-v1:employee')).toBeNull();

    // 取消勾选后清除标记，下次进入会再次自动打开。
    fireEvent.click(screen.getByRole('button', { name: '重新开启提示' }));
    expect(screen.getByTestId('state')).toHaveTextContent('closed:prompt');
    expect(window.localStorage.getItem('dwp-guide-seen-v1:admin')).toBeNull();
  });
});
