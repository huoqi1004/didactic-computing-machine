import { test, expect } from '@playwright/test'
import {
  loginAsAdmin,
  isBackendAvailable,
  clearAuthState,
  setAuthState,
} from './helpers/auth'

/**
 * 权限控制 E2E 测试
 * =================
 * 覆盖 ProtectedRoute 重定向、权限不足 403 页面、角色权限 UI 可见性等场景
 */
test.describe('权限控制测试', () => {
  // ================================================================
  // 未认证场景（不需要后端）
  // ================================================================
  test.describe('未认证重定向', () => {
    test.beforeEach(async ({ page }) => {
      // 确保没有认证状态
      await page.goto('/login')
      await clearAuthState(page)
    })

    test('未登录访问 Dashboard 重定向到登录页', async ({ page }) => {
      await page.goto('/')
      await expect(page).toHaveURL(/\/login$/)
    })

    test('未登录访问发票管理重定向到登录页', async ({ page }) => {
      await page.goto('/invoice')
      await expect(page).toHaveURL(/\/login$/)
    })

    test('未登录访问报销管理重定向到登录页', async ({ page }) => {
      await page.goto('/reimbursement')
      await expect(page).toHaveURL(/\/login$/)
    })

    test('未登录访问库存管理重定向到登录页', async ({ page }) => {
      await page.goto('/inventory')
      await expect(page).toHaveURL(/\/login$/)
    })

    test('未登录访问角色管理重定向到登录页', async ({ page }) => {
      await page.goto('/roles')
      await expect(page).toHaveURL(/\/login$/)
    })

    test('未登录访问权限管理重定向到登录页', async ({ page }) => {
      await page.goto('/permissions')
      await expect(page).toHaveURL(/\/login$/)
    })
  })

  // ================================================================
  // 权限不足场景（模拟受限用户，不需要后端）
  // ================================================================
  test.describe('权限不足', () => {
    /**
     * 模拟一个已认证但无任何权限的用户
     * 用于测试 ProtectedRoute 的 resource/action 检查
     */
    const LIMITED_USER_STATE = {
      state: {
        token: 'fake-jwt-token-for-testing',
        refreshToken: null,
        user: {
          user_id: 'limited-test-user',
          username: 'limited_user',
          role_codes: ['employee'],
          email: 'limited@test.com',
          department: '测试部门',
          permissions: [], // 无任何权限
        },
        isAuthenticated: true,
      },
      version: 0,
    }

    test.beforeEach(async ({ page }) => {
      // 设置受限用户的认证状态
      await page.goto('/login')
      await setAuthState(page, LIMITED_USER_STATE)
    })

    test('访问发票管理显示权限不足', async ({ page }) => {
      await page.goto('/invoice')

      // 应显示"权限不足"提示
      await expect(page.getByText('权限不足')).toBeVisible({ timeout: 10_000 })
      await expect(
        page.getByText('您没有访问此页面的权限'),
      ).toBeVisible()
    })

    test('访问报销管理显示权限不足', async ({ page }) => {
      await page.goto('/reimbursement')

      await expect(page.getByText('权限不足')).toBeVisible({ timeout: 10_000 })
    })

    test('访问库存管理显示权限不足', async ({ page }) => {
      await page.goto('/inventory')

      await expect(page.getByText('权限不足')).toBeVisible({ timeout: 10_000 })
    })

    test('权限不足用户侧边栏仅显示无需权限的菜单', async ({ page }) => {
      // 导航到 Dashboard（无需特定权限）
      await page.goto('/')

      // "仪表盘"始终可见（无 resource/action 要求）
      await expect(page.getByRole('link', { name: '仪表盘' })).toBeVisible()

      // "发票管理"不应在侧边栏显示（需要 invoice:read 权限）
      await expect(
        page.getByRole('link', { name: '发票管理' }),
      ).not.toBeVisible()

      // "报销管理"不应在侧边栏显示
      await expect(
        page.getByRole('link', { name: '报销管理' }),
      ).not.toBeVisible()
    })
  })

  // ================================================================
  // admin 用户权限场景（需要后端）
  // ================================================================
  test.describe('admin 用户权限', () => {
    test.beforeEach(async ({ page }) => {
      await page.goto('/login')
      const backendReady = await isBackendAvailable(page)
      test.skip(!backendReady, '后端服务未启动，跳过 admin 权限测试')
      await loginAsAdmin(page)
    })

    test('admin 可访问发票管理页面', async ({ page }) => {
      await page.goto('/invoice')

      // 不应显示"权限不足"
      await expect(page.getByText('权限不足')).not.toBeVisible()

      // 应显示页面标题
      await expect(page.getByRole('heading', { name: '发票管理' })).toBeVisible()
    })

    test('admin 可访问报销管理页面', async ({ page }) => {
      await page.goto('/reimbursement')

      await expect(page.getByText('权限不足')).not.toBeVisible()
      await expect(page.getByRole('heading', { name: '报销管理' })).toBeVisible()
    })

    test('admin 可访问库存管理页面', async ({ page }) => {
      await page.goto('/inventory')

      await expect(page.getByText('权限不足')).not.toBeVisible()
    })

    test('admin 可访问会计凭证页面', async ({ page }) => {
      await page.goto('/accounting')

      await expect(page.getByText('权限不足')).not.toBeVisible()
    })

    test('admin 可访问报税管理页面', async ({ page }) => {
      await page.goto('/tax')

      await expect(page.getByText('权限不足')).not.toBeVisible()
    })

    test('admin 侧边栏显示完整菜单', async ({ page }) => {
      // admin 应能看到各核心菜单项
      const menuItems = [
        '仪表盘',
        '发票管理',
        '报销管理',
        '报税管理',
        '会计凭证',
        '员工管理',
        '薪酬管理',
        '库存管理',
        '角色管理',
        '权限管理',
      ]

      for (const item of menuItems) {
        await expect(page.getByRole('link', { name: item }).first()).toBeVisible()
      }
    })

    test('admin 侧边栏显示系统管理分组', async ({ page }) => {
      // admin 应能看到"系统管理"分组
      await expect(page.getByText('系统管理', { exact: true }).first()).toBeVisible()

      // 包含"角色管理"和"权限管理"
      await expect(page.getByRole('link', { name: '角色管理' })).toBeVisible()
      await expect(page.getByRole('link', { name: '权限管理' })).toBeVisible()
    })

    test('admin 发票页面功能按钮可见', async ({ page }) => {
      await page.goto('/invoice')

      await expect(page.getByRole('heading', { name: '发票管理' })).toBeVisible()

      // admin 应能看到刷新按钮
      await expect(page.getByRole('button', { name: '刷新' })).toBeVisible()

      // "开具发票"按钮（需要 invoice:create 权限）
      const issueBtn = page.getByRole('button', { name: '开具发票' })
      if (await issueBtn.count() > 0) {
        await expect(issueBtn.first()).toBeVisible()
      }

      // "导出发票清单"按钮（需要 document:create 权限）
      const exportBtn = page.getByText('导出发票清单')
      if (await exportBtn.count() > 0) {
        await expect(exportBtn.first()).toBeVisible()
      }
    })

    test('admin 报销页面功能按钮可见', async ({ page }) => {
      await page.goto('/reimbursement')

      await expect(page.getByRole('heading', { name: '报销管理' })).toBeVisible()

      await expect(page.getByRole('button', { name: '刷新' })).toBeVisible()

      // "提交报销"按钮（需要 reimbursement:create 权限）
      const submitBtn = page.getByRole('button', { name: '提交报销' })
      if (await submitBtn.count() > 0) {
        await expect(submitBtn.first()).toBeVisible()
      }
    })
  })
})
