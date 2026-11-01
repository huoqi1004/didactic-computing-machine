/**
 * 评测趋势看板测试（Phase F5.2）
 * ==============================
 * 1. 纯函数：通过率口径（计数缺失 → null，不冒充 0%/100%）、趋势点按时间升序、
 *    计数文案、汇总统计；
 * 2. 渲染：域下拉与用例数同源、筛选参数打到后端、触发的写权限闸门、
 *    轮询到终态后回到历史列表、无历史/无趋势点时给明确空态而不是空白面板。
 */
import { act, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { apiService } from '../services/api'
import { useAuthStore } from '../stores/authStore'
import {
  EvalTrendBoard,
  countsLabel,
  durationLabel,
  passRateOf,
  trendPoints,
  trendSummary,
  verdictOf,
} from './EvalTrendBoard'

vi.mock('../services/api', () => ({
  apiService: {
    getEvalCases: vi.fn(),
    getEvalHistory: vi.fn(),
    getEvalStatus: vi.fn(),
    runEvals: vi.fn(),
  },
}))

const PASS_RUN = {
  run_id: 'eval-01', domain: 'reimbursement', source: 'manual', status: 'pass',
  passed: 8, failed: 0, errors: null, skipped: null, deselected: 42,
  duration_s: 3.21, exit_code: 0, triggered_by: 'user_cio',
  created_at: '2026-09-30T09:00:00', finished_at: '2026-09-30T09:00:04',
  tail: '8 passed, 42 deselected in 3.21s',
}
const FAIL_RUN = {
  run_id: 'eval-02', domain: 'contract', source: 'gate', status: 'fail',
  passed: 7, failed: 1, errors: null, skipped: null, deselected: 42,
  duration_s: 4.55, exit_code: 1, triggered_by: 'publish:user_admin',
  created_at: '2026-09-30T10:30:00', finished_at: '2026-09-30T10:30:06',
  tail: '1 failed, 7 passed, 42 deselected in 4.55s',
}
// 库里没有计数的一行（旧版本落库前跑的运行 / pytest 输出异常）
const NO_COUNT_RUN = {
  run_id: 'eval-00', domain: '', source: 'manual', status: 'pass',
  passed: null, failed: null, duration_s: null, exit_code: 0,
  triggered_by: 'user_cio', created_at: '2026-09-30T08:00:00', tail: '',
}
const CASES = {
  total: 50,
  domains: [
    { domain: 'reimbursement', cases: 8 },
    { domain: 'treasury', cases: 8 },
    { domain: 'hr', cases: 7 },
  ],
}

function setPerms(permissions: string[]) {
  useAuthStore.setState({
    token: 't', refreshToken: null, isAuthenticated: true, loading: false, error: null,
    user: { user_id: 'u_cio', username: 'cio_user', role_codes: ['cio'], permissions } as never,
  })
}

/** 不用 waitFor：本文件部分用例开着 fake timers，靠 act 冲微任务更稳。 */
async function flush(times = 8) {
  for (let i = 0; i < times; i++) await act(async () => { await Promise.resolve() })
}

describe('EvalTrendBoard 纯函数', () => {
  it('通过率只认 pytest 报出的数字，缺计数一律 null', () => {
    expect(passRateOf(PASS_RUN)).toBe(1)
    expect(passRateOf(FAIL_RUN)).toBeCloseTo(7 / 8, 6)
    expect(passRateOf(NO_COUNT_RUN)).toBeNull()
    expect(passRateOf({ ...PASS_RUN, passed: 0, failed: 0, deselected: 50 })).toBeNull()
  })

  it('countsLabel / durationLabel 把缺字段写成可读占位，不是 NaN 或 0', () => {
    expect(countsLabel(PASS_RUN)).toBe('8 通过 · 42 未选')
    expect(countsLabel(FAIL_RUN)).toBe('7 通过 · 1 失败 · 42 未选')
    expect(countsLabel(NO_COUNT_RUN)).toBe('计数未取到')
    expect(durationLabel(PASS_RUN)).toBe('3.21s')
    expect(durationLabel(NO_COUNT_RUN)).toBe('—')
  })

  it('verdictOf 只认 pass/fail/running，其它值当未知（不能默认成通过）', () => {
    expect(verdictOf(PASS_RUN)).toBe('pass')
    expect(verdictOf(FAIL_RUN)).toBe('fail')
    expect(verdictOf({ ...PASS_RUN, status: 'running' })).toBe('running')
    expect(verdictOf({ ...PASS_RUN, status: 'weird' })).toBe('unknown')
  })

  it('trendPoints 按时间升序取点，跳过没有通过率的运行', () => {
    // 接口给的是新→旧：eval-02(10:30) → eval-01(09:00) → eval-00(08:00)
    const pts = trendPoints([FAIL_RUN, PASS_RUN, NO_COUNT_RUN])
    expect(pts).toHaveLength(2)                    // 无计数那次不进趋势
    expect(pts[0].runId).toBe('eval-01')           // 最早的在左
    expect(pts[0].x).toBe(0)
    expect(pts[0].y).toBe(0)                       // 100% 通过 → 顶部
    expect(pts[1].runId).toBe('eval-02')
    expect(pts[1].x).toBe(100)
    expect(pts[1].y).toBeCloseTo(12.5, 6)          // 87.5% → 距顶 12.5
  })

  it('trendPoints 单次运行时落在中间，不出现除零', () => {
    expect(trendPoints([PASS_RUN])).toEqual([{ x: 50, y: 0, runId: 'eval-01' }])
    expect(trendPoints([NO_COUNT_RUN])).toEqual([])
  })

  it('trendSummary 分开数结论与"不计入趋势"：状态是结论，计数缺失只影响趋势', () => {
    // NO_COUNT_RUN 的 status=pass（exit_code 0）仍是"通过"，但没有 pytest 计数，
    // 所以它进 pass 桶、也进 noCounts 桶——两个轴不能混成一个。
    expect(trendSummary([FAIL_RUN, PASS_RUN, NO_COUNT_RUN])).toEqual({
      total: 3, pass: 2, fail: 1, running: 0, noCounts: 1,
    })
    expect(trendSummary([{ ...PASS_RUN, status: 'running' }])).toMatchObject({
      total: 1, pass: 0, running: 1,
    })
  })
})

describe('EvalTrendBoard 渲染', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(apiService.getEvalCases).mockResolvedValue(CASES as never)
    vi.mocked(apiService.getEvalHistory).mockResolvedValue({
      runs: [FAIL_RUN, PASS_RUN, NO_COUNT_RUN],
    } as never)
    setPerms(['customization:read', 'customization:update'])
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('域下拉带用例数、明细行给结果/计数/通过率/时长/触发人', async () => {
    render(<EvalTrendBoard />)
    expect(await screen.findByText('contract')).toBeInTheDocument()

    const sel = screen.getByTestId('eval-domain')
    expect(within(sel).getByText('全部（50 用例）')).toBeInTheDocument()
    expect(within(sel).getByText('reimbursement（8 用例）')).toBeInTheDocument()

    const table = screen.getByRole('table')
    expect(within(table).getByText('2026-09-30 09:00:00')).toBeInTheDocument()
    expect(within(table).getByText('8 通过 · 42 未选')).toBeInTheDocument()
    expect(within(table).getByText('7 通过 · 1 失败 · 42 未选')).toBeInTheDocument()
    expect(within(table).getByText('计数未取到')).toBeInTheDocument()
    // 手动来源两条（生效那次与无计数那次），门禁那次单独标出来
    expect(within(table).getAllByText('手动')).toHaveLength(2)
    expect(within(table).getByText('门禁')).toBeInTheDocument()
    expect(within(table).getByText('publish:user_admin')).toBeInTheDocument()
    expect(within(table).getByText('3.21s')).toBeInTheDocument()
    // 通过率两列并存：100% 与 87.5%，"未取到"的那行给占位而不是 0%
    expect(within(table).getByText('100%')).toBeInTheDocument()
    expect(within(table).getByText('88%')).toBeInTheDocument()
    expect(within(table).getAllByText('—').length).toBeGreaterThan(0)

    expect(screen.getByText(/共 3 次 · 通过 2 · 失败 1 · 1 次未取到计数（不计入趋势）/))
      .toBeInTheDocument()
  })

  it('筛选参数打到后端（域/来源/条数），不是前端截断', async () => {
    render(<EvalTrendBoard />)
    await screen.findByText('contract')

    fireEvent.change(screen.getByTestId('eval-domain'), { target: { value: 'reimbursement' } })
    await flush()
    expect(apiService.getEvalHistory).toHaveBeenLastCalledWith('reimbursement', undefined, 50)

    fireEvent.change(screen.getByTestId('eval-source'), { target: { value: 'gate' } })
    await flush()
    expect(apiService.getEvalHistory).toHaveBeenLastCalledWith('reimbursement', 'gate', 50)

    fireEvent.change(screen.getByTestId('eval-domain'), { target: { value: '' } })
    await flush()
    expect(apiService.getEvalHistory).toHaveBeenLastCalledWith(undefined, 'gate', 50)
  })

  it('点行展开尾部输出与 run_id/exit_code', async () => {
    render(<EvalTrendBoard />)
    fireEvent.click(await screen.findByText('contract'))

    const box = await screen.findByTestId('eval-tail-eval-02')
    expect(box.textContent).toContain('1 failed, 7 passed')
    expect(screen.getByText(/run_id：eval-02/)).toBeInTheDocument()
    expect(screen.getByText(/exit_code：1/)).toBeInTheDocument()
  })

  it('无 customization:update 时触发按钮不可用，只给权限点提示', async () => {
    setPerms(['customization:read'])
    render(<EvalTrendBoard />)
    await screen.findByText('contract')

    expect(screen.queryByText('触发评测')).not.toBeInTheDocument()
    expect(screen.getByText('需 customization:update')).toBeInTheDocument()
    expect(apiService.runEvals).not.toHaveBeenCalled()
  })

  it('触发评测 → 轮询到终态 → 提示结论并重取历史', async () => {
    vi.useFakeTimers()
    vi.mocked(apiService.runEvals).mockResolvedValue({ run_id: 'eval-new' } as never)
    vi.mocked(apiService.getEvalStatus)
      .mockResolvedValueOnce({ finished: false, status: 'running' } as never)
      .mockResolvedValueOnce({
        finished: true, summary: 'FAIL', exit_code: 1,
        tail: 'xx\n1 failed, 7 passed in 4.55s',
      } as never)

    render(<EvalTrendBoard />)
    await flush()
    const before = vi.mocked(apiService.getEvalHistory).mock.calls.length

    await act(async () => {
      fireEvent.click(screen.getByText('触发评测'))
    })
    await flush()
    expect(apiService.runEvals).toHaveBeenCalledWith('')
    expect(screen.getByTestId('eval-message').textContent).toContain('评测进行中')

    await act(async () => { vi.advanceTimersByTime(3000) })
    await flush()
    expect(apiService.getEvalStatus).toHaveBeenCalledTimes(2)
    expect(screen.getByTestId('eval-message').textContent)
      .toContain('本轮评测结束：FAIL · 1 failed, 7 passed in 4.55s')
    // 结束后必须重取历史：新跑完的那次要立刻出现在列表里
    expect(vi.mocked(apiService.getEvalHistory).mock.calls.length).toBeGreaterThan(before)
    // 触发中禁用按钮，跑完后恢复
    expect(screen.getByText('触发评测')).toBeInTheDocument()
  })

  it('评测触发失败给真错（403/409 不静默）', async () => {
    vi.mocked(apiService.runEvals).mockRejectedValue({
      response: { data: { detail: '权限不足：需要 customization:update' } },
    } as never)
    render(<EvalTrendBoard />)
    await screen.findByText('contract')

    await act(async () => {
      fireEvent.click(screen.getByText('触发评测'))
    })
    await flush()
    expect(await screen.findByText('权限不足：需要 customization:update')).toBeInTheDocument()
    expect(apiService.getEvalStatus).not.toHaveBeenCalled()
  })

  it('全部运行都没有计数时，折线区说明为什么画不出来', async () => {
    vi.mocked(apiService.getEvalHistory).mockResolvedValue({ runs: [NO_COUNT_RUN] } as never)
    render(<EvalTrendBoard />)
    await screen.findByText('计数未取到')

    expect(screen.getByText('暂无可绘制的趋势点（评测历史为空，或这些运行未取到 pytest 计数）'))
      .toBeInTheDocument()
    expect(screen.queryByTestId('eval-chart')).not.toBeInTheDocument()
  })

  it('历史加载失败时空表写明查询失败', async () => {
    vi.mocked(apiService.getEvalHistory).mockRejectedValue({
      response: { data: { detail: '评测历史查询失败' } },
    } as never)
    render(<EvalTrendBoard />)

    expect(await screen.findByText('评测历史查询失败')).toBeInTheDocument()
    expect(screen.getByText('查询失败，未取到记录')).toBeInTheDocument()
  })
})
