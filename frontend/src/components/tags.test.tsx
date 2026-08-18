import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { DecisionTag, LevelTag, StatusBadge, TypeTag } from './tags';

describe('tags', () => {
  it('渲染员工类型标签', () => {
    render(<TypeTag value="twin" />);
    expect(screen.getByText('数字分身')).toBeInTheDocument();
  });

  it('渲染决策标签', () => {
    render(<DecisionTag value="deny" />);
    expect(screen.getByText('拒绝')).toBeInTheDocument();
  });

  it('渲染数据等级标签', () => {
    render(<LevelTag value="L3" />);
    expect(screen.getByText('L3 高敏')).toBeInTheDocument();
  });

  it('渲染状态徽标', () => {
    render(<StatusBadge value="active" />);
    expect(screen.getByText('启用')).toBeInTheDocument();
  });
});
