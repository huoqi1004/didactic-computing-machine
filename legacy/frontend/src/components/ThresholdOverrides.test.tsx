/**
 * gate 阈值管理端入口测试（路线图 F5.6 UAT 补口）
 * ==============================================
 * 1. 纯函数与后端同口径：生效值（停用/非数字回落出厂默认）、方向判定（上调=放宽）、
 *    输入校验（非负有限数字）；
 * 2. 交互：payload 走 gate/<key> 且带 value、放宽回显"已提交人工审批"而不是"已生效"、
 *    停用覆盖的口径提示、无 customization:update 时写入口消失。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { useAuthStore } from '../stores/authStore'
import { apiService } from '../services/api'
import {
  ThresholdOverrides,
  classifyDirection,
  effectiveValue,
  fmtNum,
  parseThresholdValue,
  readThreshold,
} from './ThresholdOverrides'

vi.mock('../services/api', () => ({
  apiService: {
    getCustomizationMeta: vi.fn(),
    getAgentCustomizations: vi.fn(),
    putAgentCustomization: vi.fn(),
  },
}))

const METAS = [
  { key: 'reimbursement_auto_approve_limit', label: '报销免审线（元）', builtin_default: 5000 },
  { key: 'payment_human_gate_limit', label: '大额付款人审线（元）', builtin_default: 100000 },
]

function setPerms(permissions: string[]) {
  useAuthStore.setState({
    token: 't', refreshToken: null, loading: false, error: null, isAuthenticated: true,
    user: { user_id: 'u_cio', username: 'cio', role_codes: ['dept_staff'], permissions } as never,
  })
}

function card(label: string | RegExp) {
  return screen.getByText(label).closest('.rounded-xl') as HTMLElement
}

/* ===================== 纯函数（与后端 gate_effective_value 对齐） ===================== */

describe('ThresholdOverrides 生效值口径', () => {
  it('无条目=出厂默认；已停用=出厂默认但保留覆盖值用于显示', () => {
    expect(readThreshold(undefined)).toEqual({ enabled: true })
    expect(readThreshold({ enabled: false, overrides: { value: 1200 } }))
      .toEqual({ enabled: false, value: 1200 })
    expect(effectiveValue(readThreshold({ enabled: false, overrides: { value: 1200 } }), 5000))
      .toBe(5000)
  })

  it('value 非数字（后端会静默回退）→ 前端同样按出厂默认显示', () => {
    expect(readThreshold({ enabled: true, overrides: { value: '5000' } }).value).toBeUndefined()
    expect(effectiveValue(readThreshold({ enabled: true, overrides: { value: '5000' } }), 5000))
      .toBe(5000)
  })

  it('方向判定：上调=放宽、下调与持平=收紧', () => {
    expect(classifyDirection(5000, 6000)).toBe('relax')
    expect(classifyDirection(5000, 1200)).toBe('tighten')
    expect(classifyDirection(5000, 5000)).toBe('tighten')
  })

  it('输入校验与 apply_change 同口径：非负有限数字，空串/负数/非数字拒绝', () => {
    expect(parseThresholdValue('5000')).toBe(5000)
    expect(parseThresholdValue(' 1200.5 ')).toBe(1200.5)
    expect(parseThresholdValue('')).toBeNull()
    expect(parseThresholdValue('-1')).toBeNull()
    expect(parseThresholdValue('abc')).toBeNull()
  })

  it('fmtNum 千分位只加在整数部分', () => {
    expect(fmtNum(100000)).toBe('100,000')
    expect(fmtNum(1200.5)).toBe('1,200.5')
    expect(fmtNum(5000)).toBe('5,000')
    expect(fmtNum(500)).toBe('500')
  })
})

/* ===================== 组件行为 ===================== */

describe('ThresholdOverrides 管理端动线', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    setPerms(['customization:read', 'customization:update'])
    vi.mocked(apiService.getCustomizationMeta).mockResolvedValue({ thresholds: METAS } as never)
    vi.mocked(apiService.getAgentCustomizations).mockResolvedValue({
      customizations: [
        { type: 'gate', target: 'reimbursement_auto_approve_limit', enabled: true, overrides: { value: 1200 } },
      ],
    } as never)
    vi.mocked(apiService.putAgentCustomization).mockResolvedValue({ status: 'applied' } as never)
  })

  it('清单来自 /meta：已覆盖的阈值显示覆盖值与"已覆盖"，未覆盖的显示出厂默认', async () => {
    render(<ThresholdOverrides />)

    const reimb = await waitFor(() => card('报销免审线（元）'))
    expect(within(reimb).getByText('已覆盖')).toBeInTheDocument()
    expect(within(reimb).getAllByText('1,200')).toHaveLength(2)   // 当前生效 + 覆盖值
    expect(within(reimb).getByText('5,000')).toBeInTheDocument()  // 出厂默认

    const payment = card('大额付款人审线（元）')
    expect(within(payment).getByText('未覆盖（按出厂线）')).toBeInTheDocument()
    expect(within(payment).getAllByText('100,000')).toHaveLength(2)
  })

  it('上调＝放宽：保存前明示转人审，后端返回 pending_approval 时不得显示已生效', async () => {
    vi.mocked(apiService.putAgentCustomization).mockResolvedValue({
      status: 'pending_approval', reason: '放宽类变更需人工审批后生效',
    } as never)
    const onChanged = vi.fn()
    render(<ThresholdOverrides onChanged={onChanged} />)
    const reimb = await waitFor(() => card('报销免审线（元）'))

    fireEvent.change(within(reimb).getByRole('spinbutton'), { target: { value: '8000' } })
    expect(within(reimb).getByText(/放宽类变更，需人工审批后生效/)).toBeInTheDocument()

    fireEvent.click(within(reimb).getByText('保存'))
    await waitFor(() => expect(apiService.putAgentCustomization).toHaveBeenCalled())
    expect(vi.mocked(apiService.putAgentCustomization).mock.calls[0]).toEqual([
      'gate', 'reimbursement_auto_approve_limit',
      {
        overrides: { value: 8000 }, enabled: true,
        comment: '管理端阈值调整：报销免审线（元） 1,200→8,000',
      },
    ])
    expect(await screen.findByText(/已提交人工审批，批准前仍按 1,200 跑/)).toBeInTheDocument()
    // 真机踩到：子分区自己的 load() 只刷卡片，父级待审批队列必须靠回调才跟得上
    await waitFor(() => expect(onChanged).toHaveBeenCalled())
  })

  it('下调＝收紧：即时生效文案', async () => {
    vi.mocked(apiService.putAgentCustomization).mockResolvedValue({ status: 'applied' } as never)
    render(<ThresholdOverrides />)
    const payment = await waitFor(() => card('大额付款人审线（元）'))

    fireEvent.change(within(payment).getByRole('spinbutton'), { target: { value: '50000' } })
    expect(within(payment).getByText(/收紧，保存即生效/)).toBeInTheDocument()
    fireEvent.click(within(payment).getByText('保存'))

    await waitFor(() => expect(screen.getByText(/已即时生效（50,000）/)).toBeInTheDocument())
  })

  it('停用覆盖：生效值回出厂线，比出厂线更紧时后端判为放宽并转人审', async () => {
    vi.mocked(apiService.putAgentCustomization).mockResolvedValue({
      status: 'pending_approval',
    } as never)
    render(<ThresholdOverrides />)
    const reimb = await waitFor(() => card('报销免审线（元）'))

    fireEvent.click(within(reimb).getByText('停用覆盖'))
    await waitFor(() => expect(apiService.putAgentCustomization).toHaveBeenCalled())
    expect(vi.mocked(apiService.putAgentCustomization).mock.calls[0][2]).toMatchObject({
      overrides: { value: 1200 }, enabled: false,
    })
    expect(await screen.findByText(/停用后生效值升回出厂线 5,000，属放宽，已转人工审批/))
      .toBeInTheDocument()
  })

  it('停用中的条目如实标注，不显示成"没配过"', async () => {
    vi.mocked(apiService.getAgentCustomizations).mockResolvedValue({
      customizations: [
        { type: 'gate', target: 'reimbursement_auto_approve_limit', enabled: false, overrides: { value: 1200 } },
      ],
    } as never)
    render(<ThresholdOverrides />)

    const reimb = await waitFor(() => card('报销免审线（元）'))
    expect(within(reimb).getByText('已停用（回退出厂线）')).toBeInTheDocument()
    expect(within(reimb).getAllByText('5,000')).toHaveLength(2)   // 出厂默认 + 当前生效
    expect(within(reimb).getAllByText('1,200')).toHaveLength(1)   // 仅覆盖值
    expect(within(reimb).queryByText('停用覆盖')).not.toBeInTheDocument()
  })

  it('非法输入不发请求（前端预检与后端 400 同口径）', async () => {
    render(<ThresholdOverrides />)
    const reimb = await waitFor(() => card('报销免审线（元）'))

    fireEvent.change(within(reimb).getByRole('spinbutton'), { target: { value: '-1' } })
    expect(within(reimb).getByText('阈值必须是非负数字')).toBeInTheDocument()
    expect(within(reimb).getByText('保存').closest('button')).toBeDisabled()
    expect(apiService.putAgentCustomization).not.toHaveBeenCalled()
  })

  it('值未变化时不提供保存（避免制造空版本行）', async () => {
    render(<ThresholdOverrides />)
    const reimb = await waitFor(() => card('报销免审线（元）'))

    expect(within(reimb).getByRole('spinbutton')).toHaveValue(1200)
    expect(within(reimb).getByText('保存').closest('button')).toBeDisabled()
  })

  it('无 customization:update 权限：只读显示生效值，写入口消失', async () => {
    setPerms(['customization:read'])
    render(<ThresholdOverrides />)

    const reimb = await waitFor(() => card('报销免审线（元）'))
    expect(within(reimb).getByText('需 customization:update 权限')).toBeInTheDocument()
    expect(within(reimb).queryByRole('spinbutton')).not.toBeInTheDocument()
    expect(within(reimb).getAllByText('1,200')).toHaveLength(2)
    expect(within(reimb).queryByText('保存')).not.toBeInTheDocument()
  })
  it('/meta 没下发阈值清单时不手工造键（防死键）', async () => {
    vi.mocked(apiService.getCustomizationMeta).mockResolvedValue({} as never)
    render(<ThresholdOverrides />)

    expect(await screen.findByText(/此处不提供手工输入/)).toBeInTheDocument()
  })
})
