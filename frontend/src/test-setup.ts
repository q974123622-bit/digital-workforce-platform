import '@testing-library/jest-dom';
import { vi } from 'vitest';

// jsdom 不支持带伪元素的 getComputedStyle，rc-util 测量滚动条时会报错；提供最小实现
const originalGetComputedStyle = window.getComputedStyle.bind(window);
window.getComputedStyle = ((elt: Element, pseudoElt?: string | null) => {
  if (pseudoElt) {
    return { width: '0px', height: '0px', getPropertyValue: () => '' } as unknown as CSSStyleDeclaration;
  }
  return originalGetComputedStyle(elt);
}) as typeof window.getComputedStyle;

Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  }),
});
