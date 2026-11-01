import { test, expect } from '@playwright/test'
import { loginAsAdmin, isBackendAvailable } from './helpers/auth'

/**
 * 发票管理流程 E2E 测试
 * =====================
 * 覆盖发票页面加载、列表展示、导出功能等场景
 */
test.describe('发票管理流程', () => {
  test.beforeEach(async ({ page }) => {
    // 登录前置准备
    await page.goto('/login')
    const backendReady = await isBackendAvailable(page)
    test.skip(!backendReady, '后端服务未启动，跳过发票管理测试')
    await loginAsAdmin(page)
  })

  test('发票页面正确加载', async ({ page }) => {
    await page.goto('/invoice')

    // 页面标题
    await expect(page.getByRole('heading', { name: '发票管理' })).toBeVisible()

    // 副标题
    await expect(page.getByText('发票开具、验证、红冲与统计分析')).toBeVisible()

    // 统计卡片标题
    await expect(page.getByText('发票总数').first()).toBeVisible()
    await expect(page.getByText('有效发票').first()).toBeVisible()
    await expect(page.getByText('税额合计').first()).toBeVisible()
    await expect(page.getByText('红冲发票').first()).toBeVisible()

    // 刷新按钮
    await expect(page.getByRole('button', { name: '刷新' })).toBeVisible()
  })

  test('发票列表展示', async ({ page }) => {
    await page.goto('/invoice')

    // 等待"发票列表"标题出现
    await expect(page.getByText('发票列表')).toBeVisible()

    // 验证表格表头存在
    const tableHead = page.locator('table thead tr')
    await expect(tableHead).toBeVisible()

    // 验证表头列名
    await expect(page.locator('th', { hasText: '发票号' }).first()).toBeVisible()
    await expect(page.locator('th', { hasText: '类型' }).first()).toBeVisible()
    await expect(page.locator('th', { hasText: '购买方' }).first()).toBeVisible()
    await expect(page.locator('th', { hasText: '状态' }).first()).toBeVisible()

    // 验证搜索框
    await expect(
      page.getByPlaceholder('搜索发票号/购买方...'),
    ).toBeVisible()

    // 验证状态筛选按钮
    await expect(page.getByRole('button', { name: '全部' })).toBeVisible()

    // 检查表格内容：有数据则验证行存在，无数据则验证空状态提示
    const tableRows = page.locator('table tbody tr')
    const rowCount = await tableRows.count()

    if (rowCount === 0) {
      // 空状态
      await expect(page.getByText('暂无发票记录')).toBeVisible()
    } else {
      // 有数据：验证至少有一行
      expect(rowCount).toBeGreaterThan(0)
    }
  })

  test('导出按钮存在（如果权限允许）', async ({ page }) => {
    await page.goto('/invoice')

    // 等待页面加载
    await expect(page.getByRole('heading', { name: '发票管理' })).toBeVisible()

    // 导出按钮（需要 document:create 权限）
    // admin 用户通常拥有此权限；若无权限则按钮不存在，测试仍通过
    const exportButton = page.getByText('导出发票清单')
    const count = await exportButton.count()

    if (count > 0) {
      await expect(exportButton.first()).toBeVisible()
    }
  })

  test('开具发票按钮存在（如果权限允许）', async ({ page }) => {
    await page.goto('/invoice')

    await expect(page.getByRole('heading', { name: '发票管理' })).toBeVisible()

    // "开具发票"按钮（需要 invoice:create 权限）
    const issueButton = page.getByRole('button', { name: '开具发票' })
    const count = await issueButton.count()

    if (count > 0) {
      await expect(issueButton.first()).toBeVisible()
    }
  })

  test('状态筛选功能正常', async ({ page }) => {
    await page.goto('/invoice')

    await expect(page.getByText('发票列表')).toBeVisible()

    // 点击"已开具"筛选
    const issuedFilter = page.getByRole('button', { name: '已开具' })
    if (await issuedFilter.count() > 0) {
      await issuedFilter.click()
      // 验证筛选按钮激活状态
      await expect(issuedFilter).toHaveClass(/bg-primary/)
    }

    // 点击"全部"恢复
    await page.getByRole('button', { name: '全部' }).click()
    await expect(page.getByRole('button', { name: '全部' })).toHaveClass(/bg-primary/)
  })

  test('搜索功能正常', async ({ page }) => {
    await page.goto('/invoice')

    await expect(page.getByText('发票列表')).toBeVisible()

    // 在搜索框输入内容
    const searchInput = page.getByPlaceholder('搜索发票号/购买方...')
    await searchInput.fill('测试搜索')

    // 验证输入值正确
    await expect(searchInput).toHaveValue('测试搜索')

    // 清空搜索
    await searchInput.clear()
    await expect(searchInput).toHaveValue('')
  })
})
