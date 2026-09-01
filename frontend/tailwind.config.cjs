/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ['./index.html', './admin/index.html', './src/**/*.{ts,tsx}'],
  corePlugins: { preflight: false },
  theme: {
    extend: {
      colors: {
        brand: '#165dff',
      },
    },
  },
  plugins: [],
};
