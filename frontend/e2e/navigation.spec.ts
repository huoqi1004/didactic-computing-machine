import { test, expect } from '@playwright/test'
import { loginAsAdmin, isBackendAvailable, clearAuthState } from './helpers/auth'

/**
 * 导航 E2E 测试
 * =============
 * 覆盖侧边栏导航、路由跳转、未登录重定向等场景
 */
test.describe('导航测试', () => {
  test.describe('未登录重定向', () => {
    test('未登录访问 Dashboard 重定向到登录页', async ({ page }) => {
      // 确保没有认证状态
      await page.goto('/login')
      await clearAuthState(page)

      // 尝试访问 Dashboard
      await page.goto('/')

      // 应重定向到登录页
      await expect(page).toHaveURL(/\/login$/)
    })

    test('未登录访问发票管理重定向到登录页', async ({ page }) => {
      await page.goto('/login')
      await clearAuthState(page)

      await page.goto('/invoice')

      await expect(page).toHaveURL(/\/login$/)
    })

    test('未登录访问报销管理重定向到登录页', async ({ page }) => {
      await page.goto('/login')
      await clearAuthState(page)

      await page.goto('/reimbursement')

      await expect(page).toHaveURL(/\/login$/)
    })

    test('未登录访问库存管理重定向到登录页', async ({ page }) => {
      await page.goto('/login')
      await clearAuthState(page)

      await page.goto('/inventory')

      await expect(page).toHaveURL(/\/login$/)
    })
  })

  test.describe('已登录导航', () => {
    test.beforeEach(async ({ page }) => {
      // 登录前置准备
      await page.goto('/login')
      const backendReady = await isBackendAvailable(page)
      test.skip(!backendReady, '后端服务未启动，跳过导航测试')
      await loginAsAdmin(page)
    })

    test('Dashboard 正确加载', async ({ page }) => {
      // 已在 Dashboard（登录后跳转）
      await expect(page).toHaveURL(/\/$/)

      // 侧边栏标题
      await expect(page.getByText('企业数字化大脑').first()).toBeVisible()

      // 顶栏系统信息
      await expect(page.getByText('实时态势')).toBeVisible()
    })

    test('从 Dashboard 导航到发票管理', async ({ page }) => {
      // 点击侧边栏"发票管理"链接
      await page.getByRole('link', { name: '发票管理' }).click()

      // 验证 URL 变化
      await expect(page).toHaveURL(/\/invoice$/)

      // 验证页面标题加载
      await expect(page.getByRole('heading', { name: '发票管理' })).toBeVisible()
    })

    test('从 Dashboard 导航到报销管理', async ({ page }) => {
      await page.getByRole('link', { name: '报销管理' }).click()

      await expect(page).toHaveURL(/\/reimbursement$/)
      await expect(page.getByRole('heading', { name: '报销管理' })).toBeVisible()
    })

    test('从 Dashboard 导航到库存管理', async ({ page }) => {
      await page.getByRole('link', { name: '库存管理' }).click()

      await expect(page).toHaveURL(/\/inventory$/)
    })

    test('从 Dashboard 导航到会计凭证', async ({ page }) => {
      await page.getByRole('link', { name: '会计凭证' }).click()

      await expect(page).toHaveURL(/\/accounting$/)
    })

    test('从 Dashboard 导航到报税管理', async ({ page }) => {
      await page.getByRole('link', { name: '报税管理' }).click()

      await expect(page).toHaveURL(/\/tax$/)
    })

    test('从 Dashboard 导航到员工管理', async ({ page }) => {
      await page.getByRole('link', { name: '员工管理' }).click()

      await expect(page).toHaveURL(/\/employees$/)
    })

    test('从 Dashboard 导航到薪资管理', async ({ page }) => {
      await page.getByRole('link', { name: '薪酬管理' }).click()

      await expect(page).toHaveURL(/\/payroll$/)
    })

    test('从 Dashboard 导航到角色管理', async ({ page }) => {
      await page.getByRole('link', { name: '角色管理' }).click()

      await expect(page).toHaveURL(/\/roles$/)
    })

    test('从 Dashboard 导航到权限管理', async ({ page }) => {
      await page.getByRole('link', { name: '权限管理' }).click()

      await expect(page).toHaveURL(/\/permissions$/)
    })

    test('从发票管理返回 Dashboard', async ({ page }) => {
      // 先导航到发票管理
      await page.getByRole('link', { name: '发票管理' }).click()
      await expect(page).toHaveURL(/\/invoice$/)

      // 点击侧边栏"仪表盘"返回 Dashboard
      await page.getByRole('link', { name: '仪表盘' }).click()
      await expect(page).toHaveURL(/\/$/)
    })

    test('侧边栏菜单包含所有核心分组', async ({ page }) => {
      // 验证侧边栏分组标题存在
      const groups = ['总览', '财务', '人力资源', '运营管理', '系统管理']
      for (const group of groups) {
        await expect(page.getByText(group, { exact: true }).first()).toBeVisible()
      }
    })
  })
})
