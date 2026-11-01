/**
 * 审批人路由矩阵测试（Phase F2.7）
 * ================================
 * 1. 纯函数：矩阵 schema 的双向转换与前端预检（口径必须与后端 match_route 一致）；
 * 2. 渲染：预检拦截时不发请求、合法变更走"转人审"提示、待审批计数来自父级队列。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { useAuthStore } from '../stores/authStore'
import { apiService } from '../services/api'
import {
  ApprovalRouteMatrix,
  formToRule,
  matrixOverrides,
  ruleSummary,
  ruleToForm,
  rulesFromOverrides,
  validateRules,
  type RuleForm,
} from './ApprovalRouteMatrix'

vi.mock('../services/api', () => ({
  apiService: {
    getCustomizationMeta: vi.fn(),
    getAgentCustomizations: vi.fn(),
    putAgentCustomization: vi.fn(),
  },
}))

const ROLES = [
  { code: 'cfo', name: '财务总监', level: 4 },
  { code: 'fin_manager', name: '财务经理', level: 3 },
]

function setPerms(permissions: string[]) {
  useAuthStore.setState({
    token: 't', refreshToken: null, loading: false, error: null, isAuthenticated: true,
    user: { user_id: 'u_cio', username: 'cio', role_codes: ['dept_staff'], permissions } as never,
  })
}

/* ===================== 纯函数 ===================== */

describe('ApprovalRouteMatrix 数据转换', () => {
  it('无覆盖时为空矩阵（全部走存量三级审批链）', () => {
    expect(rulesFromOverrides(undefined)).toEqual([])
    expect(rulesFromOverrides({ rules: '不是数组' })).toEqual([])
  })

  it('规则回读：数字与字符串金额都归一为 number，缺失为 undefined', () => {
    const [a, b] = rulesFromOverrides({
      rules: [
        { document_type: 'reimbursement_submit', amount_min: 0, amount_max: 5000, approve_roles: ['fin_manager'] },
        { document_type: 'treasury_fund_transfer_request', amount_min: '10000', approve_roles: [] },
      ],
    })
    expect(a).toEqual({ document_type: 'reimbursement_submit', amount_min: 0, amount_max: 5000, approve_roles: ['fin_manager'] })
    expect(b.amount_min).toBe(10000)
    expect(b.amount_max).toBeUndefined()
    expect(b.approve_roles).toEqual([])
  })

  it('表单与规则互转：留空金额不写键（后端按缺省视为不限）', () => {
    const form: RuleForm = { document_type: ' x ', amount_min: '', amount_max: 'abc', approve_roles: ['cfo'] }
    expect(formToRule(form)).toEqual({ document_type: 'x', approve_roles: ['cfo'] })
    expect(ruleToForm({ document_type: 'x', approve_roles: ['cfo'] }))
      .toEqual({ document_type: 'x', amount_min: '', amount_max: '', approve_roles: ['cfo'] })
  })

  it('round-trip：下发结果回读一致', () => {
    const rules = [{ document_type: 'reimbursement_submit', amount_min: 1000, amount_max: 5000, approve_roles: ['cfo', 'fin_manager'] }]
    expect(rulesFromOverrides(matrixOverrides(rules))).toEqual(rules)
  })

  it('预检与后端落库口径一致：空类型/空角色/未知角色/区间写反', () => {
    expect(validateRules([{ document_type: '', approve_roles: [] }], ['cfo'])).toEqual([
      '第 1 条：单据类型不能为空',
      '第 1 条：至少指定一个审批角色',
    ])
    expect(validateRules([{ document_type: 'a', approve_roles: ['ghost'] }], ['cfo']))
      .toEqual(['第 1 条：角色 ghost 不在 RBAC 角色清单内'])
    expect(validateRules([{ document_type: 'a', amount_min: 9000, amount_max: 100, approve_roles: ['cfo'] }], ['cfo']))
      .toEqual(['第 1 条：金额区间下限大于上限（该规则永远不会命中）'])
    expect(validateRules([{ document_type: 'a', approve_roles: ['cfo'] }], ['cfo'])).toEqual([])
  })

  it('ruleSummary 把规则读成人话（含不限与未填）', () => {
    expect(ruleSummary({ document_type: 'a', approve_roles: ['cfo'] }))
      .toBe('a · 任意金额 → cfo')
    expect(ruleSummary({ document_type: 'a', amount_max: 1000, approve_roles: [] }))
      .toBe('a · 不限 ~ 1000 → （未选角色）')
    expect(ruleSummary({ document_type: '', approve_roles: ['cfo'] }))
      .toBe('（未填类型） · 任意金额 → cfo')
  })
})

/* ===================== 渲染 ===================== */

describe('ApprovalRouteMatrix 渲染', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    setPerms(['customization:read', 'customization:update'])
    vi.mocked(apiService.getCustomizationMeta).mockResolvedValue({
      agents: [], roles: ROLES, document_types: ['reimbursement_submit'],
    } as never)
    vi.mocked(apiService.getAgentCustomizations).mockResolvedValue({ customizations: [] } as never)
    vi.mocked(apiService.putAgentCustomization).mockResolvedValue({
      status: 'pending_approval', reason: '放宽类变更需人工审批后生效',
    } as never)
  })

  it('空矩阵给出明确的存量回退说明', async () => {
    render(<ApprovalRouteMatrix />)
    expect(await screen.findByText(/尚无矩阵规则/)).toBeInTheDocument()
    expect(screen.getByText('已生效 0 条 · 编辑中 0 条')).toBeInTheDocument()
  })

  it('预检失败时拦住请求，逐条显示原因', async () => {
    render(<ApprovalRouteMatrix />)
    fireEvent.click(await screen.findByText('新增规则'))
    fireEvent.click(screen.getByText('cfo'))
    fireEvent.click(screen.getByText('提交变更（转人审）'))

    expect(await screen.findByText('第 1 条：单据类型不能为空')).toBeInTheDocument()
    expect(apiService.putAgentCustomization).not.toHaveBeenCalled()
  })

  it('合法变更下发矩阵并如实提示转人审（红线 2）', async () => {
    render(<ApprovalRouteMatrix />)
    fireEvent.click(await screen.findByText('新增规则'))
    fireEvent.change(screen.getByPlaceholderText(/单据类型/), {
      target: { value: 'reimbursement_submit' },
    })
    fireEvent.change(screen.getByPlaceholderText('金额上限（空=不限）'), { target: { value: '5000' } })
    fireEvent.click(screen.getByText('cfo'))
    expect(screen.getByText('reimbursement_submit · 不限 ~ 5000 → cfo')).toBeInTheDocument()

    fireEvent.click(screen.getByText('提交变更（转人审）'))
    await waitFor(() => expect(apiService.putAgentCustomization).toHaveBeenCalled())
    expect(vi.mocked(apiService.putAgentCustomization).mock.calls[0]).toEqual([
      'gate', 'approval_route_matrix',
      {
        overrides: {
          rules: [{ document_type: 'reimbursement_submit', approve_roles: ['cfo'], amount_max: 5000 }],
          default: 'legacy',
        },
        enabled: true,
        comment: '管理端审批路由矩阵编辑',
      },
    ])
    expect(await screen.findByText(/已提交人工审批，批准后才参与路由/)).toBeInTheDocument()
  })

  it('已生效规则回读为可编辑行', async () => {
    vi.mocked(apiService.getAgentCustomizations).mockResolvedValue({
      customizations: [{
        type: 'gate', target: 'approval_route_matrix', enabled: true,
        overrides: { rules: [{ document_type: 'treasury_fund_transfer_request', amount_min: 100000, approve_roles: ['cfo'] }] },
      }],
    } as never)
    render(<ApprovalRouteMatrix />)

    expect(await screen.findByDisplayValue('treasury_fund_transfer_request')).toBeInTheDocument()
    expect(screen.getByText('已生效 1 条 · 编辑中 1 条')).toBeInTheDocument()
    expect(screen.getByPlaceholderText('金额下限（空=不限）')).toHaveValue(100000)
    // 未改动时提交不可用
    expect((screen.getByText('提交变更（转人审）') as HTMLButtonElement).disabled).toBe(true)
  })

  it('待审批计数由父级队列下发（单一数据源）', async () => {
    render(<ApprovalRouteMatrix pendingCount={2} />)
    expect(await screen.findByText('2 条矩阵变更待审批')).toBeInTheDocument()
  })

  it('后端 400 原样呈现（非法角色等）', async () => {
    vi.mocked(apiService.putAgentCustomization).mockRejectedValue(
      Object.assign(new Error('boom'), { response: { data: { detail: '未知角色: ghost' } } }) as never)
    render(<ApprovalRouteMatrix />)
    fireEvent.click(await screen.findByText('新增规则'))
    fireEvent.change(screen.getByPlaceholderText(/单据类型/), { target: { value: 'a' } })
    fireEvent.click(screen.getByText('cfo'))
    fireEvent.click(screen.getByText('提交变更（转人审）'))

    expect(await screen.findByText('未知角色: ghost')).toBeInTheDocument()
  })
})
