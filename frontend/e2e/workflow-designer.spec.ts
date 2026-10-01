/**
 * 工作流设计器 E2E（Phase F3.6 管理端二期）
 * =========================================
 * 后端 /agent-ops/workflow-definitions/* 的响应由 e2e/fixtures/workflow-designer/*.json
 * 提供——这些 JSON 是从同一 FastAPI 路由 + 真校验器 + 真版本表导出的真实响应，
 * 重新生成：backend 下执行
 *   DUMP_WF_FIXTURES=1 python -m pytest tests/test_dump_designer_fixtures.py -s
 * 因此本用例校验的是前端与后端契约，而非自造样例。
 *
 * 运行：npx playwright test e2e/workflow-designer.spec.ts --retries=0
 */
import { test, expect, type Page, type Route } from '@playwright/test'
import fs from 'fs'
import path from 'path'
import { fileURLToPath } from 'url'
import { setAuthState } from './helpers/auth'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const FX = path.join(HERE, 'fixtures', 'workflow-designer')
const fx = (name: string) => JSON.parse(fs.readFileSync(path.join(FX, name), 'utf-8'))

const meta = fx('meta.json')
const status = fx('status.json')
const definition = fx('definition.json')
const builtinNodes = meta.builtin_dsl.reimbursement_approval.nodes as { id: string; type: string }[]

const CIO_STATE = {
  state: {
    token: 'e2e-fixture-token',
    refreshToken: null,
    user: {
      user_id: 'user_admin',
      username: 'admin',
      full_name: 'E2E 管理员',
      department: '数字化中心',
      email: '',
      role_codes: ['cio'],
      permissions: [
        'system:manage', 'workflow:read',
        'customization:read', 'customization:update', 'customization:publish', 'customization:export',
      ],
      must_change_password: false,
    },
    isAuthenticated: true,
  },
  version: 0,
}

/** 全部 /api/v1 请求本地应答：设计器路由给真响应，其余给空壳，避免 401 触发刷新跳转。 */
async function stubBackend(page: Page) {
  await page.route('**/api/v1/**', async (route: Route) => {
    const p = new URL(route.request().url()).pathname
    const method = route.request().method()
    const send = (body: unknown, status = 200) =>
      route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })

    if (p.endsWith('/workflow-definitions/meta')) return send(meta)
    if (p.endsWith('/workflow-definitions/validate')) return send(fx('validate_errors.json'))
    // 动词端点先匹配：/{name}/{version}/publish 与 /{name}/rollback 都形如"两段路径"
    if (/(draft|publish|rollback)$/.test(p)) {
      if (method === 'PUT') return send(fx('draft.json'))
      if (p.endsWith('/publish')) return send(fx('publish.json'))
      return send(fx('rollback.json'))
    }
    if (/\/workflow-definitions\/[^/]+\/[^/]+$/.test(p)) return send(definition)
    if (/\/workflow-definitions\/[^/]+$/.test(p)) return send(status)
    if (p.endsWith('/auth/me')) return send(CIO_STATE.state.user)
    return send({})
  })
}

async function openDesigner(page: Page) {
  await page.goto('/login')
  await setAuthState(page, CIO_STATE)
  await stubBackend(page)
  await page.goto('/cio-admin')
  await page.getByRole('button', { name: /工作流设计/ }).click()
  await expect(page.getByText('工作流设计器')).toBeVisible()
}

test.describe('工作流设计器（真实后端响应回放）', () => {
  test('出厂模板 + 已发布版本状态正确渲染', async ({ page }) => {
    await openDesigner(page)

    // 元数据驱动：流程下拉来自后端 workflows 列表
    await expect(page.locator('select').first()).toContainText('reimbursement_approval')
    // 出厂 DSL 节点逐条渲染（数量取自真响应，避免前端自造预期）
    await expect(page.getByText(`节点（${builtinNodes.length}）`)).toBeVisible()
    // 版本状态：stable 与灰度 canary 同时在场（D-3/S1 语义）
    await expect(page.getByText(`stable ${status.stable.version}`)).toBeVisible()
    await expect(page.getByText(`canary ${status.canary.version} · ${status.canary.traffic}%`)).toBeVisible()
    await expect(page.getByText(`版本历史（${status.versions.length}）`)).toBeVisible()
    await expect(page.getByText('尚无发布版本')).toHaveCount(0)

    await page.screenshot({
      path: path.join(HERE, 'test-results', 'workflow-designer.png'), fullPage: true,
    })
  })

  test('载入已发布版本进入编辑态', async ({ page }) => {
    await openDesigner(page)
    // 版本历史按新→旧排列：末位是 stable v1（definition.json 即其真实响应）
    await page.getByRole('button', { name: '载入编辑' }).last().click()

    await expect(page.getByText(new RegExp(`已载入 ${definition.name}@${definition.version}`))).toBeVisible()
    await expect(page.getByText(`节点（${definition.definition.nodes.length}）`)).toBeVisible()
    // condition 多路分支与额外合规门（UAT-F3-2）在节点列表可见
    for (const id of ['route', 'compliance_gate']) {
      await expect(page.getByText(id).first()).toBeVisible()
    }
  })

  test('gate 节点暴露治理字段（审批角色/单据类型/超时/双出边）', async ({ page }) => {
    await openDesigner(page)
    const gate = builtinNodes.find((n) => n.type === 'gate')
    expect(gate).toBeTruthy()
    await page.getByText(gate!.id).first().click()

    for (const label of [
      '审批角色（逗号分隔，留空走路由矩阵）',
      '单据类型 document_type',
      '超时提醒（小时）',
      '驳回 →（红线：必填）',
      '阈值键（白名单，可留空）',
    ]) {
      await expect(page.getByText(label)).toBeVisible()
    }
  })

  test('校验走后端同一校验器并逐条回显中文错误', async ({ page }) => {
    await openDesigner(page)
    await page.getByRole('button', { name: '校验' }).click()

    const err = fx('validate_errors.json')
    for (const msg of err.errors as string[]) {
      await expect(page.getByText(msg)).toBeVisible()
    }
    await expect(page.getByText(`校验未通过（${err.errors.length} 项）`)).toBeVisible()
  })

  test('申请发布转人工审批（红线 2），回滚即时生效', async ({ page }) => {
    await openDesigner(page)

    await page.getByRole('button', { name: '申请发布' }).click()
    await expect(page.getByText(/发布申请已提交人工审批/)).toBeVisible()

    await page.getByRole('button', { name: /回滚/ }).first().click()
    await expect(page.getByText(/已回滚到 reimbursement_approval@/)).toBeVisible()
  })

  test('无 customization:publish 权限时发布入口隐藏', async ({ page }) => {
    await page.goto('/login')
    await setAuthState(page, {
      ...CIO_STATE,
      state: {
        ...CIO_STATE.state,
        user: {
          ...CIO_STATE.state.user,
          role_codes: ['customization_admin'],
          permissions: ['system:manage', 'customization:read', 'customization:update'],
        },
      },
    })
    await stubBackend(page)
    await page.goto('/cio-admin')
    await page.getByRole('button', { name: /工作流设计/ }).click()

    await expect(page.getByText('需 customization:publish 权限')).toBeVisible()
    await expect(page.getByRole('button', { name: '申请发布' })).toHaveCount(0)
    await expect(page.getByRole('button', { name: '保存草稿' })).toBeVisible()
  })
})
