import { defineConfig, devices } from '@playwright/test'

/**
 * Playwright E2E 测试配置
 * ========================
 * 覆盖 Agent-Native ERP 核心业务流程的端到端测试
 *
 * 运行方式:
 *   npx playwright test          # 无头模式运行
 *   npm run e2e:headed           # 有头模式运行
 *   npm run e2e:ui               # UI 模式运行（交互式调试）
 */

const isCI = !!process.env.CI

export default defineConfig({
  // 测试目录
  testDir: './e2e',

  // 并行执行（CI 中单线程避免资源竞争）
  fullyParallel: !isCI,
  workers: isCI ? 1 : undefined,

  // 禁止 .only（CI 中强制）
  forbidOnly: isCI,

  // 重试次数
  retries: 2,

  // 超时设置
  timeout: 30_000,
  expect: {
    timeout: 10_000,
  },

  // 报告器
  reporter: isCI
    ? [['html', { open: 'never' }], ['list']]
    : [['html', { open: 'never' }], ['list']],

  // 全局配置
  use: {
    // 基础 URL（Vite dev server）
    baseURL: 'http://localhost:5173',

    // 浏览器语言与时区（中文界面）
    locale: 'zh-CN',
    timezone: 'Asia/Shanghai',

    // 截图：仅失败时
    screenshot: 'only-on-failure',

    // 视频：仅失败时保留
    video: 'retain-on-failure',

    // 跟踪：首次重试时记录
    trace: 'on-first-retry',

    // 导航超时
    navigationTimeout: 15_000,
  },

  // 浏览器项目
  projects: [
    {
      name: 'chromium',
      use: {
        ...devices['Desktop Chrome'],
        // 视口大小
        viewport: { width: 1440, height: 900 },
      },
    },
  ],

  // webServer 配置（可选）
  // ================================
  // 本地开发时自动启动 Vite dev server（需后端单独启动）。
  // CI 环境中服务在 workflow 中单独启动（见 .github/workflows/ci.yml）。
  ...(isCI
    ? {}
    : {
        webServer: {
          command: 'npm run dev',
          url: 'http://localhost:5173',
          reuseExistingServer: true,
          timeout: 60_000,
        },
      }),
})
