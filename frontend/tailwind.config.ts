/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        navy: { DEFAULT: '#1e3a5f', dark: '#152c47', light: '#2d5075' },
        steel: { DEFAULT: '#4a7ba6', light: '#6b9cc3' },
        canvas: '#f4f6f9',
        card: '#ffffff',
        border: '#dde3ea',
        ink: '#1f2937',
        slate2: '#6b7280',
        accent: '#0d9488',
        alert: '#dc2626',
        warn: '#d97706',
        ok: '#059669',
      },
    },
  },
  plugins: [],
}
