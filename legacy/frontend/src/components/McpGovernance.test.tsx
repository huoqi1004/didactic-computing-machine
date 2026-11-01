/**
 * MCP 治理台测试（Phase F4.6）
 * ============================
 * 1. 纯函数：探测结果归一、勾选差异判定（扩面须批/收缩即时）、
 *    幂等键由动作语义派生、后端 status → 界面结论文案；
 * 2. 渲染：入口表按 last_probe 出连接测试徽标、待审批条数提示、
 *    动作按钮按 customization:update 权限出现/隐藏、SSRF 拒绝原因必须显式报错、
 *    出口域开关把"开放"翻译成转人审而非已生效。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { useAuthStore } from '../stores/authStore'
import { apiService } from '../services/api'
import {
  McpGovernance,
  changeNotice,
  diffTools,
  idemKey,
  probeOf,
} from './McpGovernance'

vi.mock('../services/api', () => ({
  apiService: {
    getMcpServers: vi.fn(),
    createMcpServer: vi.fn(),
    updateMcpServer: vi.fn(),
    deleteMcpServer: vi.fn(),
    probeMcpUrl: vi.fn(),
    probeMcpServer: vi.fn(),
    getMcpServerTools: vi.fn(),
    setMcpServerTools: vi.fn(),
    getMcpExposure: vi.fn(),
    setMcpDomainExposure: vi.fn(),
    getAgentApprovals: vi.fn(),
  },
}))

interface Srv {
  name: string
  url: string
  description: string
  last_probe: Record<string, unknown> | null
  exposed_tools: string[]
  exposure_enabled: boolean
}

const CRM: Srv = {
  name: 'crm',
  url: 'https://mcp.crm.example.com/mcp',
  description: 'CRM 外部能力',
  last_probe: { ok: true, tools: ['crm_query', 'crm_list'], at: '2026-09-30T10:00:00' },
  exposed_tools: ['crm_query'],
  exposure_enabled: true,
}

const SINK: Srv = {
  name: 'hr_sink',
  url: 'https://mcp.hr.example.com/mcp',
  description: '',
  last_probe: { ok: false, error: '连接超时', at: '2026-09-29T09:00:00' },
  exposed_tools: [],
  exposure_enabled: false,
}

const DOMAINS = [
  { domain: 'sales', label: '销售', exposed: true, customized: true, tool_total: 5, tool_exposed: 3 },
  { domain: 'treasury', label: '资金', exposed: false, customized: false, tool_total: 4, tool_exposed: 0 },
]

function setPerms(permissions: string[]) {
  useAuthStore.setState({
    token: 't',
    refreshToken: null,
    isAuthenticated: true,
    loading: false,
    error: null,
    user: {
      user_id: 'u_dept', username: 'dept_staff_user', role_codes: ['dept_staff'],
      permissions,
    } as never,
  })
}

/* ===================== 纯函数 ===================== */

describe('McpGovernance 纯函数', () => {
  it('probeOf 归一缺省与非法字段（不出现"绿勾但 0 工具"的误读）', () => {
    expect(probeOf(SINK)).toEqual({ ok: false, tools: [], at: '2026-09-29T09:00:00', error: '连接超时' })
    expect(probeOf({ ...CRM, last_probe: null })).toEqual({ ok: false, tools: [], at: '', error: '' })
    expect(probeOf({ ...CRM, last_probe: { ok: true, tools: 'crm_query' } }).tools).toEqual([])
    expect(probeOf(CRM).tools).toEqual(['crm_query', 'crm_list'])
  })

  it('diffTools 分离扩面与收缩：扩面非空即为放宽类，须人审', () => {
    expect(diffTools(['a'], ['a', 'b'])).toEqual({ added: ['b'], removed: [] })
    expect(diffTools(['a', 'b'], ['a'])).toEqual({ added: [], removed: ['b'] })
    expect(diffTools(['a'], ['b'])).toEqual({ added: ['b'], removed: ['a'] })
    expect(diffTools(['a'], ['a'])).toEqual({ added: [], removed: [] })
  })

  it('idemKey 由动作语义派生：同一目标状态重复点击落同一键，改状态即换键', () => {
    expect(idemKey('mcp-tools', 'crm', 'a,b')).toBe('mcp-tools:crm:a,b')
    expect(idemKey('mcp-tools', 'crm', 'a,b')).toBe(idemKey('mcp-tools', 'crm', 'a,b'))
    expect(idemKey('mcp-exp', 'sales', 'true')).not.toBe(idemKey('mcp-exp', 'sales', 'false'))
  })

  it('changeNotice 把放宽类说成"待审批"，绝不显示成已生效', () => {
    expect(changeNotice({ status: 'pending_approval' })).toMatch(/已提交人工审批/)
    expect(changeNotice({ status: 'pending_approval' })).toMatch(/当前运行时配置未改动/)
    expect(changeNotice({ status: 'applied' })).toBe('收紧类变更，已即时生效。')
    expect(changeNotice({ status: 'duplicate' })).toMatch(/幂等键/)
    expect(changeNotice({ status: 'rejected' })).toMatch(/驳回/)
    expect(changeNotice({ reply: '自定义结论' })).toBe('自定义结论')
    expect(changeNotice(null)).toBe('已完成。')
  })
})

/* ===================== 入口分区 ===================== */

describe('McpGovernance 外部 MCP 接入', () => {
  beforeEach(() => {
    // 只清调用记录，不清用例自己设的 resolved value
    vi.clearAllMocks()
    vi.mocked(apiService.getMcpServers).mockResolvedValue({ servers: [CRM, SINK] } as never)
    vi.mocked(apiService.getMcpExposure).mockResolvedValue({ domains: DOMAINS } as never)
    vi.mocked(apiService.getAgentApprovals).mockResolvedValue({
      approvals: [
        { thread_id: 't1', domain: 'customization', tool: 'mcp:crm', preview: '扩面', requested_by: 'u1', created_at: '' },
        { thread_id: 't2', domain: 'customization', tool: 'mcp_export:sales', preview: '开放', requested_by: 'u1', created_at: '' },
        { thread_id: 't3', domain: 'customization', tool: 'gate:approval_route_matrix', preview: '矩阵', requested_by: 'u1', created_at: '' },
      ],
    } as never)
    vi.mocked(apiService.getMcpServerTools).mockResolvedValue({ tools: ['crm_query', 'crm_list'] } as never)
  })

  it('按 last_probe 渲染连接测试徽标与生效中的暴露工具', async () => {
    setPerms(['customization:read', 'customization:update'])
    render(<McpGovernance />)

    expect(await screen.findByText('https://mcp.crm.example.com/mcp')).toBeInTheDocument()
    expect(screen.getByText('OK · 2 工具')).toBeInTheDocument()
    expect(screen.getByText('失败 · 连接超时')).toBeInTheDocument()
    expect(screen.getByText('crm_query')).toBeInTheDocument()
    // 未暴露任何工具时不能留空，要让管理员看出"登记 ≠ 暴露"
    expect(screen.getByText('未暴露')).toBeInTheDocument()
  })

  it('待审批提示只统计 MCP 相关变更（矩阵类不计入）', async () => {
    setPerms(['customization:read'])
    render(<McpGovernance />)
    expect(await screen.findByText(/有 1 条 MCP 暴露变更正在等待人工审批/)).toBeInTheDocument()
  })

  it('无 update 权限时登记表单与动作按钮都不出现', async () => {
    setPerms(['customization:read'])
    render(<McpGovernance />)
    await screen.findByText('https://mcp.crm.example.com/mcp')
    expect(screen.queryByText('登记外部 MCP Server')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '勾选工具' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '删除' })).not.toBeInTheDocument()
  })

  it('登记被后端拒绝时把 SSRF 原因显式报错（不静默降级）', async () => {
    vi.mocked(apiService.createMcpServer).mockRejectedValue({
      response: { data: { detail: 'URL 命中 SSRF 拦截：解析到内网地址 10.0.0.5' } },
    } as never)
    setPerms(['customization:update'])
    render(<McpGovernance />)

    fireEvent.change(screen.getByTestId('mcp-name'), { target: { value: 'intranet' } })
    fireEvent.change(screen.getByTestId('mcp-url'), { target: { value: 'http://10.0.0.5/mcp' } })
    fireEvent.click(screen.getByRole('button', { name: '登记' }))

    expect(await screen.findByText(/SSRF 拦截/)).toBeInTheDocument()
    expect(screen.queryByText('已登记（未暴露任何工具）。')).not.toBeInTheDocument()
  })

  it('勾选工具：扩面提示先行，提交带幂等键并把结论说成待审批', async () => {
    vi.mocked(apiService.setMcpServerTools).mockResolvedValue({ status: 'pending_approval' } as never)
    setPerms(['customization:update'])
    render(<McpGovernance />)

    fireEvent.click((await screen.findAllByRole('button', { name: '勾选工具' }))[0])
    expect(await screen.findByRole('checkbox', { name: 'crm_list' })).not.toBeChecked()
    expect(screen.getByRole('checkbox', { name: 'crm_query' })).toBeChecked()

    fireEvent.click(screen.getByRole('checkbox', { name: 'crm_list' }))
    expect(screen.getByText(/扩面 \+1 个工具/)).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '提交' }))
    await waitFor(() => expect(apiService.setMcpServerTools).toHaveBeenCalled())
    expect(apiService.setMcpServerTools).toHaveBeenCalledWith(
      'crm', ['crm_query', 'crm_list'], '', 'mcp-tools:crm:crm_list,crm_query')
    expect(await screen.findByText(/已提交人工审批/)).toBeInTheDocument()
  })

  it('纯收缩勾选走即时生效口径', async () => {
    vi.mocked(apiService.setMcpServerTools).mockResolvedValue({ status: 'applied' } as never)
    setPerms(['customization:update'])
    render(<McpGovernance />)

    fireEvent.click((await screen.findAllByRole('button', { name: '勾选工具' }))[0])
    fireEvent.click(await screen.findByRole('checkbox', { name: 'crm_query' }))
    expect(screen.getByText(/收缩 −1 个工具（crm_query）：属收紧类，即时生效/)).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '提交' }))
    expect(await screen.findByText('收紧类变更，已即时生效。')).toBeInTheDocument()
    expect(apiService.setMcpServerTools).toHaveBeenCalledWith(
      'crm', [], '', 'mcp-tools:crm:')
  })

  it('探测结果为空时指引先做连接测试，而不是让管理员空勾选', async () => {
    vi.mocked(apiService.getMcpServerTools).mockResolvedValue({ tools: [] } as never)
    setPerms(['customization:update'])
    render(<McpGovernance />)

    fireEvent.click((await screen.findAllByRole('button', { name: '勾选工具' }))[0])
    expect(await screen.findByText(/最近一次探测没有工具清单/)).toBeInTheDocument()
  })
})

/* ===================== 出口分区 ===================== */

describe('McpGovernance 对外出口', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(apiService.getMcpServers).mockResolvedValue({ servers: [] } as never)
    vi.mocked(apiService.getMcpExposure).mockResolvedValue({ domains: DOMAINS } as never)
    vi.mocked(apiService.getAgentApprovals).mockResolvedValue({ approvals: [] } as never)
  })

  it('域卡片显示出口工具计数与默认/已定制来源', async () => {
    setPerms(['customization:read'])
    render(<McpGovernance />)

    fireEvent.click(await screen.findByText('MCP 对外出口'))
    expect(await screen.findByText(/出口工具 3\/5/)).toBeInTheDocument()
    expect(screen.getByText(/出口工具 0\/4/)).toBeInTheDocument()
    expect(screen.getByText('默认')).toBeInTheDocument()
    expect(screen.getByText('已开放')).toBeInTheDocument()
    expect(screen.getByText('已关闭')).toBeInTheDocument()
  })

  it('开放未暴露域 = 放宽，转人审；关闭已暴露域 = 收紧，即时', async () => {
    vi.mocked(apiService.setMcpDomainExposure)
      .mockResolvedValueOnce({ status: 'pending_approval' } as never)
      .mockResolvedValueOnce({ status: 'applied' } as never)
    setPerms(['customization:update'])
    render(<McpGovernance />)

    fireEvent.click(await screen.findByText('MCP 对外出口'))
    fireEvent.click(await screen.findByRole('button', { name: '开放该域出口（转人工审批）' }))
    expect(apiService.setMcpDomainExposure).toHaveBeenCalledWith(
      'treasury', true, '管理端开放 MCP 出口域 资金', 'mcp-exp:treasury:true')
    expect(await screen.findByText(/已提交人工审批/)).toBeInTheDocument()

    fireEvent.click(await screen.findByRole('button', { name: '关闭该域出口（即时生效）' }))
    expect(apiService.setMcpDomainExposure).toHaveBeenCalledWith(
      'sales', false, '管理端关闭 MCP 出口域 销售', 'mcp-exp:sales:false')
    expect(await screen.findByText(/销售：收紧类变更，已即时生效/)).toBeInTheDocument()
  })

  it('重复点击被幂等键拦下时给出可读结论', async () => {
    vi.mocked(apiService.setMcpDomainExposure).mockResolvedValue({ status: 'duplicate' } as never)
    setPerms(['customization:update'])
    render(<McpGovernance />)

    fireEvent.click(await screen.findByText('MCP 对外出口'))
    fireEvent.click(await screen.findByRole('button', { name: '关闭该域出口（即时生效）' }))
    expect(await screen.findByText(/重复请求已被幂等键拦下/)).toBeInTheDocument()
  })

  it('无 update 权限时出口开关只读（只能看状态）', async () => {
    setPerms(['customization:read'])
    render(<McpGovernance />)

    fireEvent.click(await screen.findByText('MCP 对外出口'))
    await screen.findByText(/出口工具 3\/5/)
    expect(screen.queryByRole('button', { name: /该域出口/ })).not.toBeInTheDocument()
  })
})
