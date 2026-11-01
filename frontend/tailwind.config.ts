/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // 未来科技浅蓝主色（navy=品牌主蓝，按钮/高亮全局生效）
        navy: { DEFAULT: '#1d4ed8', dark: '#1e3a8a', light: '#3b82f6' },
        steel: { DEFAULT: '#3b82f6', light: '#38bdf8' },
        canvas: '#edf3fc',
        card: '#ffffff',
        border: '#d9e4f5',
        ink: '#0f1f3d',
        slate2: '#5a6b85',
        accent: '#0891b2',
        alert: '#dc2626',
        warn: '#d97706',
        ok: '#059669',
        // 侧边栏深空蓝
        abyss: { DEFAULT: '#081a40', mid: '#0c2660', deep: '#113690' },
      },
      fontFamily: {
        display: ['Bahnschrift', 'Segoe UI', 'Microsoft YaHei', 'sans-serif'],
      },
      boxShadow: {
        soft: '0 1px 2px rgba(16,49,110,.05), 0 8px 24px -12px rgba(29,78,216,.18)',
        lift: '0 2px 4px rgba(16,49,110,.06), 0 12px 32px -12px rgba(29,78,216,.28)',
        glow: '0 4px 16px -4px rgba(56,189,248,.45)',
      },
      backgroundImage: {
        'gradient-brand': 'linear-gradient(135deg, #1d4ed8 0%, #0ea5e9 100%)',
        'gradient-brand-v': 'linear-gradient(180deg, #081a40 0%, #0c2660 55%, #113690 100%)',
        'gradient-pill': 'linear-gradient(90deg, #2563eb 0%, #38bdf8 100%)',
      },
    },
  },
  plugins: [],
}
