/**
 * 工作流设计器测试（Phase F / F3.6）
 * ===================================
 * 1. 纯函数：路由抽取、分层预览（含未连通与成环草稿不死循环）、
 *    条件值序列化、节点增删改与版本号推进；
 * 2. 渲染：元数据驱动载入出厂模板、发布动作按 customization:publish 权限出现/隐藏、
 *    校验请求以当前定义调用后端同一校验器。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { useAuthStore } from '../stores/authStore'
import { apiService } from '../services/api'
import {
  layoutLayers,
  newNode,
  nextVersion,
  nodeRoutes,
  parseBranchValue,
  parseRoles,
  serializeBranchValue,
  withoutNode,
  withNode,
  WorkflowDesigner,
  type WfDefinition,
} from './WorkflowDesigner'

vi.mock('../services/api', () => ({
  apiService: {
    getWorkflowDesignerMeta: vi.fn(),
    getWorkflowStatus: vi.fn(),
    getWorkflowDefinition: vi.fn(),
    validateWorkflowDefinition: vi.fn(),
    saveWorkflowDraft: vi.fn(),
    requestWorkflowPublish: vi.fn(),
    rollbackWorkflow: vi.fn(),
  },
}))

const OPS = ['equals', 'not_equals', 'in', 'gt', 'gte', 'lt', 'lte', 'exists']

/** 出厂模板：submit → gate（阈值+角色）→ verify / reject，4 节点 */
const BUILTIN: WfDefinition = {
  name: 'reimbursement_approval',
  entry: 'submit',
  nodes: [
    { id: 'submit', type: 'task', handler: 'reimb_submit', next: 'gate' },
    {
      id: 'gate', type: 'gate',
      amount_from: 'result.amount',
      threshold: { key: 'reimbursement_auto_approve_limit', default: 5000 },
      approve_roles: ['fin_manager'],
      next_approved: 'verify', next_rejected: 'reject',
    },
    { id: 'verify', type: 'task', handler: 'reimb_precheck', next: 'END' },
    { id: 'reject', type: 'task', handler: 'reimb_precheck', next: 'END' },
  ],
}

const META = {
  node_types: ['task', 'condition', 'gate'],
  condition_ops: OPS,
  handlers: ['reimb_submit', 'reimb_precheck'],
  threshold_keys: ['reimbursement_auto_approve_limit'],
  workflows: [{ name: 'reimbursement_approval', description: '报销审批流', input_hint: '{}' }],
  builtin_dsl: { reimbursement_approval: BUILTIN },
}

/* ===================== 纯函数 ===================== */

describe('WorkflowDesigner 纯函数', () => {
  it('nodeRoutes 抽取三种节点的出边', () => {
    expect(nodeRoutes({ id: 'a', type: 'task', next: 'b' })).toEqual([{ label: 'next', target: 'b' }])
    expect(nodeRoutes({ id: 'g', type: 'gate', next_approved: 'ok', next_rejected: 'no' }))
      .toEqual([{ label: '批准', target: 'ok' }, { label: '驳回', target: 'no' }])
    expect(nodeRoutes({
      id: 'c', type: 'condition', field: 'input.x',
      branches: [{ equals: 'y', goto: 'g' }], default: 'h',
    })).toEqual([
      { label: '分支1', target: 'g' },
      { label: 'default', target: 'h' },
    ])
  })

  it('缺失出边目标用占位符展示（草稿半成品不崩渲染）', () => {
    expect(nodeRoutes({ id: 'a', type: 'task' })).toEqual([{ label: 'next', target: '—' }])
    expect(nodeRoutes({ id: 'g', type: 'gate' })).toEqual([
      { label: '批准', target: '—' }, { label: '驳回', target: '—' },
    ])
    expect(nodeRoutes({ id: 'c', type: 'condition' })).toEqual([{ label: 'default', target: 'END' }])
  })

  it('layoutLayers 线性分层并把未连通节点单列一层', () => {
    const defn: WfDefinition = {
      name: 'x', entry: 'a',
      nodes: [
        { id: 'a', type: 'task', next: 'b' },
        { id: 'b', type: 'gate', next_approved: 'c', next_rejected: 'END' },
        { id: 'c', type: 'task', next: 'END' },
        { id: 'orphan', type: 'task', next: 'END' },
      ],
    }
    const layers = layoutLayers(defn)
    expect(layers.map((l) => l.nodes.map((n) => n.id))).toEqual([['a'], ['b'], ['c'], ['orphan']])
    expect(layers[3].unreachable).toBe(true)
    expect(layers.slice(0, 3).map((l) => l.unreachable)).toEqual([false, false, false])
  })

  it('layoutLayers 取最长路径：分支汇合点排在最深一层', () => {
    const defn: WfDefinition = {
      name: 'x', entry: 'a',
      nodes: [
        { id: 'a', type: 'condition', field: 'input.f',
          branches: [{ equals: 'y', goto: 'long' }], default: 'join' },
        { id: 'long', type: 'task', next: 'join' },
        { id: 'join', type: 'task', next: 'END' },
      ],
    }
    expect(layoutLayers(defn).map((l) => l.depth)).toEqual([0, 1, 2])
    expect(layoutLayers(defn)[2].nodes[0].id).toBe('join')
  })

  it('layoutLayers 对成环草稿收敛（预览不挂死）', () => {
    const defn: WfDefinition = {
      name: 'x', entry: 'a',
      nodes: [
        { id: 'a', type: 'task', next: 'b' },
        { id: 'b', type: 'task', next: 'a' },
      ],
    }
    const layers = layoutLayers(defn)
    expect(layers.length).toBeGreaterThan(0)
    expect(layers.flatMap((l) => l.nodes.map((n) => n.id))).toEqual(expect.arrayContaining(['a', 'b']))
  })

  it('serializeBranchValue / parseBranchValue 按算子类型转换', () => {
    expect(serializeBranchValue('gt', '5000')).toBe(5000)
    expect(serializeBranchValue('gt', '')).toBe('')
    expect(serializeBranchValue('in', 'a, b，c')).toEqual(['a', 'b', 'c'])
    expect(serializeBranchValue('exists', 'false')).toBe(false)
    expect(serializeBranchValue('exists', 'true')).toBe(true)
    expect(serializeBranchValue('equals', 'entertainment')).toBe('entertainment')
    expect(parseBranchValue('in', ['a', 'b'])).toBe('a, b')
    expect(parseBranchValue('exists', false)).toBe('false')
    expect(parseBranchValue('equals', undefined)).toBe('')
  })

  it('parseRoles 支持中英文逗号与空格，空串得空数组', () => {
    expect(parseRoles('cfo， fin_manager legal_manager')).toEqual(['cfo', 'fin_manager', 'legal_manager'])
    expect(parseRoles('  ')).toEqual([])
  })

  it('newNode 各类型自带治理必填骨架', () => {
    const gate = newNode('gate', 3)
    expect(gate.id).toBe('node_3')
    // 红线 1 兜底：gate 节点新建即带批准/驳回双出边，无需管理员补
    expect(gate.next_approved).toBe('END')
    expect(gate.next_rejected).toBe('END')
    const cond = newNode('condition', 1)
    expect(cond.default).toBe('END')
    expect(Array.isArray(cond.branches)).toBe(true)
    expect(newNode('task', 1)).toMatchObject({ type: 'task', handler: '', next: 'END' })
  })

  it('withNode 改名同步 entry，withoutNode 保序删除', () => {
    const defn: WfDefinition = {
      name: 'x', entry: 'a',
      nodes: [{ id: 'a', type: 'task', next: 'b' }, { id: 'b', type: 'task', next: 'END' }],
    }
    const renamed = withNode(defn, 0, { id: 'start' })
    expect(renamed.entry).toBe('start')
    expect(renamed.nodes[0].id).toBe('start')
    // 原对象不被修改
    expect(defn.nodes[0].id).toBe('a')
    expect(withNode(defn, 1, { next: 'a' }).entry).toBe('a')
    expect(withoutNode(defn, 0).nodes.map((n) => n.id)).toEqual(['b'])
  })

  it('nextVersion 递增推进，非 v 编号回落到 1', () => {
    expect(nextVersion([])).toBe('v1')
    expect(nextVersion([{ version: 'v1' }, { version: 'v9' }])).toBe('v10')
    expect(nextVersion([{ version: 'release-2026' }])).toBe('v1')
  })
})

/* ===================== 渲染与权限 ===================== */

/** dept_staff + 显式权限表：避免落入 authStore 的 cio/chairman 放行分支 */
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

describe('WorkflowDesigner 渲染', () => {
  beforeEach(() => {
    vi.mocked(apiService.getWorkflowDesignerMeta).mockResolvedValue(META as never)
    vi.mocked(apiService.getWorkflowStatus).mockResolvedValue({
      versions: [], stable: null, canary: null, publish_eval: { status: 'none' },
    } as never)
    vi.mocked(apiService.validateWorkflowDefinition).mockResolvedValue({ valid: true, errors: [] } as never)
  })

  it('元数据到达后载入出厂模板并渲染节点与版本历史', async () => {
    setPerms(['customization:read', 'customization:update'])
    render(<WorkflowDesigner />)

    expect(await screen.findByText('节点（4）')).toBeInTheDocument()
    expect(screen.getByText('stable 出厂模板')).toBeInTheDocument()
    expect(screen.getByText('尚无发布版本，当前运行出厂模板。')).toBeInTheDocument()
    expect(vi.mocked(apiService.getWorkflowStatus)).toHaveBeenCalledWith('reimbursement_approval')
  })

  it('只有 read/update 时发布入口被权限挡住', async () => {
    setPerms(['customization:read', 'customization:update'])
    render(<WorkflowDesigner />)
    expect(await screen.findByText('节点（4）')).toBeInTheDocument()
    expect(screen.getByText('保存草稿')).toBeInTheDocument()
    expect(screen.queryByText('申请发布')).not.toBeInTheDocument()
    expect(screen.getByText('需 customization:publish 权限')).toBeInTheDocument()
  })

  it('拥有 publish 权限时出现申请发布', async () => {
    setPerms(['customization:read', 'customization:update', 'customization:publish'])
    render(<WorkflowDesigner />)
    expect(await screen.findByText('申请发布')).toBeInTheDocument()
  })

  it('校验按钮把当前定义交给后端同一校验器，并回显结论', async () => {
    setPerms(['customization:publish'])
    render(<WorkflowDesigner />)
    fireEvent.click(await screen.findByText('校验'))

    await waitFor(() => expect(screen.getByText('校验通过')).toBeInTheDocument())
    const payload = vi.mocked(apiService.validateWorkflowDefinition).mock.calls[0][0]
    expect(payload).toMatchObject({ name: 'reimbursement_approval' })
    expect((payload as WfDefinition).nodes.map((n) => n.id)).toEqual(['submit', 'gate', 'verify', 'reject'])
  })

  it('校验失败时逐条展示后端错误，供设计器内就地整改', async () => {
    vi.mocked(apiService.validateWorkflowDefinition).mockResolvedValue({
      valid: false, errors: ['节点 reject 未从入口连通', 'gate 节点缺少 approve_roles'],
    } as never)
    setPerms(['customization:publish'])
    render(<WorkflowDesigner />)
    fireEvent.click(await screen.findByText('校验'))

    expect(await screen.findByText('节点 reject 未从入口连通')).toBeInTheDocument()
    expect(screen.getByText('gate 节点缺少 approve_roles')).toBeInTheDocument()
    expect(screen.getByText('校验未通过（2 项）')).toBeInTheDocument()
  })

  it('申请发布走转人审提示（宽变更必经审批红线）', async () => {
    vi.mocked(apiService.requestWorkflowPublish).mockResolvedValue({ status: 'pending_approval' } as never)
    setPerms(['customization:publish'])
    render(<WorkflowDesigner />)
    fireEvent.click(await screen.findByText('申请发布'))

    expect(await screen.findByText(/发布申请已提交人工审批/)).toBeInTheDocument()
    expect(vi.mocked(apiService.requestWorkflowPublish)).toHaveBeenCalledWith(
      'reimbursement_approval', 'v1', 0, '')
  })

  it('元数据加载失败时给出可见错误而非空表', async () => {
    vi.mocked(apiService.getWorkflowDesignerMeta).mockRejectedValue(new Error('离线') as never)
    setPerms(['customization:read'])
    render(<WorkflowDesigner />)
    expect(await screen.findByText('离线')).toBeInTheDocument()
    expect(screen.getByText('节点（0）')).toBeInTheDocument()
  })
})
