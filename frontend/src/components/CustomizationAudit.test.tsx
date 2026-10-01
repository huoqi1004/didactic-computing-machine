/**
 * 定制审计视图测试（Phase F5.1）
 * =============================
 * 1. 纯函数：变更前后快照 → 逐键差异（启停轴单独成行）、状态语气、提案判定；
 * 2. 渲染：过滤条件按参数打到后端、待批行不能显示成已生效、
 *    审批人为空时区分"待批"与"收紧即时"、展开行给完整前后 JSON、
 *    加载失败给可见错误而不是空表（审计视图空表会被读成"没人改过")。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { useAuthStore } from '../stores/authStore'
import { apiService } from '../services/api'
import {
  CustomizationAudit,
  diffSummary,
  isProposal,
  showValue,
  statusTone,
} from './CustomizationAudit'

vi.mock('../services/api', () => ({
  apiService: {
    getCustomizationVersions: vi.fn(),
  },
}))

const ACTIVE = {
  id: 9, type: 'tool', target: 'treasury_fund_transfer_request', environment: 'prod',
  status: 'active', comment: '收紧：降到需人审',
  changed_by: 'user_cio', approved_by: null,
  created_at: '2026-09-30T10:00:00',
  old_summary: { enabled: true, overrides: { risk_level: 'write_high' } },
  new_summary: { enabled: true, overrides: { risk_level: 'write_low' } },
}
const PENDING = {
  id: 10, type: 'skill', target: 'sales_helper', environment: 'prod',
  status: 'pending_approval', comment: '放宽：放行外部工具',
  changed_by: 'user_cio', approved_by: null,
  created_at: '2026-09-30T11:00:00',
  old_summary: { enabled: true, overrides: { tools: ['crm_query'] } },
  new_summary: { enabled: true, overrides: { tools: ['crm_query', 'ext_weather'] } },
}
const TOGGLE = {
  id: 11, type: 'tool', target: 'order_create', environment: 'prod',
  status: 'active', comment: null, changed_by: 'user_cio', approved_by: null,
  created_at: '2026-09-30T12:00:00',
  old_summary: { enabled: true, overrides: { risk_level: 'write_high' } },
  new_summary: { enabled: false, overrides: { risk_level: 'write_high' } },
}

function setPerms(permissions: string[]) {
  useAuthStore.setState({
    token: 't', refreshToken: null, isAuthenticated: true, loading: false, error: null,
    user: { user_id: 'u_cio', username: 'cio_user', role_codes: ['cio'], permissions } as never,
  })
}

describe('CustomizationAudit 纯函数', () => {
  it('diffSummary 只列真正变化的键，新增与删除都给另一端占位', () => {
    expect(diffSummary(
      { enabled: true, overrides: { a: 1, b: 2 } },
      { enabled: true, overrides: { a: 1, c: [3] } },
    )).toEqual([
      { key: 'b', from: '2', to: '—' },
      { key: 'c', from: '—', to: '[3]' },
    ])
  })

  it('启停轴变化单独成行，且排在键值差异之前', () => {
    expect(diffSummary(
      { enabled: true, overrides: null },
      { enabled: false, overrides: { max_amount: 500 } },
    )).toEqual([
      { key: '启停', from: '启用', to: '停用' },
      { key: 'max_amount', from: '—', to: '500' },
    ])
  })

  it('旧快照为空时新增键就是变化（首条记录没有 overrides，不能吞掉不显示）', () => {
    expect(diffSummary(null, { enabled: true, overrides: { a: 1 } })).toEqual([
      { key: 'a', from: '—', to: '1' },
    ])
    expect(diffSummary({ enabled: true, overrides: null },
      { enabled: true, overrides: { a: 1 } })).toEqual([
      { key: 'a', from: '—', to: '1' },
    ])
    expect(diffSummary(undefined, undefined)).toEqual([])
  })

  it('showValue 对象走 JSON，undefined 占位，null 显式写出', () => {
    expect(showValue({ a: 1 })).toBe('{"a":1}')
    expect(showValue(undefined)).toBe('—')
    expect(showValue(null)).toBe('null')
  })

  it('statusTone 把待批标成警告，不把提案说成已生效', () => {
    expect(statusTone('pending_approval')).toBe('warn')
    expect(statusTone('rejected')).toBe('bad')
    expect(statusTone('rolled_back')).toBe('info')
    expect(statusTone('active')).toBe('ok')
    expect(statusTone('whatever')).toBe('mute')
    expect(isProposal('pending_approval')).toBe(true)
    expect(isProposal('approved_executed')).toBe(false)
  })
})

describe('CustomizationAudit 渲染', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(apiService.getCustomizationVersions).mockResolvedValue({
      versions: [TOGGLE, PENDING, ACTIVE],
    } as never)
    setPerms(['customization:read'])
  })

  it('每条记录给出时间/对象/旧值→新值/提出人，待批行审批人标"待批"', async () => {
    render(<CustomizationAudit />)
    await screen.findByText('tool:treasury_fund_transfer_request')
    // 状态筛选下拉里也有同名 option，表格内的状态徽标才是审计结论
    const table = screen.getByRole('table')

    expect(screen.getByText('2026-09-30 10:00:00')).toBeInTheDocument()
    expect(within(table).getByText('risk_level')).toBeInTheDocument()
    expect(within(table).getByText('write_high')).toBeInTheDocument()
    expect(within(table).getByText('write_low')).toBeInTheDocument()
    expect(screen.getByText(/共 3 条 · 待批 1 · 已生效 2/)).toBeInTheDocument()
    // 提案行不能读成"已生效"：状态与审批人两处都要留痕
    expect(within(table).getByText('pending_approval')).toBeInTheDocument()
    expect(within(table).getByText('待批')).toBeInTheDocument()
    // 收紧即时生效的行没有审批人，标"系统/即时"而不是空白（两条都是 active）
    expect(within(table).getAllByText('系统/即时')).toHaveLength(2)
  })

  it('启停变化显示成"启用 → 停用"，不是 true/false', async () => {
    render(<CustomizationAudit />)
    await screen.findByText('tool:order_create')
    expect(screen.getByText('启停')).toBeInTheDocument()
    expect(screen.getByText('启用')).toBeInTheDocument()
    expect(screen.getByText('停用')).toBeInTheDocument()
  })

  it('过滤条件按参数落到查询接口（不做前端截断，避免"看起来没有"）', async () => {
    render(<CustomizationAudit />)
    await screen.findByText('tool:order_create')

    fireEvent.change(screen.getByTestId('audit-type'), { target: { value: 'skill' } })
    await waitFor(() => expect(apiService.getCustomizationVersions)
      .toHaveBeenLastCalledWith('skill', undefined, 50, undefined))

    fireEvent.change(screen.getByTestId('audit-target'), { target: { value: ' sales_helper ' } })
    await waitFor(() => expect(apiService.getCustomizationVersions)
      .toHaveBeenLastCalledWith('skill', 'sales_helper', 50, undefined))

    fireEvent.change(screen.getByTestId('audit-status'), { target: { value: 'pending_approval' } })
    await waitFor(() => expect(apiService.getCustomizationVersions)
      .toHaveBeenLastCalledWith('skill', 'sales_helper', 50, 'pending_approval'))

    fireEvent.change(screen.getByTestId('audit-target'), { target: { value: '' } })
    await waitFor(() => expect(apiService.getCustomizationVersions)
      .toHaveBeenLastCalledWith('skill', undefined, 50, 'pending_approval'))
  })

  it('点行展开完整前后快照与备注', async () => {
    render(<CustomizationAudit />)
    fireEvent.click(await screen.findByText('skill:sales_helper'))

    const oldBox = await screen.findByTestId('audit-old')
    expect(oldBox.textContent).toContain('crm_query')
    expect(screen.getByTestId('audit-new').textContent).toContain('ext_weather')
    expect(screen.getByText(/放宽：放行外部工具/)).toBeInTheDocument()
    expect(screen.getByText(/环境：prod/)).toBeInTheDocument()
  })

  it('加载失败时给出可见错误，空表写明"查询失败"', async () => {
    vi.mocked(apiService.getCustomizationVersions).mockRejectedValue({
      response: { data: { detail: '变更历史查询失败' } },
    } as never)
    render(<CustomizationAudit />)

    expect(await screen.findByText('变更历史查询失败')).toBeInTheDocument()
    expect(screen.getByText('查询失败，未取到记录')).toBeInTheDocument()
  })

  it('没有记录时明确说没有，不留下空白面板', async () => {
    vi.mocked(apiService.getCustomizationVersions).mockResolvedValue({ versions: [] } as never)
    render(<CustomizationAudit />)

    expect(await screen.findByText('没有符合条件的变更记录')).toBeInTheDocument()
    expect(screen.getByText(/共 0 条/)).toBeInTheDocument()
  })
})
