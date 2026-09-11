/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ['./index.html', './admin/index.html', './src/**/*.{ts,tsx}'],
  corePlugins: { preflight: false },
  theme: {
    extend: {
      colors: {
        brand: 'var(--dwp-brand)',
        'brand-hover': 'var(--dwp-brand-hover)',
        'brand-soft': 'var(--dwp-brand-soft)',
        surface: 'var(--dwp-surface)',
        'surface-subtle': 'var(--dwp-surface-subtle)',
        page: 'var(--dwp-page-bg)',
        ink: 'var(--dwp-text)',
        'ink-secondary': 'var(--dwp-text-secondary)',
        'ink-tertiary': 'var(--dwp-text-tertiary)',
        line: 'var(--dwp-border)',
        'line-light': 'var(--dwp-border-light)',
        'line-light': 'var(--dwp-border-light)',
        success: 'var(--dwp-success)',
        warning: 'var(--dwp-warning)',
        danger: 'var(--dwp-danger)',
      },
      borderRadius: {
        control: 'var(--dwp-radius-control)',
        container: 'var(--dwp-radius-container)',
        bubble: 'var(--dwp-radius-bubble)',
      },
    },
  },
  plugins: [],
};
