/**
 * Agent 卡片式定制测试（Phase F2.7）
 * ==================================
 * 1. 纯函数：覆盖回读（draftFromOverrides）与下发（buildAgentOverrides）的口径对称，
 *    尤其是"全选/全不选=不限制"与 tri 参数写 null 的语义——这两处一旦走偏，
 *    管理端保存就会把"没勾"变成"禁用全部工具"或把"跟随默认"变成"强制关闭"；
 * 2. 渲染：元数据驱动参数表单、保存按钮脏检查、清除定制入口。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { useAuthStore } from '../stores/authStore'
import { apiService } from '../services/api'
import {
  AgentProfileCards,
  buildAgentOverrides,
  draftFromOverrides,
  isDraftDirty,
  type AgentMeta,
  type AgentDraft,
} from './AgentProfileCards'

vi.mock('../services/api', () => ({
  apiService: {
    getCustomizationMeta: vi.fn(),
    getAgentCustomizations: vi.fn(),
    putAgentCustomization: vi.fn(),
  },
}))

const META: AgentMeta = {
  domain: 'reimbursement',
  label: '报销',
  tools: ['reimb_create', 'reimb_submit', 'reimb_query'],
  params: [
    { key: 'require_receipt', label: '强制附发票/收据', type: 'tri', description: '跟随默认=按类别' },
  ],
}

const TREASURY: AgentMeta = {
  domain: 'treasury',
  label: '资金',
  tools: ['treasury_fund_transfer'],
  params: [],
}

const DRAFT: AgentDraft = {
  prompt_override: '',
  temperature: '',
  daily_token_budget: '',
  tool_whitelist: [],
  params: { require_receipt: 'default' },
}

function setPerms(permissions: string[]) {
  useAuthStore.setState({
    token: 't',
    refreshToken: null,
    loading: false,
    error: null,
    isAuthenticated: true,
    user: {
      user_id: 'u_cio', username: 'cio', role_codes: ['dept_staff'], permissions,
    } as never,
  })
}

/** 占位提示在各域卡片上重复，必须先按域定位卡片再作用域查询 */
async function cardOf(label: string): Promise<HTMLElement> {
  const el = (await screen.findByText(label)).closest('.rounded-xl')
  if (!el) throw new Error(`未找到卡片：${label}`)
  return el as HTMLElement
}

/* ===================== 纯函数 ===================== */

describe('AgentProfileCards 表单转换', () => {
  it('空覆盖回读为默认草稿', () => {
    expect(draftFromOverrides(META, {})).toEqual(DRAFT)
  })

  it('覆盖回读：标量与 params 各自归位', () => {
    const draft = draftFromOverrides(META, {
      prompt_override: '严谨审核',
      temperature: 0.2,
      daily_token_budget: 50000,
      tool_whitelist: ['reimb_query'],
      params: { require_receipt: true },
    })
    expect(draft).toEqual({
      prompt_override: '严谨审核',
      temperature: '0.2',
      daily_token_budget: '50000',
      tool_whitelist: ['reimb_query'],
      params: { require_receipt: 'true' },
    })
  })

  it('下发：留空字段不进 overrides（回到出厂默认）', () => {
    expect(buildAgentOverrides(META, DRAFT)).toEqual({ params: { require_receipt: null } })
  })

  it('下发：tri 三态 → true/false/null', () => {
    const base = { ...DRAFT, params: { require_receipt: 'false' } }
    expect(buildAgentOverrides(META, base).params).toEqual({ require_receipt: false })
    expect(buildAgentOverrides(META, { ...DRAFT, params: { require_receipt: 'true' } }).params)
      .toEqual({ require_receipt: true })
  })

  it('下发：白名单全选或全不选都不写（空数组在运行期等于不限制）', () => {
    const all = buildAgentOverrides(META, { ...DRAFT, tool_whitelist: [...META.tools] })
    expect(all.tool_whitelist).toBeUndefined()
    expect(buildAgentOverrides(META, DRAFT).tool_whitelist).toBeUndefined()
    expect(buildAgentOverrides(META, { ...DRAFT, tool_whitelist: ['reimb_query'] }).tool_whitelist)
      .toEqual(['reimb_query'])
  })

  it('round-trip：下发结果回读等于草稿（仅 params 因 null 归位为 default）', () => {
    const draft: AgentDraft = {
      prompt_override: '只看单据事实',
      temperature: '0',
      daily_token_budget: '1000',
      tool_whitelist: ['reimb_create'],
      params: { require_receipt: 'true' },
    }
    const ov = buildAgentOverrides(META, draft)
    expect(draftFromOverrides(META, ov)).toEqual(draft)
  })

  it('isDraftDirty 对 params 内的变化同样敏感', () => {
    expect(isDraftDirty(DRAFT, DRAFT)).toBe(false)
    expect(isDraftDirty(DRAFT, { ...DRAFT, params: { require_receipt: 'true' } })).toBe(true)
  })
})

/* ===================== 渲染 ===================== */

describe('AgentProfileCards 渲染', () => {
  beforeEach(() => {
    // 逐用例断言 mock.calls[0]，必须先清空调用记录
    vi.clearAllMocks()
    setPerms(['customization:read', 'customization:update'])
    vi.mocked(apiService.getCustomizationMeta).mockResolvedValue({
      agents: [META, TREASURY], roles: [], document_types: [],
    } as never)
    vi.mocked(apiService.getAgentCustomizations).mockResolvedValue({ customizations: [] } as never)
    vi.mocked(apiService.putAgentCustomization).mockResolvedValue({ status: 'applied' } as never)
  })

  it('按元数据渲染域卡片、声明式参数与工具子集', async () => {
    render(<AgentProfileCards />)

    expect(await screen.findByText('报销')).toBeInTheDocument()
    expect(screen.getByText('资金')).toBeInTheDocument()
    expect(screen.getByText('强制附发票/收据')).toBeInTheDocument()
    expect(screen.getByText('跟随默认=按类别')).toBeInTheDocument()
    expect(screen.getByText('reimb_create')).toBeInTheDocument()
    // 无参数域不渲染参数区块，避免空表单
    expect(screen.getByText('工具子集（0/1）')).toBeInTheDocument()
  })

  it('未改动时保存不可用，改动后人设下发为 prompt_override', async () => {
    render(<AgentProfileCards />)
    const card = await cardOf('报销')
    expect((within(card).getByText('保存') as HTMLButtonElement).disabled).toBe(true)

    fireEvent.change(within(card).getByPlaceholderText(/严谨审核助手/), {
      target: { value: '只依据单据事实作答' },
    })
    await waitFor(() => expect((within(card).getByText('保存') as HTMLButtonElement).disabled).toBe(false))
    fireEvent.click(within(card).getByText('保存'))

    await waitFor(() => expect(apiService.putAgentCustomization).toHaveBeenCalled())
    expect(vi.mocked(apiService.putAgentCustomization).mock.calls[0]).toEqual([
      'agent', 'reimbursement',
      { overrides: { prompt_override: '只依据单据事实作答', params: { require_receipt: null } },
        enabled: true, comment: '管理端 Agent 卡片编辑' },
    ])
    expect(await screen.findByText(/已生效（2 项覆盖）/)).toBeInTheDocument()
  })

  it('agent 级定制不该走审批：后端返回 pending_approval 时如实报警', async () => {
    vi.mocked(apiService.putAgentCustomization).mockResolvedValue({ status: 'pending_approval' } as never)
    render(<AgentProfileCards />)
    const card = await cardOf('报销')
    fireEvent.change(within(card).getByPlaceholderText(/严谨审核助手/), {
      target: { value: '测试' },
    })
    fireEvent.click(within(card).getByText('保存'))

    expect(await screen.findByText(/不应发生/)).toBeInTheDocument()
  })

  it('已有定制时提供清除入口，清除下发空 overrides', async () => {
    vi.mocked(apiService.getAgentCustomizations).mockResolvedValue({
      customizations: [{
        type: 'agent', target: 'reimbursement', enabled: true,
        overrides: { prompt_override: '旧人设' },
      }],
    } as never)
    render(<AgentProfileCards />)

    fireEvent.click(await screen.findByText('清除定制'))
    await waitFor(() => expect(apiService.putAgentCustomization).toHaveBeenCalled())
    expect(vi.mocked(apiService.putAgentCustomization).mock.calls[0][2]).toEqual({
      overrides: {}, enabled: true, comment: '清除 Agent 定制',
    })
  })

  it('元数据失败时给出可见错误', async () => {
    vi.mocked(apiService.getCustomizationMeta).mockRejectedValue(
      Object.assign(new Error('boom'), { response: { data: { detail: '离线' } } }) as never)
    render(<AgentProfileCards />)

    expect(await screen.findByText('离线')).toBeInTheDocument()
  })
})
