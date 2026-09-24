import { expect, it, vi } from 'vitest';
import { render } from '@testing-library/react';

/**
 * 左下角账号菜单的头像：登录人有头像时显示图片，没有（如管理端账号）回退姓名首字。
 */
const state = vi.hoisted(() => ({
  account: null as Record<string, unknown> | null,
}));

vi.mock('../../context/AuthContext', () => ({
  useAuth: () => ({ account: state.account, logout: vi.fn() }),
}));

import AccountMenu from './AccountMenu';

const base = {
  username: 'E10281',
  employee_no: 'E10281',
  name: '张三',
  department: '架构部',
  employment_type: 'formal',
  roles: ['user'],
  must_change_password: false,
};

it('登录人有头像时显示分身图片，不再显示姓名首字', () => {
  state.account = { ...base, avatar_url: '/avatars/dt-e10281.webp' };
  const { container } = render(<AccountMenu />);

  const image = container.querySelector('img');
  expect(image?.getAttribute('src')).toBe('/avatars/dt-e10281.webp');
});

it('没有头像的账号回退姓名首字', () => {
  state.account = { ...base, avatar_url: null };
  const { container } = render(<AccountMenu />);

  expect(container.querySelector('img')).toBeNull();
  expect(container.textContent).toContain('张');
});

it('账号未加载完成时不渲染', () => {
  state.account = null;
  const { container } = render(<AccountMenu />);
  expect(container.querySelector('button')).toBeNull();
});
