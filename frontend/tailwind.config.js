/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        // 深蓝黑背景体系
        bg: {
          DEFAULT: '#0a0e1a',
          card: '#131825',
          hover: '#1a1f2e',
          input: '#0d1220',
          deep: '#060912',
        },
        // 主色 - 科技蓝
        primary: {
          DEFAULT: '#4f7cff',
          hover: '#3a66ff',
          light: '#6b94ff',
          dark: '#2d4dd9',
        },
        // 强调色 - 青绿
        accent: {
          DEFAULT: '#00d9a3',
          hover: '#00b88a',
          light: '#33e8b8',
        },
        // 状态色
        status: {
          running: '#22c55e',
          paused: '#eab308',
          circuit: '#ef4444',
          failed: '#ef4444',
          idle: '#64748b',
        },
      },
      borderRadius: {
        card: '12px',
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', '-apple-system', 'PingFang SC', 'Microsoft YaHei', 'sans-serif'],
        mono: ['JetBrains Mono', 'Fira Code', 'Consolas', 'monospace'],
      },
      boxShadow: {
        card: '0 4px 24px -8px rgba(0, 0, 0, 0.5)',
        glow: '0 0 20px rgba(79, 124, 255, 0.25)',
        'glow-accent': '0 0 20px rgba(0, 217, 163, 0.25)',
        'glow-danger': '0 0 20px rgba(239, 68, 68, 0.25)',
      },
      animation: {
        'pulse-slow': 'pulse 3s cubic-bezier(0.4, 0, 0.6, 1) infinite',
        'fade-in': 'fadeIn 0.3s ease-in-out',
        'slide-in': 'slideIn 0.3s ease-out',
        'glow-pulse': 'glowPulse 2s ease-in-out infinite',
      },
      keyframes: {
        fadeIn: {
          '0%': { opacity: '0' },
          '100%': { opacity: '1' },
        },
        slideIn: {
          '0%': { transform: 'translateX(-10px)', opacity: '0' },
          '100%': { transform: 'translateX(0)', opacity: '1' },
        },
        glowPulse: {
          '0%, 100%': { boxShadow: '0 0 8px rgba(79, 124, 255, 0.3)' },
          '50%': { boxShadow: '0 0 20px rgba(79, 124, 255, 0.6)' },
        },
      },
      backgroundImage: {
        'grid-pattern':
          'linear-gradient(rgba(79,124,255,0.04) 1px, transparent 1px), linear-gradient(90deg, rgba(79,124,255,0.04) 1px, transparent 1px)',
        'gradient-primary': 'linear-gradient(135deg, #4f7cff 0%, #2d4dd9 100%)',
        'gradient-accent': 'linear-gradient(135deg, #00d9a3 0%, #00b88a 100%)',
      },
    },
  },
  plugins: [],
}
