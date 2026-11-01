import { type Page } from '@playwright/test'

/**
 * E2E 测试认证辅助工具
 * ====================
 *
 * 提供登录、认证状态保存与恢复功能，
 * 用于在测试间复用登录态，减少重复登录开销。
 */

/** 默认管理员凭据 */
export const ADMIN_USERNAME = 'admin'
export const ADMIN_PASSWORD = 'admin123'

/** Zustand persist 存储在 localStorage 的 key */
const AUTH_STORAGE_KEY = 'erp-auth'

/** 缓存的认证状态（JSON 字符串），用于测试间复用 */
let savedAuthState: string | null = null

/**
 * 检查后端服务是否可用
 * ==================================
 * 通过向 API 发起 GET 请求判断后端是否在线。
 * - 收到任何 HTTP 响应（包括 404/405）说明后端可达
 * - 收到 5xx（如 502 网关错误）说明 Vite 代理无法连接后端
 * - 请求抛出异常说明 Vite dev server 或后端均不可用
 *
 * @returns true 如果后端可用
 */
export async function isBackendAvailable(page: Page): Promise<boolean> {
  try {
    const response = await page.request.get('/api/v1/', {
      timeout: 5_000,
      failOnStatusCode: false,
    })
    // 任何 1xx-4xx 响应都表示后端在线
    // 5xx 通常是代理无法连接后端
    return response.status() > 0 && response.status() < 500
  } catch {
    return false
  }
}

/**
 * 以管理员身份登录
 * ==================
 * 自动填写用户名密码并提交登录表单，
 * 等待页面跳转到 Dashboard。
 *
 * 前置条件：后端服务必须可用
 *
 * @param page - Playwright Page 实例
 */
export async function loginAsAdmin(page: Page): Promise<void> {
  await page.goto('/login')

  // 等待登录表单渲染完成
  await page.getByPlaceholder('请输入用户名').waitFor({ state: 'visible' })

  // 填写凭据
  await page.getByPlaceholder('请输入用户名').fill(ADMIN_USERNAME)
  await page.getByPlaceholder('请输入密码').fill(ADMIN_PASSWORD)

  // 提交登录
  await page.locator('button[type="submit"]').click()

  // 等待跳转到 Dashboard（URL 以 / 结尾）
  await page.waitForURL(/\/$/, { timeout: 15_000 })
}

/**
 * 保存当前页面的认证状态
 * ======================
 * 从 localStorage 读取 Zustand 持久化的认证数据并缓存，
 * 供后续测试通过 restoreAuthState() 恢复使用。
 *
 * 典型用法：
 *   await loginAsAdmin(page)
 *   await saveAuthState(page)  // 保存一次
 *   // 后续测试中：
 *   await restoreAuthState(page)  // 快速恢复，无需重新登录
 *
 * @param page - 已登录的 Page 实例
 */
export async function saveAuthState(page: Page): Promise<void> {
  savedAuthState = await page.evaluate(
    (key) => localStorage.getItem(key),
    AUTH_STORAGE_KEY,
  )
}

/**
 * 恢存认证状态到当前页面
 * ======================
 * 将之前通过 saveAuthState() 保存的认证数据写入 localStorage。
 * 调用后需导航到受保护路由（如 page.goto('/')），
 * Zustand store 会从 localStorage 读取并恢复登录态。
 *
 * @param page - 目标 Page 实例
 * @throws 如果没有已保存的认证状态
 */
export async function restoreAuthState(page: Page): Promise<void> {
  if (!savedAuthState) {
    throw new Error('没有已保存的认证状态，请先调用 saveAuthState()')
  }
  await page.evaluate(
    ({ key, value }) => localStorage.setItem(key, value),
    { key: AUTH_STORAGE_KEY, value: savedAuthState },
  )
}

/**
 * 清除认证状态（模拟登出）
 * ======================
 * 从 localStorage 中移除认证数据，
 * 使后续导航触发 ProtectedRoute 重定向到登录页。
 *
 * @param page - 目标 Page 实例
 */
export async function clearAuthState(page: Page): Promise<void> {
  await page.evaluate(
    (key) => localStorage.removeItem(key),
    AUTH_STORAGE_KEY,
  )
}

/**
 * 设置自定义认证状态（用于模拟特定角色/权限的用户）
 * =================================================
 * 将指定的状态对象序列化后写入 localStorage，
 * 用于测试权限控制等场景。
 *
 * @param page - 目标 Page 实例
 * @param state - 认证状态对象（符合 Zustand persist 格式）
 */
export async function setAuthState(
  page: Page,
  state: Record<string, unknown>,
): Promise<void> {
  await page.evaluate(
    ({ key, value }) => localStorage.setItem(key, value),
    { key: AUTH_STORAGE_KEY, value: JSON.stringify(state) },
  )
}
