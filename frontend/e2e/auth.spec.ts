import { test, expect } from '@playwright/test'
import {
  loginAsAdmin,
  isBackendAvailable,
  ADMIN_USERNAME,
  ADMIN_PASSWORD,
} from './helpers/auth'

/**
 * 认证流程 E2E 测试
 * =================
 * 覆盖登录页面渲染、表单校验、登录跳转、错误提示、token 持久化等场景
 */
test.describe('认证流程', () => {
  test.describe.configure({ mode: 'parallel' })

  test('登录页正确渲染', async ({ page }) => {
    await page.goto('/login')

    // 标题"企业数字化大脑"
    await expect(page.getByText('企业数字化大脑').first()).toBeVisible()

    // 用户名输入框
    await expect(page.getByPlaceholder('请输入用户名')).toBeVisible()

    // 密码输入框
    await expect(page.getByPlaceholder('请输入密码')).toBeVisible()

    // 登录按钮
    const submitButton = page.locator('button[type="submit"]')
    await expect(submitButton).toBeVisible()
    await expect(submitButton).toHaveText('登录')

    // 默认管理员提示
    await expect(page.getByText('admin / admin123')).toBeVisible()
  })

  test('空表单时登录按钮禁用', async ({ page }) => {
    await page.goto('/login')

    const submitButton = page.locator('button[type="submit"]')

    // 两个输入框都为空 -> 按钮禁用
    await expect(submitButton).toBeDisabled()

    // 仅填写用户名 -> 按钮仍禁用
    await page.getByPlaceholder('请输入用户名').fill('admin')
    await expect(submitButton).toBeDisabled()

    // 清空用户名，仅填写密码 -> 按钮仍禁用
    await page.getByPlaceholder('请输入用户名').clear()
    await page.getByPlaceholder('请输入密码').fill('admin123')
    await expect(submitButton).toBeDisabled()

    // 同时填写用户名和密码 -> 按钮启用
    await page.getByPlaceholder('请输入用户名').fill('admin')
    await expect(submitButton).toBeEnabled()
  })

  test('错误凭据显示错误提示', async ({ page }) => {
    await page.goto('/login')

    // 填写错误凭据
    await page.getByPlaceholder('请输入用户名').fill('wronguser')
    await page.getByPlaceholder('请输入密码').fill('wrongpassword')
    await page.locator('button[type="submit"]').click()

    // 等待错误提示出现
    // 无论后端是否启动，错误信息都会显示：
    // - 后端启动但凭据错误：后端返回的错误消息
    // - 后端未启动：前端 fallback "登录失败，请检查用户名和密码"
    const errorText = page.locator('span.text-status-failed')
    await expect(errorText).toBeVisible({ timeout: 15_000 })
  })

  test('成功登录后跳转到 Dashboard', async ({ page }) => {
    await page.goto('/login')

    // 优雅降级：后端未启动时跳过
    const backendReady = await isBackendAvailable(page)
    test.skip(!backendReady, '后端服务未启动，跳过此测试')

    // 执行登录
    await loginAsAdmin(page)

    // 验证 URL 跳转到根路径（Dashboard）
    await expect(page).toHaveURL(/\/$/)

    // 验证侧边栏已渲染（Layout 加载成功）
    await expect(page.getByText('企业数字化大脑').first()).toBeVisible()

    // 验证顶栏存在（包含"实时态势"）
    await expect(page.getByText('实时态势')).toBeVisible()
  })

  test('登录后 localStorage 中有 token', async ({ page }) => {
    await page.goto('/login')

    // 优雅降级：后端未启动时跳过
    const backendReady = await isBackendAvailable(page)
    test.skip(!backendReady, '后端服务未启动，跳过此测试')

    // 执行登录
    await loginAsAdmin(page)

    // 验证 localStorage 中有认证信息
    const authState = await page.evaluate(() => {
      return localStorage.getItem('erp-auth')
    })
    expect(authState).not.toBeNull()

    // 解析并验证 token 和认证状态
    const parsed = JSON.parse(authState!)
    expect(parsed.state).toBeDefined()
    expect(parsed.state.isAuthenticated).toBe(true)
    expect(parsed.state.token).toBeTruthy()
    expect(parsed.state.token.length).toBeGreaterThan(0)
    expect(parsed.state.user).toBeDefined()
    expect(parsed.state.user.username).toBe(ADMIN_USERNAME)
  })

  test('已登录用户访问登录页自动跳转 Dashboard', async ({ page }) => {
    await page.goto('/login')

    // 优雅降级：后端未启动时跳过
    const backendReady = await isBackendAvailable(page)
    test.skip(!backendReady, '后端服务未启动，跳过此测试')

    // 先登录
    await loginAsAdmin(page)

    // 再次访问登录页
    await page.goto('/login')

    // 应自动跳转回 Dashboard
    await expect(page).toHaveURL(/\/$/)
  })
})
