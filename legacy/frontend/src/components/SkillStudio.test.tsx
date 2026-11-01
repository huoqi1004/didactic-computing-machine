/**
 * Skill 工坊测试（Phase F4.6，覆盖 F4.4 编辑器 + F4.5 版本与搬运）
 * ==============================================================
 * 1. 纯函数：版本推进、切域清跨域工具、guardrails 解析、已发布不可变的动作判定、
 *    后端 status → 界面结论（发布/回滚/待审批必须区分开）；
 * 2. 渲染：目录标签区分出厂/已发布/停用、运行时视图证明"生效版本"、
 *    申请发布前必须先过后端校验器（前端不复制规则）、发布走人审、
 *    停用即时而启用须批、回滚只对历史已批版本开放、导入只落草稿；
 * 3. 可发现性：从未发布过的草稿（导入/新建）要在目录里有点得动的入口，
 *    而不是只能手输标识符。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { useAuthStore } from '../stores/authStore'
import { apiService } from '../services/api'
import {
  SkillStudio,
  buildDefinition,
  canEdit,
  canRollback,
  changeNotice,
  editableVersion,
  exportSummary,
  idemKey,
  nextVersion,
  parseGuardrails,
  toolsOnDomainChange,
} from './SkillStudio'

vi.mock('../services/api', () => ({
  apiService: {
    getSkillDesignerMeta: vi.fn(),
    listSkillVersions: vi.fn(),
    getSkillStatus: vi.fn(),
    getSkillDefinition: vi.fn(),
    validateSkillDefinition: vi.fn(),
    saveSkillDraft: vi.fn(),
    requestSkillPublish: vi.fn(),
    setSkillEnabled: vi.fn(),
    rollbackSkill: vi.fn(),
    exportSkills: vi.fn(),
    importSkills: vi.fn(),
  },
}))

const V2 = {
  id: 2, name: 'sales_helper', version: 'v2', status: 'stable',
  domain: 'sales', display_name: '销售助手', tool_count: 1,
  comment: '按域报价', created_by: 'u_cio', published_at: '2026-09-30T10:00:00',
}
const V1 = {
  id: 1, name: 'sales_helper', version: 'v1', status: 'superseded',
  domain: 'sales', display_name: '销售助手', tool_count: 2,
  comment: '首版', created_by: 'u_cio', published_at: '2026-09-20T10:00:00',
}

const DEFINITION = {
  name: 'sales_helper',
  display_name: '销售助手',
  description: '按客户等级报价',
  domain: 'sales',
  tools: ['crm_query'],
  instructions: '先查客户等级再报价。',
  guardrails: { max_amount: 5000 },
}

const STATUS = {
  name: 'sales_helper',
  versions: [V2, V1],
  stable: V2,
  drafts: [],
  enabled: true,
  pending: null,
  runtime: {
    version: 'v2', domain: 'sales', tools: ['crm_query'],
    valid: true, invalid_reason: '', published: true,
  },
}

const META = {
  tools_by_domain: { sales: ['crm_query', 'order_create'], treasury: ['cash_flow_report'] },
  domains: ['sales', 'treasury'],
  skills: [
    {
      name: 'sales_helper', display_name: '销售助手', description: '按客户等级报价',
      domain: 'sales', tools: ['crm_query'], valid: true, invalid_reason: '',
      enabled: true, version: 'v2', published: true,
    },
    {
      name: 'treasury_helper', display_name: '资金助手', description: '',
      domain: 'treasury', tools: [], valid: true, invalid_reason: '',
      enabled: false, version: '', published: false,
    },
  ],
  template: {
    name: '', display_name: '', description: '', domain: '',
    tools: [], instructions: '', guardrails: {},
  },
  overlay_dir: '/app/backend/data/skills_overlay',
  name_pattern: '^[a-z][a-z0-9_]{1,63}$',
}

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

describe('SkillStudio 纯函数', () => {
  it('nextVersion 取最大号推进，无版本回落 v1', () => {
    expect(nextVersion([])).toBe('v1')
    expect(nextVersion([{ version: 'v1' }, { version: 'v9' }])).toBe('v10')
    expect(nextVersion([{ version: 'release' }])).toBe('v1')
  })

  it('toolsOnDomainChange 只保留新域在册工具（跨域白名单会让图编译直接失败）', () => {
    const byDomain = { sales: ['crm_query', 'order_create'], treasury: ['cash_flow_report'] }
    expect(toolsOnDomainChange(['crm_query', 'order_create'], 'treasury', byDomain)).toEqual([])
    expect(toolsOnDomainChange(['crm_query', 'cash_flow_report'], 'treasury', byDomain))
      .toEqual(['cash_flow_report'])
    expect(toolsOnDomainChange(['crm_query'], 'unknown', byDomain)).toEqual([])
  })

  it('parseGuardrails 空串视为空对象，数组与坏 JSON 拒绝', () => {
    expect(parseGuardrails('')).toEqual({ ok: true, value: {}, error: '' })
    expect(parseGuardrails('  ')).toEqual({ ok: true, value: {}, error: '' })
    expect(parseGuardrails('{"max_amount":5000}').value).toEqual({ max_amount: 5000 })
    expect(parseGuardrails('[]').ok).toBe(false)
    expect(parseGuardrails('[]').error).toMatch(/必须是 JSON 对象/)
    expect(parseGuardrails('{').ok).toBe(false)
    expect(parseGuardrails('null').error).toMatch(/必须是 JSON 对象/)
  })

  it('buildDefinition 用标识符兜底显示名，并统一 trim', () => {
    const defn = buildDefinition(' sales_helper ', {
      ...DEFINITION, display_name: '  ', description: ' 按客户等级报价 ',
    }, { a: 1 })
    expect(defn.name).toBe('sales_helper')
    expect(defn.display_name).toBe('sales_helper')
    expect(defn.description).toBe('按客户等级报价')
    expect(defn.guardrails).toEqual({ a: 1 })
    expect(defn.tools).toEqual(DEFINITION.tools)
  })

  it('已发布版本不可变：只有草稿可编辑，stable/superseded 可回滚', () => {
    expect(canEdit(null)).toBe(true)
    expect(canEdit({ ...V1, status: 'draft' })).toBe(true)
    expect(canEdit(V2)).toBe(false)
    expect(canRollback(V1)).toBe(true)
    expect(canRollback(V2)).toBe(true)
    expect(canRollback({ ...V1, status: 'draft' })).toBe(false)
    expect(canRollback(null)).toBe(false)
    expect(editableVersion([V2, V1], 'v1')?.status).toBe('superseded')
    expect(editableVersion([V2, V1], 'v9')).toBeNull()
  })

  it('idemKey 同动作同状态同键', () => {
    expect(idemKey('skill-publish', 'sales_helper', 'v3')).toBe('skill-publish:sales_helper:v3')
  })

  it('changeNotice 区分发布/回滚/待审批，不把放宽说成已生效', () => {
    expect(changeNotice({ status: 'pending_approval' })).toMatch(/批准后生效并物化/)
    expect(changeNotice({ status: 'applied' })).toBe('收紧类变更，已即时生效。')
    expect(changeNotice({ status: 'published', version: 'v3' })).toBe('已发布为 v3（生效版本切换）。')
    expect(changeNotice({ status: 'rolled_back', version: 'v1' })).toBe('已回滚到 v1。')
    expect(changeNotice({ status: 'duplicate' })).toMatch(/幂等键/)
    expect(changeNotice({ status: 'rejected' })).toMatch(/驳回/)
    expect(changeNotice({ reply: '技能已生效' })).toBe('技能已生效')
    expect(changeNotice(undefined)).toBe('已完成。')
  })

  it('exportSummary 分开计数草稿与出厂，不把出厂说成已发布', () => {
    expect(exportSummary({ count: 0, skills: [] })).toBe('已导出 0 个技能定义（无）。')
    expect(exportSummary({
      count: 3, skills: [
        { name: 'a', status: 'draft' }, { name: 'b', status: 'factory' },
        { name: 'c', status: 'stable' },
      ],
    })).toBe('已导出 3 个技能定义（1 草稿 · 1 出厂 · 1 已发布）。')
    expect(exportSummary({
      count: 6, skills: Array.from({ length: 6 }, (_, i) => ({ name: `f${i}`, status: 'factory' })),
    })).toBe('已导出 6 个技能定义（6 出厂）。')
    expect(exportSummary(null)).toBe('已导出 0 个技能定义（无）。')
  })
})

/* ===================== 渲染与动作 ===================== */

describe('SkillStudio 渲染', () => {
  beforeEach(() => {
    // 只清调用记录，不清上面各用例自己设的 resolved value
    vi.clearAllMocks()
    vi.mocked(apiService.getSkillDesignerMeta).mockResolvedValue(META as never)
    vi.mocked(apiService.getSkillStatus).mockResolvedValue(STATUS as never)
    vi.mocked(apiService.getSkillDefinition).mockResolvedValue({
      name: 'sales_helper', version: 'v2', definition: DEFINITION, status: 'stable',
    } as never)
    vi.mocked(apiService.validateSkillDefinition).mockResolvedValue({
      valid: true, errors: [], publish_errors: [],
    } as never)
  })

  it('目录区分已发布版本、出厂默认与停用状态', async () => {
    setPerms(['customization:read'])
    render(<SkillStudio />)

    expect(await screen.findByText('销售助手')).toBeInTheDocument()
    expect(screen.getByText('v2')).toBeInTheDocument()
    expect(screen.getByText('停用')).toBeInTheDocument()
    expect(screen.getByText('sales · 1 工具')).toBeInTheDocument()
    expect(screen.getByText('treasury · 0 工具')).toBeInTheDocument()
  })

  it('未选技能时展示发布目录（生效证据的落点）', async () => {
    setPerms(['customization:read'])
    render(<SkillStudio />)

    expect(await screen.findByText(/左侧选择技能/)).toBeInTheDocument()
    expect(screen.getByText('/app/backend/data/skills_overlay')).toBeInTheDocument()
  })

  it('选中技能即载入生效版本定义与运行时视图', async () => {
    setPerms(['customization:read'])
    render(<SkillStudio />)

    fireEvent.click(await screen.findByText('销售助手'))
    expect(await screen.findByText('技能定义（表单 → YAML → 热加载）')).toBeInTheDocument()
    expect(apiService.getSkillStatus).toHaveBeenCalledWith('sales_helper')
    expect(apiService.getSkillDefinition).toHaveBeenCalledWith('sales_helper', 'v2')
    expect(screen.getByDisplayValue('按客户等级报价')).toBeInTheDocument()
    expect(screen.getByText('已加载 v2')).toBeInTheDocument()
    expect(screen.getByText('启用中')).toBeInTheDocument()
    expect(screen.getByText('工具白名单（1 / 2）')).toBeInTheDocument()
  })

  it('切域把跨域工具勾选清掉，避免发布出编译不了的白名单', async () => {
    setPerms(['customization:update'])
    render(<SkillStudio />)

    fireEvent.click(await screen.findByText('销售助手'))
    await screen.findByText('工具白名单（1 / 2）')
    fireEvent.change(screen.getByTestId('skill-domain'), { target: { value: 'treasury' } })

    expect(await screen.findByText('工具白名单（0 / 1）')).toBeInTheDocument()
    expect(screen.queryByText('该域无可选工具（工具由治理面注册表提供）')).not.toBeInTheDocument()
  })

  it('guardrails 写成非法 JSON 时本地拦住，不打到后端', async () => {
    setPerms(['customization:update'])
    render(<SkillStudio />)

    fireEvent.click(await screen.findByText('销售助手'))
    fireEvent.change(await screen.findByTestId('skill-guardrails'), { target: { value: '{' } })
    fireEvent.click(screen.getByRole('button', { name: '校验' }))

    expect(await screen.findByText(/guardrails|JSON 解析失败/)).toBeInTheDocument()
    expect(apiService.saveSkillDraft).not.toHaveBeenCalled()
    expect(apiService.validateSkillDefinition).not.toHaveBeenCalled()
  })

  it('发布权限缺失时只能存草稿，申请发布与回滚都不出现', async () => {
    setPerms(['customization:read', 'customization:update'])
    render(<SkillStudio />)

    fireEvent.click(await screen.findByText('销售助手'))
    await screen.findByText('技能定义（表单 → YAML → 热加载）')
    expect(screen.getByRole('button', { name: '存草稿' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /申请发布/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '回滚' })).not.toBeInTheDocument()
  })

  it('申请发布前先跑后端同一校验器，发布门槛未过就不提交', async () => {
    vi.mocked(apiService.validateSkillDefinition).mockResolvedValue({
      valid: true, errors: [], publish_errors: ['工具未在治理面注册: order_create'],
    } as never)
    setPerms(['customization:publish'])
    render(<SkillStudio />)

    fireEvent.click(await screen.findByText('销售助手'))
    fireEvent.click(await screen.findByRole('button', { name: /申请发布/ }))

    expect(await screen.findByText(/工具未在治理面注册/)).toBeInTheDocument()
    expect(apiService.requestSkillPublish).not.toHaveBeenCalled()
  })

  it('校验通过时申请发布转人审，界面说"待审批"而非已生效', async () => {
    vi.mocked(apiService.requestSkillPublish).mockResolvedValue({ status: 'pending_approval' } as never)
    setPerms(['customization:publish'])
    render(<SkillStudio />)

    fireEvent.click(await screen.findByText('销售助手'))
    fireEvent.click(await screen.findByRole('button', { name: /申请发布/ }))

    await waitFor(() => expect(apiService.requestSkillPublish).toHaveBeenCalled())
    expect(apiService.requestSkillPublish).toHaveBeenCalledWith(
      'sales_helper', 'v2', '', 'skill-publish:sales_helper:v2')
    expect(await screen.findByText(/已提交人工审批（放宽类变更）/)).toBeInTheDocument()
  })

  it('停用即时生效，启用走人审', async () => {
    vi.mocked(apiService.setSkillEnabled).mockResolvedValue({ status: 'applied' } as never)
    setPerms(['customization:update'])
    render(<SkillStudio />)

    fireEvent.click(await screen.findByText('销售助手'))
    fireEvent.click(await screen.findByRole('button', { name: '停用（即时生效）' }))
    expect(apiService.setSkillEnabled).toHaveBeenCalledWith(
      'sales_helper', false, '管理端停用技能', 'skill-enabled:sales_helper:false')
    expect(await screen.findByText('收紧类变更，已即时生效。')).toBeInTheDocument()
  })

  it('启停后重取目录元数据，徽标不会停在动作前的状态', async () => {
    // 真机验证暴露：目录来自 /skills/meta，停用后不重取就会在列表里继续显示"启用中"，
    // 界面与治理事实（停用即时生效）相反。
    vi.mocked(apiService.setSkillEnabled).mockResolvedValue({ status: 'applied' } as never)
    const after = {
      ...META,
      skills: META.skills.map((s) =>
        s.name === 'sales_helper' ? { ...s, enabled: false } : s),
    }
    vi.mocked(apiService.getSkillDesignerMeta)
      .mockResolvedValueOnce(META as never)
      .mockResolvedValueOnce(after as never)
    setPerms(['customization:update'])
    render(<SkillStudio />)

    fireEvent.click(await screen.findByText('销售助手'))
    fireEvent.click(await screen.findByRole('button', { name: '停用（即时生效）' }))

    await waitFor(() => expect(apiService.getSkillDesignerMeta).toHaveBeenCalledTimes(2))
    const row = (await screen.findByText('销售助手')).closest('button')
    expect(row?.textContent).toContain('停用')
  })

  it('待批提案在运行时视图显式标出，避免误读为已生效', async () => {
    vi.mocked(apiService.getSkillStatus).mockResolvedValue({
      ...STATUS,
      pending: { version_id: 9, comment: '放宽报价口径', changed_by: 'u_ops',
                 proposed: { version: 'v3' } },
    } as never)
    setPerms(['customization:read'])
    render(<SkillStudio />)

    fireEvent.click(await screen.findByText('销售助手'))
    expect(await screen.findByText(/待批提案：v3（由 u_ops 提交/)).toBeInTheDocument()
  })

  it('回滚只对历史已批版本开放，当前 stable 自身不可回滚', async () => {
    vi.mocked(apiService.rollbackSkill).mockResolvedValue({
      status: 'rolled_back', version: 'v1',
    } as never)
    setPerms(['customization:publish'])
    render(<SkillStudio />)

    fireEvent.click(await screen.findByText('销售助手'))
    const back = await screen.findAllByRole('button', { name: '回滚' })
    expect(back[0]).toBeDisabled()
    expect(back[1]).toBeEnabled()

    fireEvent.click(back[1])
    await waitFor(() => expect(apiService.rollbackSkill).toHaveBeenCalledWith('sales_helper', 'v1'))
    expect(await screen.findByText('已回滚到 v1。')).toBeInTheDocument()
  })

  it('已发布版本不可变：改内容前先另存为新版本', async () => {
    setPerms(['customization:update'])
    render(<SkillStudio />)

    fireEvent.click(await screen.findByText('销售助手'))
    await screen.findByText('技能定义（表单 → YAML → 热加载）')
    expect(screen.getByRole('button', { name: '存草稿' })).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: '另存为新版本' }))
    expect(screen.getByTestId('skill-version')).toHaveValue('v3')
    expect(screen.getByRole('button', { name: '存草稿' })).toBeEnabled()
  })

  it('存草稿后停在刚保存的草稿版本，申请发布提交的就是它', async () => {
    // 真机验证踩过：重载一律跳回 stable，版本选择器从 v2 掉回 v1，接着点
    // "申请发布"就把已生效的 v1 再发一遍——发布错了版本。
    const stableV1 = { ...V1, status: 'stable' }
    const draftV2 = { ...V2, status: 'draft', published_at: null }
    vi.mocked(apiService.getSkillStatus)
      .mockResolvedValueOnce({
        ...STATUS, versions: [stableV1], stable: stableV1, drafts: [],
      } as never)
      .mockResolvedValue({
        ...STATUS, versions: [draftV2, stableV1], stable: stableV1, drafts: [draftV2],
      } as never)
    vi.mocked(apiService.getSkillDefinition).mockImplementation(((
      skill: string, ver: string) => Promise.resolve({
      name: skill, version: ver, status: ver === 'v2' ? 'draft' : 'stable',
      definition: { ...DEFINITION, instructions: ver === 'v2' ? '草稿指令' : '生效指令' },
    })) as never)
    vi.mocked(apiService.saveSkillDraft).mockResolvedValue({
      status: 'draft', name: 'sales_helper', version: 'v2',
    } as never)
    vi.mocked(apiService.requestSkillPublish).mockResolvedValue({
      status: 'pending_approval',
    } as never)
    setPerms(['customization:update', 'customization:publish'])
    render(<SkillStudio />)

    fireEvent.click(await screen.findByText('销售助手'))
    await screen.findByText('技能定义（表单 → YAML → 热加载）')
    fireEvent.click(screen.getByRole('button', { name: '另存为新版本' }))
    fireEvent.click(screen.getByRole('button', { name: '存草稿' }))

    await waitFor(() => expect(screen.getByTestId('skill-instructions')).toHaveValue('草稿指令'))
    expect(screen.getByTestId('skill-version')).toHaveValue('v2')

    fireEvent.click(screen.getByRole('button', { name: /申请发布/ }))
    await waitFor(() => expect(apiService.requestSkillPublish).toHaveBeenCalled())
    expect(apiService.requestSkillPublish).toHaveBeenCalledWith(
      'sales_helper', 'v2', '', 'skill-publish:sales_helper:v2')
  })

  it('导入只落草稿并提示需逐条发布（跨环境搬运不得绕过本环境审批）', async () => {
    vi.mocked(apiService.importSkills).mockResolvedValue({
      imported: 2, items: [], failed: [{ name: 'Bad_Name', errors: ['技能名非法'] }],
    } as never)
    setPerms(['customization:update'])
    render(<SkillStudio />)

    await screen.findByText('销售助手')
    const bundle = '{ "format": "skills-bundle", "count": 3 }'
    const file = new File([bundle], 'skills.json', { type: 'application/json' })
    // jsdom 未实现 Blob#text（浏览器自 2019 起支持），生产代码不改，只在测试里补最小实现
    ;(file as unknown as { text: () => Promise<string> }).text = () => Promise.resolve(bundle)
    fireEvent.change(screen.getByTestId('skill-import-input'), { target: { files: [file] } })

    expect(await screen.findByText(/导入 2 条为草稿，1 条被拒/)).toBeInTheDocument()
    expect(apiService.importSkills).toHaveBeenCalledWith(
      { format: 'skills-bundle', count: 3 }, 'skill-import:skills-bundle:3')
    expect(await screen.findByText(/技能名非法/)).toBeInTheDocument()
    expect(screen.getByText(/导入内容不会自动生效，需逐条申请发布并过人审/)).toBeInTheDocument()
  })

  it('定制包不是合法 JSON 时本地拒绝，不发导入请求', async () => {
    setPerms(['customization:update'])
    render(<SkillStudio />)

    await screen.findByText('销售助手')
    const file = new File(['{oops'], 'bad.json', { type: 'application/json' })
    ;(file as unknown as { text: () => Promise<string> }).text = () => Promise.resolve('{oops')
    fireEvent.change(screen.getByTestId('skill-import-input'), { target: { files: [file] } })

    expect(await screen.findByText('定制包不是合法 JSON')).toBeInTheDocument()
    expect(apiService.importSkills).not.toHaveBeenCalled()
  })

  it('导出走 customization:export 权限点', async () => {
    setPerms(['customization:read', 'customization:update'])
    const first = render(<SkillStudio />)
    await first.findByText('销售助手')
    expect(screen.queryByTitle('导出 JSON 定制包')).not.toBeInTheDocument()
    first.unmount()

    setPerms(['customization:export'])
    render(<SkillStudio />)
    expect(await screen.findByTitle('导出 JSON 定制包')).toBeInTheDocument()
  })

  it('元数据加载失败时给出可见错误而不是空目录', async () => {
    vi.mocked(apiService.getSkillDesignerMeta).mockRejectedValue({
      response: { data: { detail: '治理面不可用' } },
    } as never)
    setPerms(['customization:read'])
    render(<SkillStudio />)

    expect(await screen.findByText('治理面不可用')).toBeInTheDocument()
    expect(screen.getByText('目录为空')).toBeInTheDocument()
  })
})

/* ===================== 仅存草稿的可发现性（F4.6） ===================== */

describe('SkillStudio 草稿可发现', () => {
  const ORPHAN = {
    id: 7, name: 'imported_helper', version: 'v1', status: 'draft',
    domain: 'reimbursement', display_name: '导入的助手', tool_count: 1,
    comment: '导入自定制包', created_by: 'u_cio', published_at: null,
  }
  const SALES_DRAFT = {
    ...V2, id: 8, version: 'v3', status: 'draft', published_at: null,
  }

  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(apiService.getSkillDesignerMeta).mockResolvedValue(META as never)
    vi.mocked(apiService.getSkillStatus).mockResolvedValue({
      name: 'imported_helper', versions: [ORPHAN], stable: null, drafts: [ORPHAN],
      enabled: true, pending: null,
      runtime: {
        version: '', domain: 'reimbursement', tools: [],
        valid: true, invalid_reason: '', published: false,
      },
    } as never)
    vi.mocked(apiService.getSkillDefinition).mockResolvedValue({
      name: 'imported_helper', version: 'v1', status: 'draft',
      definition: { ...DEFINITION, name: 'imported_helper', instructions: '导入指令：只复述。' },
    } as never)
  })

  it('从未发布过的草稿单独成组，点它把草稿载进编辑器', async () => {
    vi.mocked(apiService.listSkillVersions).mockResolvedValue({
      versions: [SALES_DRAFT, ORPHAN, V2],
    } as never)
    setPerms(['customization:read', 'customization:update'])
    render(<SkillStudio />)

    expect(await screen.findByText('仅存草稿（不参与运行，需逐条申请发布过人审）'))
      .toBeInTheDocument()
    // 已在目录里的技能，它的草稿不在草稿组重复出现
    expect(screen.queryAllByText('销售助手')).toHaveLength(1)

    fireEvent.click(screen.getByText('导入的助手'))
    await waitFor(() => expect(apiService.getSkillStatus).toHaveBeenCalledWith('imported_helper'))
    expect((screen.getByTestId('skill-version') as HTMLInputElement).value).toBe('v1')
    expect((screen.getByTestId('skill-instructions') as HTMLTextAreaElement).value)
      .toBe('导入指令：只复述。')
  })

  it('没有草稿时不出现草稿组', async () => {
    vi.mocked(apiService.listSkillVersions).mockResolvedValue({ versions: [V2, V1] } as never)
    setPerms(['customization:read'])
    render(<SkillStudio />)

    await screen.findByText('销售助手')
    expect(screen.queryByText(/仅存草稿/)).not.toBeInTheDocument()
  })

  it('新建技能存草稿后立刻进草稿组，不用手输标识符再重载', async () => {
    const NEWDRAFT = {
      id: 9, name: 'f46_draft_skill', version: 'v1', status: 'draft',
      domain: 'reimbursement', display_name: 'f46 首草', tool_count: 1,
      comment: '', created_by: 'u_cio', published_at: null,
    }
    let round = 0
    vi.mocked(apiService.listSkillVersions).mockImplementation((() =>
      Promise.resolve({ versions: round++ === 0 ? [] : [NEWDRAFT] })) as never)
    vi.mocked(apiService.saveSkillDraft).mockResolvedValue({
      status: 'draft', name: 'f46_draft_skill', version: 'v1',
    } as never)
    setPerms(['customization:read', 'customization:update'])
    render(<SkillStudio />)

    await screen.findByText('销售助手')
    expect(screen.queryByText(/仅存草稿/)).not.toBeInTheDocument()

    fireEvent.change(screen.getByTestId('skill-name'), { target: { value: 'f46_draft_skill' } })
    fireEvent.click(screen.getByRole('button', { name: /新建技能/ }))
    fireEvent.click(screen.getByRole('button', { name: '存草稿' }))

    expect(await screen.findByText('仅存草稿（不参与运行，需逐条申请发布过人审）'))
      .toBeInTheDocument()
    expect(screen.getByText('f46 首草')).toBeInTheDocument()
    expect(screen.getByText('草稿已保存：f46_draft_skill@v1')).toBeInTheDocument()
    expect(apiService.saveSkillDraft).toHaveBeenCalledWith(
      'f46_draft_skill', 'v1', expect.anything(), '')
  })

  it('草稿转为已发布后从草稿组消失，进运行时目录（版本流与目录同源刷新）', async () => {
    vi.mocked(apiService.listSkillVersions).mockResolvedValue({ versions: [ORPHAN] } as never)
    vi.mocked(apiService.validateSkillDefinition).mockResolvedValue({
      valid: true, errors: [], publish_errors: [],
    } as never)
    vi.mocked(apiService.requestSkillPublish).mockResolvedValue({
      status: 'pending_approval', name: 'imported_helper', version: 'v1',
    } as never)
    setPerms(['customization:read', 'customization:update', 'customization:publish'])
    const first = render(<SkillStudio />)
    await first.findByText('导入的助手')
    fireEvent.click(screen.getByText('导入的助手'))
    await waitFor(() => expect(apiService.getSkillStatus).toHaveBeenCalledWith('imported_helper'))

    // 批准后它会进运行时目录：版本流里不再有草稿行，目录里多了它
    vi.mocked(apiService.listSkillVersions).mockResolvedValue({
      versions: [{ ...ORPHAN, status: 'stable', published_at: '2026-09-30T12:00:00' }],
    } as never)
    vi.mocked(apiService.getSkillDesignerMeta).mockResolvedValue({
      ...META,
      skills: [...META.skills, {
        name: 'imported_helper', display_name: '导入的助手', description: '',
        domain: 'reimbursement', tools: ['reimbursement_query_detail'], valid: true,
        invalid_reason: '', enabled: true, version: 'v1', published: true,
      }],
    } as never)

    fireEvent.click(screen.getByText('申请发布（转人审）'))
    await waitFor(() => expect(apiService.requestSkillPublish)
      .toHaveBeenCalledWith('imported_helper', 'v1', '', 'skill-publish:imported_helper:v1'))
    await waitFor(() => expect(apiService.getSkillDesignerMeta).toHaveBeenCalledTimes(2))
    expect(screen.queryByText(/仅存草稿/)).not.toBeInTheDocument()
  })
})
