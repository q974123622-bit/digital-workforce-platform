import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it } from 'vitest';
import ThemeSwitcher from '../components/ui/ThemeSwitcher';
import { THEME_STORAGE_KEY } from '../theme';
import { ThemeProvider, useTheme } from './ThemeContext';

function ThemeProbe() {
  const { themeId } = useTheme();
  return <output>{themeId}</output>;
}

describe('ThemeProvider', () => {
  beforeEach(() => {
    localStorage.clear();
    document.documentElement.removeAttribute('data-theme');
  });

  it('uses warm-paper by default and safely ignores an invalid stored value', () => {
    localStorage.setItem(THEME_STORAGE_KEY, 'unknown-theme');
    render(<ThemeProvider><ThemeProbe /></ThemeProvider>);
    expect(screen.getByText('warm-paper')).toBeInTheDocument();
    expect(document.documentElement.dataset.theme).toBe('warm-paper');
  });

  it('switches all four palettes and persists the selected theme', () => {
    render(<ThemeProvider><ThemeSwitcher showLabel placement="bottomLeft" /><ThemeProbe /></ThemeProvider>);
    fireEvent.click(screen.getByRole('button', { name: /切换界面色调/ }));
    expect(screen.getAllByRole('radio')).toHaveLength(4);
    fireEvent.click(screen.getByRole('radio', { name: /深海/ }));
    expect(screen.getByText('midnight')).toBeInTheDocument();
    expect(document.documentElement.dataset.theme).toBe('midnight');
    expect(document.documentElement.style.colorScheme).toBe('dark');
    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBe('midnight');
  });
});
