/**
 * Agent 定制中心 E2E（Phase F1.7 + F2.7 二期）
 * ============================================
 * 后端 /agent-ops/{tools,customizations*,approvals*} 的响应由
 * e2e/fixtures/agent-customization/*.json 提供——这些 JSON 是从同一 FastAPI 路由 +
 * 真定制服务 + 真版本表/审批表导出的真实响应，重新生成：backend 下执行
 *   DUMP_CUST_FIXTURES=1 python -m pytest tests/test_dump_customization_fixtures.py -s
 * 夹具里刻意留了一条"待批的放宽变更"（工具重新启用），因此真机里能看到审批队列。
 *
 * 运行：npx playwright test e2e/agent-customization.spec.ts --retries=0
 */
import { test, expect, type Page, type Route } from '@playwright/test'
import fs from 'fs'
import path from 'path'
import { fileURLToPath } from 'url'
import { setAuthState } from './helpers/auth'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const FX = path.join(HERE, 'fixtures', 'agent-customization')
const fx = (name: string) => JSON.parse(fs.readFileSync(path.join(FX, name), 'utf-8'))

const tools = fx('tools.json')
const meta = fx('meta.json')
const customizations = fx('customizations.json')
const versions = fx('versions.json')
const approvals = fx('approvals_pending.json')

const reimbAgent = meta.agents.find((a: { domain: string }) => a.domain === 'reimbursement')
const activeMatrix = customizations.customizations.find(
  (c: { type: string, target: string }) => c.type === 'gate' && c.target === 'approval_route_matrix',
)
const activeToolOverrides = customizations.customizations.filter(
  (c: { type: string }) => c.type === 'tool',
)
const rollbackable = versions.versions.filter(
  (v: { status: string }) => ['active', 'approved_executed', 'rolled_back'].includes(v.status),
)

const CIO_USER = {
  user_id: 'user_admin',
  username: 'admin',
  full_name: 'E2E 管理员',
  department: '数字化中心',
  email: '',
  role_codes: ['cio'],
  permissions: [
    'system:manage', 'agent:read', 'agent:approve',
    'customization:read', 'customization:update', 'customization:publish', 'customization:export',
  ],
  must_change_password: false,
}

const CIO_STATE = {
  state: {
    token: 'e2e-fixture-token',
    refreshToken: null,
    user: CIO_USER,
    isAuthenticated: true,
  },
  version: 0,
}

/** 全部 /api/v1 请求本地应答：定制路由给真响应，其余给空壳，避免 401 触发刷新跳转。 */
async function stubBackend(page: Page, user: Record<string, unknown> = CIO_USER) {
  await page.route('**/api/v1/**', async (route: Route) => {
    const p = new URL(route.request().url()).pathname
    const method = route.request().method()
    const send = (body: unknown, status = 200) =>
      route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })

    // 具名子资源必须先匹配：/customizations/meta 与 /versions 都形如 /{ctype}/{target}
    if (p.endsWith('/agent-ops/tools')) return send(tools)
    if (p.endsWith('/customizations/meta')) return send(meta)
    if (p.endsWith('/customizations/versions')) return send(versions)
    if (p.endsWith('/customizations')) return send(customizations)
    if (p.endsWith('/approvals') || p.endsWith('/approvals/')) {
      return send(approvals)
    }
    if (p.endsWith('/decide')) return send(fx('matrix_decided.json'))
    if (p.endsWith('/rollback')) return send(fx('rollback.json'))
    if (/\/customizations\/[^/]+\/[^/]+$/.test(p)) {
      if (method === 'PUT') {
        return send(p.includes('/gate/') ? fx('matrix_pending.json') : fx('apply_agent.json'))
      }
      return send({})
    }
    if (p.endsWith('/auth/me')) return send(user)
    return send({})
  })
}

async function openCenter(page: Page, user?: Record<string, unknown>) {
  await page.goto('/login')
  await setAuthState(page, user ? { ...CIO_STATE, state: { ...CIO_STATE.state, user } } : CIO_STATE)
  await stubBackend(page, user)
  await page.goto('/cio-admin')
  await page.getByRole('button', { name: 'Agent 定制中心' }).click()
  await expect(page.getByText('收紧即时生效 · 放宽需审批')).toBeVisible()
}

test.describe('Agent 定制中心（真实后端响应回放）', () => {
  test('工具治理分区：真目录逐条渲染，待批队列来自真人审任务', async ({ page }) => {
    await openCenter(page)

    // 13 条工具逐条成卡（数量取自真目录，避免前端自造预期）
    await expect(page.getByRole('combobox')).toHaveCount(tools.tools.length)
    // 生效覆盖标记来自真 agent_customizations 行（tool 类型逐一对应）
    await expect(page.getByText('已定制')).toHaveCount(activeToolOverrides.length)

    const pending = approvals.approvals[0]
    await expect(page.getByText(`待审批的定制变更（${approvals.approvals.length}）`)).toBeVisible()
    await expect(page.getByText(pending.tool).first()).toBeVisible()
    // 待批的那条提案是"重新启用"，其工具卡当前仍是停用态（未批先松=红线 2 违例）
    const offlineTool = pending.tool.split(':')[1]
    await expect(page.getByText(offlineTool).first()).toBeVisible()
    await expect(page.getByText('启用')).toHaveCount(1)

    await page.screenshot({
      path: path.join(HERE, 'test-results', 'agent-customization-tools.png'), fullPage: true,
    })
  })

  test('待批队列批准入口直连 decide，回显后端真实状态', async ({ page }) => {
    await openCenter(page)
    await expect(page.getByText(`待审批的定制变更（${approvals.approvals.length}）`)).toBeVisible()

    await page.getByRole('button', { name: '批准' }).click()
    await expect(page.getByText(/tool:reimbursement_query_detail：已批准并生效/)).toBeVisible()
  })

  test('Agent 分区：参数表单由后端声明表生成，不靠前端硬编码', async ({ page }) => {
    await openCenter(page)
    await page.getByRole('button', { name: 'Agent 人设与参数' }).click()

    // 域卡片数量 = 后端 /customizations/meta 的 agents 条数
    await expect(page.getByRole('button', { name: '保存' })).toHaveCount(meta.agents.length)
    await expect(page.getByText(reimbAgent.label)).toBeVisible()
    // 夹具里后端真写过 tool_whitelist=[1 个] → 计数按真生效覆盖回读
    await expect(page.getByText(`工具子集（1/${reimbAgent.tools.length}）`)).toBeVisible()
    for (const spec of reimbAgent.params) {
      await expect(page.getByText(spec.label)).toBeVisible()
      await expect(page.getByText(spec.description)).toBeVisible()
    }
    // 已生效的 Agent 覆盖回读（夹具里由后端真写过）
    await expect(page.getByText('清除定制')).toHaveCount(1)
    await expect(page.getByRole('button', { name: '保存' }).first()).toBeDisabled()

    await page.screenshot({
      path: path.join(HERE, 'test-results', 'agent-customization-agents.png'), fullPage: true,
    })
  })

  test('矩阵分区：已生效规则回读为可编辑行，提交变更转人审', async ({ page }) => {
    await openCenter(page)
    await page.getByRole('button', { name: '审批路由矩阵' }).click()

    const docTypeInputs = page.getByPlaceholder(/单据类型/)
    await expect(docTypeInputs).toHaveCount(activeMatrix.overrides.rules.length)
    for (const [i, rule] of activeMatrix.overrides.rules.entries()) {
      await expect(docTypeInputs.nth(i)).toHaveValue(rule.document_type)
    }
    await expect(
      page.getByText(`已生效 ${activeMatrix.overrides.rules.length} 条 · 编辑中 ${activeMatrix.overrides.rules.length} 条`),
    ).toBeVisible()

    // 改首条规则的金额下限（既有规则本身合法，只测"提交即转人审"）
    await page.getByPlaceholder('金额下限（空=不限）').first().fill('200000')
    await page.getByRole('button', { name: '提交变更（转人审）' }).click()
    await expect(page.getByText(/矩阵变更已提交人工审批，批准后才参与路由/)).toBeVisible()

    await page.screenshot({
      path: path.join(HERE, 'test-results', 'agent-customization-matrix.png'), fullPage: true,
    })
  })

  test('矩阵预检拦住未选角色的新规则（与后端落库口径一致）', async ({ page }) => {
    await openCenter(page)
    await page.getByRole('button', { name: '审批路由矩阵' }).click()
    await page.getByRole('button', { name: '新增规则' }).click()
    await page.getByPlaceholder(/单据类型/).last().fill('e2e_doc_type')

    // 不选角色直接提交
    await page.getByRole('button', { name: '提交变更（转人审）' }).click()
    await expect(
      page.getByText(`第 ${activeMatrix.overrides.rules.length + 1} 条：至少指定一个审批角色`),
    ).toBeVisible()
    await expect(page.getByRole('button', { name: '提交变更（转人审）' })).toBeEnabled()
  })

  test('变更历史：待批提案行不提供撤销，已生效版本才可撤销', async ({ page }) => {
    await openCenter(page)

    await page.getByRole('button', { name: new RegExp(`变更历史（${versions.versions.length}）`) }).click()
    await expect(page.getByText('pending_approval')).toBeVisible()
    await expect(page.getByRole('button', { name: /撤销此次变更/ })).toHaveCount(rollbackable.length)

    await page.getByRole('button', { name: /撤销此次变更/ }).first().click()
    await expect(page.getByText(/已回滚到版本/)).toBeVisible()
  })

  test('无 update/approve 权限时写入口全部隐藏（后端仍是权威）', async ({ page }) => {
    // 保留 system:manage（管理页入口本身受它保护），只去掉定制写权限与审批权限
    await openCenter(page, {
      ...CIO_USER,
      role_codes: ['dept_staff'],
      permissions: ['system:manage', 'customization:read', 'agent:read'],
    })

    await expect(page.getByText(`待审批的定制变更（${approvals.approvals.length}）`)).toBeVisible()
    await expect(page.getByRole('button', { name: '批准' })).toHaveCount(0)
    await expect(page.getByText('需 agent:approve 权限')).toBeVisible()
    await expect(page.getByRole('combobox')).toHaveCount(0)
    await expect(page.getByRole('button', { name: '停用' })).toHaveCount(0)
  })
})
