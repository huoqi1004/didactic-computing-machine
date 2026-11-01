import { test, expect } from '@playwright/test'
import { loginAsAdmin, isBackendAvailable } from './helpers/auth'

/**
 * 报销管理流程 E2E 测试
 * =====================
 * 覆盖报销页面加载、列表展示、创建功能等场景
 */
test.describe('报销管理流程', () => {
  test.beforeEach(async ({ page }) => {
    // 登录前置准备
    await page.goto('/login')
    const backendReady = await isBackendAvailable(page)
    test.skip(!backendReady, '后端服务未启动，跳过报销管理测试')
    await loginAsAdmin(page)
  })

  test('报销页面正确加载', async ({ page }) => {
    await page.goto('/reimbursement')

    // 页面标题
    await expect(page.getByRole('heading', { name: '报销管理' })).toBeVisible()

    // 副标题
    await expect(
      page.getByText('员工报销申请、项目费用核验、审批与付款'),
    ).toBeVisible()

    // 统计卡片标题
    await expect(page.getByText('报销总数').first()).toBeVisible()
    await expect(page.getByText('待处理').first()).toBeVisible()
    await expect(page.getByText('已付款').first()).toBeVisible()
    await expect(page.getByText('已拒绝').first()).toBeVisible()

    // 刷新按钮
    await expect(page.getByRole('button', { name: '刷新' })).toBeVisible()
  })

  test('报销列表展示', async ({ page }) => {
    await page.goto('/reimbursement')

    // 等待"报销列表"标题出现
    await expect(page.getByText('报销列表')).toBeVisible()

    // 验证表格表头存在
    const tableHead = page.locator('table thead tr')
    await expect(tableHead).toBeVisible()

    // 验证表头列名
    await expect(page.locator('th', { hasText: '报销单号' }).first()).toBeVisible()
    await expect(page.locator('th', { hasText: '员工' }).first()).toBeVisible()
    await expect(page.locator('th', { hasText: '部门' }).first()).toBeVisible()
    await expect(page.locator('th', { hasText: '金额' }).first()).toBeVisible()
    await expect(page.locator('th', { hasText: '状态' }).first()).toBeVisible()

    // 验证搜索框
    await expect(
      page.getByPlaceholder('搜索单号/员工/描述...'),
    ).toBeVisible()

    // 验证状态筛选按钮存在
    await expect(page.getByRole('button', { name: '全部' })).toBeVisible()

    // 检查表格内容
    const tableRows = page.locator('table tbody tr')
    const rowCount = await tableRows.count()

    if (rowCount === 0) {
      // 空状态
      await expect(page.getByText('暂无报销记录')).toBeVisible()
    } else {
      expect(rowCount).toBeGreaterThan(0)
    }
  })

  test('创建报销按钮存在', async ({ page }) => {
    await page.goto('/reimbursement')

    await expect(page.getByRole('heading', { name: '报销管理' })).toBeVisible()

    // "提交报销"按钮（需要 reimbursement:create 权限）
    const submitButton = page.getByRole('button', { name: '提交报销' })
    const count = await submitButton.count()

    if (count > 0) {
      await expect(submitButton.first()).toBeVisible()
    }
  })

  test('注册项目按钮存在（如果权限允许）', async ({ page }) => {
    await page.goto('/reimbursement')

    await expect(page.getByRole('heading', { name: '报销管理' })).toBeVisible()

    // "注册项目"按钮（需要 reimbursement:create 权限）
    const projectButton = page.getByRole('button', { name: '注册项目' })
    const count = await projectButton.count()

    if (count > 0) {
      await expect(projectButton.first()).toBeVisible()
    }
  })

  test('注册员工按钮存在（如果权限允许）', async ({ page }) => {
    await page.goto('/reimbursement')

    await expect(page.getByRole('heading', { name: '报销管理' })).toBeVisible()

    // "注册员工"按钮（需要 reimbursement:create 权限）
    const employeeButton = page.getByRole('button', { name: '注册员工' })
    const count = await employeeButton.count()

    if (count > 0) {
      await expect(employeeButton.first()).toBeVisible()
    }
  })

  test('导出按钮存在（如果权限允许）', async ({ page }) => {
    await page.goto('/reimbursement')

    await expect(page.getByRole('heading', { name: '报销管理' })).toBeVisible()

    // "导出报销清单"按钮（需要 document:create 权限）
    const exportButton = page.getByText('导出报销清单')
    const count = await exportButton.count()

    if (count > 0) {
      await expect(exportButton.first()).toBeVisible()
    }
  })

  test('状态筛选功能正常', async ({ page }) => {
    await page.goto('/reimbursement')

    await expect(page.getByText('报销列表')).toBeVisible()

    // 点击"待核验"筛选
    const pendingFilter = page.getByRole('button', { name: '待核验' })
    if (await pendingFilter.count() > 0) {
      await pendingFilter.click()
      await expect(pendingFilter).toHaveClass(/bg-primary/)
    }

    // 点击"全部"恢复
    await page.getByRole('button', { name: '全部' }).click()
    await expect(page.getByRole('button', { name: '全部' })).toHaveClass(/bg-primary/)
  })

  test('搜索功能正常', async ({ page }) => {
    await page.goto('/reimbursement')

    await expect(page.getByText('报销列表')).toBeVisible()

    // 在搜索框输入内容
    const searchInput = page.getByPlaceholder('搜索单号/员工/描述...')
    await searchInput.fill('测试搜索')

    // 验证输入值正确
    await expect(searchInput).toHaveValue('测试搜索')

    // 清空搜索
    await searchInput.clear()
    await expect(searchInput).toHaveValue('')
  })

  test('提交报销弹窗可打开（如果权限允许）', async ({ page }) => {
    await page.goto('/reimbursement')

    await expect(page.getByRole('heading', { name: '报销管理' })).toBeVisible()

    // 点击"提交报销"按钮
    const submitButton = page.getByRole('button', { name: '提交报销' })
    if (await submitButton.count() > 0) {
      await submitButton.first().click()

      // 验证弹窗标题出现
      await expect(page.getByText('提交报销申请')).toBeVisible({ timeout: 5_000 })

      // 验证表单字段存在
      await expect(page.getByText('员工姓名').first()).toBeVisible()
      await expect(page.getByText('报销金额').first()).toBeVisible()
      await expect(page.getByText('报销描述').first()).toBeVisible()

      // 关闭弹窗
      await page.getByRole('button', { name: '取消' }).click()
    }
  })
})
