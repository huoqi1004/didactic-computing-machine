/**
 * Agent 定制中心（父级）测试（Phase F1.7 + F2.7）
 * ==============================================
 * 关注三件跨分区的事：
 *   1. 放宽类变更的审批闭环：待审批来自 /approvals（domain=customization），
 *      版本流里根本没有 pending 行——两处混用会让管理员以为没提交；
 *   2. 分区切换按需拉取元数据；
 *   3. 变更历史的"撤销此次变更"语义与权限门（只有已生效版本可撤销）。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { useAuthStore } from '../stores/authStore'
import { apiService } from '../services/api'
import { AgentCustomizationCenter } from './AgentCustomizationCenter'

vi.mock('../services/api', () => ({
  apiService: {
    getAgentTools: vi.fn(),
    getAgentCustomizations: vi.fn(),
    getCustomizationVersions: vi.fn(),
    getCustomizationMeta: vi.fn(),
    getAgentApprovals: vi.fn(),
    putAgentCustomization: vi.fn(),
    rollbackCustomization: vi.fn(),
    decideAgentApproval: vi.fn(),
  },
}))

const TOOLS = [
  {
    name: 'reimb_submit', description: '提交报销单', risk_level: 'write_low',
    permission: 'reimbursement:create', domain: 'reimbursement', has_compensation: true,
  },
  {
    name: 'reimb_query', description: '查询报销单', risk_level: 'readonly',
    permission: 'reimbursement:read', domain: 'reimbursement', has_compensation: false,
  },
]

const PENDING = [
  {
    thread_id: 'th-cust-1', domain: 'customization', agent_key: 'customization',
    tool: 'tool:reimb_submit', preview: '定制变更（放宽类）：tool:reimb_submit {"risk_override":"readonly"}',
    requested_by: 'u_cio', created_at: '2026-09-30T08:00:00',
  },
  {
    thread_id: 'th-cust-2', domain: 'customization', agent_key: 'customization',
    tool: 'gate:approval_route_matrix', preview: '定制变更（放宽类）：gate:approval_route_matrix {"rules":[]}',
    requested_by: 'u_cio', created_at: '2026-09-30T08:10:00',
  },
  {
    // 非定制域的人审任务（write_high 工具执行）：不得出现在定制中心队列里
    thread_id: 'th-tool-3', domain: 'reimbursement', agent_key: 'reimbursement',
    tool: 'reimb_submit', preview: 'write_high 工具执行审批',
    requested_by: 'u_cio', created_at: '2026-09-30T08:30:00',
  },
]

function setPerms(permissions: string[]) {
  useAuthStore.setState({
    token: 't', refreshToken: null, loading: false, error: null, isAuthenticated: true,
    user: { user_id: 'u_cio', username: 'cio', role_codes: ['dept_staff'], permissions } as never,
  })
}

describe('AgentCustomizationCenter', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    setPerms(['customization:read', 'customization:update', 'agent:read', 'agent:approve'])
    vi.mocked(apiService.getAgentTools).mockResolvedValue({ tools: TOOLS } as never)
    vi.mocked(apiService.getAgentCustomizations).mockResolvedValue({
      customizations: [{
        type: 'tool', target: 'reimb_submit', enabled: true,
        overrides: { risk_override: 'write_high' },
      }],
    } as never)
    vi.mocked(apiService.getCustomizationVersions).mockResolvedValue({
      versions: [
        { id: 3, type: 'tool', target: 'reimb_submit', overrides: null, status: 'active', changed_by: 'u_cio', created_at: '2026-09-30T07:00:00' },
        { id: 2, type: 'gate', target: 'approval_route_matrix', overrides: null, status: 'rejected', changed_by: 'u_cio', created_at: '2026-09-29T07:00:00' },
      ],
    } as never)
    vi.mocked(apiService.getAgentApprovals).mockResolvedValue({ approvals: PENDING } as never)
    vi.mocked(apiService.putAgentCustomization).mockResolvedValue({ status: 'applied' } as never)
    vi.mocked(apiService.rollbackCustomization).mockResolvedValue({ status: 'applied', reason: '已回滚到版本 3' } as never)
    vi.mocked(apiService.decideAgentApproval).mockResolvedValue({ status: 'executed' } as never)
    vi.mocked(apiService.getCustomizationMeta).mockResolvedValue({
      agents: [{ domain: 'reimbursement', label: '报销', tools: ['reimb_submit'], params: [] }],
      roles: [{ code: 'cfo', name: '财务总监' }],
      document_types: ['reimb_submit'],
      thresholds: [
        { key: 'reimbursement_auto_approve_limit', label: '报销免审线（元）', builtin_default: 5000 },
      ],
    } as never)
  })

  it('默认分区是工具治理，已定制工具带覆盖标记', async () => {
    render(<AgentCustomizationCenter />)

    expect(await screen.findByText(/收紧即时生效 · 放宽需审批/)).toBeInTheDocument()
    const card = (screen.getByText('reimb_submit')).closest('.rounded-xl') as HTMLElement
    expect(within(card).getByText('已定制')).toBeInTheDocument()
    expect(within(card).getByRole('combobox')).toHaveValue('write_high')
    expect(within(card).getByText('停用')).toBeInTheDocument()
  })

  it('风险降级为放宽：后端返回 pending_approval 时不得显示已生效', async () => {
    vi.mocked(apiService.putAgentCustomization).mockResolvedValue({
      status: 'pending_approval', reason: '放宽类变更需人工审批后生效',
    } as never)
    render(<AgentCustomizationCenter />)
    const card = (await screen.findByText('reimb_submit')).closest('.rounded-xl') as HTMLElement
    fireEvent.change(within(card).getByRole('combobox'), { target: { value: 'readonly' } })

    await waitFor(() => expect(apiService.putAgentCustomization).toHaveBeenCalled())
    expect(vi.mocked(apiService.putAgentCustomization).mock.calls[0]).toEqual([
      'tool', 'reimb_submit',
      { overrides: { risk_override: 'readonly' }, enabled: true },
    ])
    expect(await screen.findByText(/已提交人工审批，批准后生效/)).toBeInTheDocument()
  })

  it('待审批队列只收定制变更，批准后如实回显', async () => {
    render(<AgentCustomizationCenter />)

    expect(await screen.findByText('待审批的定制变更（2）')).toBeInTheDocument()
    expect(screen.queryByText('write_high 工具执行审批')).not.toBeInTheDocument()

    // 队列顺序即后端 created_at 倒序，首行为 tool:reimb_submit
    fireEvent.click(screen.getAllByText('批准')[0])
    await waitFor(() => expect(apiService.decideAgentApproval).toHaveBeenCalled())
    expect(vi.mocked(apiService.decideAgentApproval).mock.calls[0][0]).toBe('th-cust-1')
    expect(vi.mocked(apiService.decideAgentApproval).mock.calls[0][1]).toBe(true)
    expect(await screen.findByText(/tool:reimb_submit：已批准并生效/)).toBeInTheDocument()
  })

  it('驳回走同一决定入口，未生效原因如实显示', async () => {
    render(<AgentCustomizationCenter />)
    await screen.findByText('待审批的定制变更（2）')

    fireEvent.click(screen.getAllByText('驳回')[1])
    await waitFor(() => expect(apiService.decideAgentApproval).toHaveBeenCalled())
    expect(vi.mocked(apiService.decideAgentApproval).mock.calls[0][0]).toBe('th-cust-2')
    expect(vi.mocked(apiService.decideAgentApproval).mock.calls[0][1]).toBe(false)
  })

  it('缺少 agent:approve 时队列可见但决定入口被权限挡住', async () => {
    setPerms(['customization:read'])
    render(<AgentCustomizationCenter />)

    expect(await screen.findByText('待审批的定制变更（2）')).toBeInTheDocument()
    expect(screen.queryByText('批准')).not.toBeInTheDocument()
    expect(screen.getAllByText('需 agent:approve 权限')).toHaveLength(2)
  })

  it('切换分区才拉取表单元数据', async () => {
    render(<AgentCustomizationCenter />)
    await screen.findByText('reimb_submit')
    expect(apiService.getCustomizationMeta).not.toHaveBeenCalled()

    fireEvent.click(screen.getByText('Agent 人设与参数'))
    await waitFor(() => expect(apiService.getCustomizationMeta).toHaveBeenCalled())
    expect(await screen.findByText('人设模板 prompt_override（留空=用内置场景提示词）')).toBeInTheDocument()

    fireEvent.click(screen.getByText('审批路由矩阵'))
    expect(await screen.findByText('新增规则')).toBeInTheDocument()
    expect(screen.getByText('1 条矩阵变更待审批')).toBeInTheDocument()
  })

  it('风控阈值分区：清单取自 /meta，待审批计数不与矩阵互冒', async () => {
    vi.mocked(apiService.getAgentApprovals).mockResolvedValue({
      approvals: [
        ...PENDING,
        {
          thread_id: 'th-cust-3', domain: 'customization', agent_key: 'customization',
          tool: 'gate:reimbursement_auto_approve_limit',
          preview: '定制变更（放宽类）：gate:reimbursement_auto_approve_limit {"value":8000}',
          requested_by: 'u_cio', created_at: '2026-09-30T08:20:00',
        },
      ],
    } as never)
    render(<AgentCustomizationCenter />)
    await screen.findByText('reimb_submit')

    fireEvent.click(screen.getByText('风控阈值'))
    expect(await screen.findByText('报销免审线（元）')).toBeInTheDocument()
    expect(screen.getByText('reimbursement_auto_approve_limit')).toBeInTheDocument()
    expect(screen.getByText('1 条阈值变更待审批')).toBeInTheDocument()

    fireEvent.click(screen.getByText('审批路由矩阵'))
    expect(await screen.findByText('1 条矩阵变更待审批')).toBeInTheDocument()
  })

  it('阈值保存后父级待审批队列立刻反映（真机踩到：子分区只刷自己，队列停在保存前计数）', async () => {
    const withThreshold = [...PENDING, {
      thread_id: 'th-cust-3', domain: 'customization', agent_key: 'customization',
      tool: 'gate:reimbursement_auto_approve_limit',
      preview: '定制变更（放宽类）：gate:reimbursement_auto_approve_limit {"value":8000}',
      requested_by: 'u_cio', created_at: '2026-09-30T08:20:00',
    }]
    vi.mocked(apiService.getAgentApprovals)
      .mockResolvedValueOnce({ approvals: PENDING } as never)
      .mockResolvedValue({ approvals: withThreshold } as never)
    vi.mocked(apiService.putAgentCustomization).mockResolvedValue({
      status: 'pending_approval',
    } as never)
    render(<AgentCustomizationCenter />)
    await screen.findByText('待审批的定制变更（2）')

    fireEvent.click(screen.getByText('风控阈值'))
    const box = (await screen.findByText('报销免审线（元）')).closest('.rounded-xl') as HTMLElement
    fireEvent.change(within(box).getByRole('spinbutton'), { target: { value: '8000' } })
    fireEvent.click(within(box).getByText('保存'))

    expect(await screen.findByText('待审批的定制变更（3）')).toBeInTheDocument()
    expect(screen.getByText('1 条阈值变更待审批')).toBeInTheDocument()
  })

  it('变更历史仅已生效版本提供撤销', async () => {
    render(<AgentCustomizationCenter />)
    fireEvent.click(await screen.findByText('变更历史（2）'))

    expect(screen.getByText('撤销此次变更')).toBeInTheDocument()
    expect(screen.getAllByText('rejected')).toHaveLength(1)

    fireEvent.click(screen.getByText('撤销此次变更'))
    await waitFor(() => expect(apiService.rollbackCustomization).toHaveBeenCalled())
    expect(vi.mocked(apiService.rollbackCustomization).mock.calls[0])
      .toEqual(['tool', 'reimb_submit', 3])
    expect(await screen.findByText(/tool:reimb_submit：已回滚到版本 3/)).toBeInTheDocument()
  })

  it('无 update 权限时工具写入口与撤销入口全部消失（后端仍是权威）', async () => {
    setPerms(['customization:read'])
    render(<AgentCustomizationCenter />)
    await screen.findByText('reimb_submit')

    expect(screen.queryByRole('combobox')).not.toBeInTheDocument()
    expect(screen.queryByText('停用')).not.toBeInTheDocument()

    fireEvent.click(await screen.findByText('变更历史（2）'))

    expect(screen.queryByText('撤销此次变更')).not.toBeInTheDocument()
    expect(screen.getByText('需 customization:update')).toBeInTheDocument()
  })
})
