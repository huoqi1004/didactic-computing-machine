/**
 * api.ts 的真机踩坑回归 + SSE 流式客户端契约（工作包一 / 工作包三）
 * ==================================================================
 * 1. 静默刷新必须按后端契约把 refresh_token 放**请求体**——只发 Authorization 头会 422，
 *    启动恢复随即把已登录用户踢回 /login；刷新去重窗口还要在"无凭证早退"路径上释放，
 *    否则一次空刷新会永久废掉整页的登录态续期；
 * 2. 对话通道的传输层兜底不得产出业务数字，且要留出 LLM 回答耗时预算；
 * 3. 流式对话（裁决 D-D 第 1 条）必须用 fetch + ReadableStream 自带鉴权头，
 *    分帧要跨 TCP 分片可靠拼接，只有收到终止帧才算走完，否则交回一次性重答。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('axios', () => {
  const instance = {
    interceptors: { request: { use: vi.fn() }, response: { use: vi.fn() } },
    get: vi.fn(),
    post: vi.fn(),
  }
  const axiosMock = {
    create: () => instance,
    get: vi.fn(),
    post: vi.fn(),
  }
  return {
    default: { ...axiosMock, __instance: instance },
    __instance: instance,
    ...axiosMock,
  }
})

const axiosDefault = (await import('axios')).default as unknown as {
  post: ReturnType<typeof vi.fn>
  __instance: { post: ReturnType<typeof vi.fn> }
}

describe('auth 静默刷新契约', () => {
  beforeEach(() => {
    sessionStorage.clear()
    localStorage.clear()
    axiosDefault.post.mockReset()
  })

  it('refresh_token 走请求体，成功后轮换写回 sessionStorage', async () => {
    sessionStorage.setItem('erp-refresh-token', 'RT-1')
    axiosDefault.post.mockResolvedValueOnce({
      data: { access_token: 'AT-2', refresh_token: 'RT-2' },
    })

    const { refreshAccessToken } = await import('./api')
    const token = await refreshAccessToken()

    expect(token).toBe('AT-2')
    const [url, body] = axiosDefault.post.mock.calls[0]
    expect(url).toBe('/api/v1/auth/refresh')
    expect(body).toEqual({ refresh_token: 'RT-1' })
    expect(sessionStorage.getItem('erp-refresh-token')).toBe('RT-2')
  })

  it('无 refresh_token 时不发请求，直接返回 null（让上层落回登录页）', async () => {
    const { refreshAccessToken } = await import('./api')
    const { useAuthStore } = await import('../stores/authStore')
    // 内存态也会 carry 上一轮轮换出的 token，必须一并清空才是"无凭证"
    useAuthStore.setState({ refreshToken: null })

    expect(await refreshAccessToken()).toBeNull()
    expect(axiosDefault.post).not.toHaveBeenCalled()
  })

  it('无凭证早退后必须释放去重窗口，补齐凭证的下一次刷新照常生效', async () => {
    const { refreshAccessToken } = await import('./api')
    const { useAuthStore } = await import('../stores/authStore')
    useAuthStore.setState({ refreshToken: null })

    expect(await refreshAccessToken()).toBeNull()

    // 死锁回归：早退路径不释放 _refreshPromise 的话，这里会拿到上一条已 resolve 的
    // null，页面生命周期内所有 401 静默刷新全部失效（登录态再也续不上）
    sessionStorage.setItem('erp-refresh-token', 'RT-1')
    axiosDefault.post.mockResolvedValueOnce({
      data: { access_token: 'AT-2', refresh_token: 'RT-2' },
    })
    expect(await refreshAccessToken()).toBe('AT-2')
  })
})

describe('对话通道兜底', () => {
  const instance = axiosDefault.__instance

  beforeEach(() => {
    instance.post.mockReset()
  })

  it('通道失败返回中性占位：降级标记齐备，且不含任何业务数字', async () => {
    instance.post.mockRejectedValueOnce(new Error('Network Timeout'))
    const { apiService } = await import('./api')

    const res = await apiService.sendMessage('project-supervisor-agent', 'proj-001 目前进展如何？')

    expect(res.degraded).toBe(true)
    expect(res.degradedReason).toBe('channel_unavailable')
    expect(res.dataSource).toBe('unavailable')
    expect(res.content).toContain('不含业务结论')
    expect(res.content).not.toMatch(/\d/)
    expect(res.content).not.toContain('里程碑')
    expect(res.dataCard).toBeUndefined()
  })

  it('对话请求留出 60s 预算（LLM 带实时数据的实测耗时超过全局 10s 超时）', async () => {
    instance.post.mockResolvedValueOnce({ content: '真实回复' })
    const { apiService } = await import('./api')

    const res = await apiService.sendMessage('project-supervisor-agent', 'proj-001 进展如何？')

    expect(res.content).toBe('真实回复')
    const [url, , config] = instance.post.mock.calls[0]
    expect(url).toBe('/conversation/chat')
    expect(config).toEqual({ timeout: 60000 })
  })
})

/* ===================== SSE 流式对话客户端（工作包三 / 裁决 D-D） ===================== */

/** 把若干 SSE 文本帧包成"可读流响应"；frames 之间即为 TCP 分片边界。 */
function sseResponse(frames: string[], status = 200) {
  const encoder = new TextEncoder()
  const chunks = frames.map((frame) => encoder.encode(frame))
  let cursor = 0
  return {
    ok: status >= 200 && status < 300,
    status,
    body: {
      getReader: () => ({
        read: async () =>
          cursor < chunks.length
            ? { done: false, value: chunks[cursor++] }
            : { done: true, value: undefined },
      }),
    },
  } as unknown as Response
}

const frame = (event: string, data: unknown) =>
  `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`

function handlers() {
  return {
    onThinking: vi.fn(),
    onToken: vi.fn(),
    onDataCard: vi.fn(),
    onDone: vi.fn(),
    onRetract: vi.fn(),
    onError: vi.fn(),
  }
}

describe('SSE 流式对话客户端', () => {
  const fetchMock = vi.fn()
  let useAuthStore: (typeof import('../stores/authStore'))['useAuthStore']

  beforeEach(async () => {
    sessionStorage.clear()
    localStorage.clear()
    axiosDefault.post.mockReset()
    fetchMock.mockReset()
    vi.stubGlobal('fetch', fetchMock)
    // authStore 与 api.ts 互相引用，沿用本文件既有的动态导入，避免顶层循环 import
    useAuthStore = (await import('../stores/authStore')).useAuthStore
    useAuthStore.setState({ token: 'AT-1', refreshToken: null })
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('按帧序派发回调，收到 done 才算走完，并自带鉴权头（不使用 EventSource）', async () => {
    fetchMock.mockResolvedValue(
      sseResponse([
        frame('thinking', { step: '识别意图：查询类' }),
        frame('token', { text: '在监 2 个项目，' }),
        frame('token', { text: 'proj-001 进度 40%' }),
        frame('dataCard', { title: '在监项目', rows: [[{ label: '进度', value: '40.0%' }]] }),
        frame('done', { traceId: 'trace-1', intent: 'query', status: 'completed' }),
      ]),
    )
    const eventSourceSpy = vi.fn()
    vi.stubGlobal('EventSource', eventSourceSpy)
    const h = handlers()

    const { sendMessageStream } = await import('./api')
    const outcome = await sendMessageStream('project-supervisor-agent', 'proj-001 进展如何？', h)

    expect(outcome).toEqual({ kind: 'completed' })
    expect(h.onThinking).toHaveBeenCalledWith('识别意图：查询类')
    expect(h.onToken.mock.calls.map((c) => c[0])).toEqual(['在监 2 个项目，', 'proj-001 进度 40%'])
    expect(h.onDataCard).toHaveBeenCalledTimes(1)
    expect(h.onDone).toHaveBeenCalledWith(expect.objectContaining({ traceId: 'trace-1' }))
    expect(h.onError).not.toHaveBeenCalled()

    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/v1/conversation/chat/stream')
    expect(init.method).toBe('POST')
    expect(init.headers.Accept).toBe('text/event-stream')
    expect(init.headers.Authorization).toBe('Bearer AT-1')
    expect(JSON.parse(init.body)).toEqual({
      agent_id: 'project-supervisor-agent',
      message: 'proj-001 进展如何？',
    })
    // EventSource 带不上 Authorization 头，裁决 D-D 明令禁用
    expect(eventSourceSpy).not.toHaveBeenCalled()
  })

  it('帧被 TCP 分片拦腰切断也要拼回完整帧', async () => {
    const whole = frame('token', { text: '预算使用率 40.0%，无超支' })
    const mid = whole.indexOf('data') + 4
    fetchMock.mockResolvedValue(
      sseResponse([whole.slice(0, mid), whole.slice(mid, mid + 9), whole.slice(mid + 9)]),
    )
    const h = handlers()

    const { sendMessageStream } = await import('./api')
    await sendMessageStream('project-supervisor-agent', 'proj-001 预算', h)

    expect(h.onToken).toHaveBeenCalledWith('预算使用率 40.0%，无超支')
  })

  it('流被中途掐断（无终止帧）返回 stream_incomplete，交回一次性重答', async () => {
    fetchMock.mockResolvedValue(
      sseResponse([frame('thinking', { step: '取实时数据' }), frame('token', { text: '已流出半句' })]),
    )
    const h = handlers()

    const { sendMessageStream } = await import('./api')
    const outcome = await sendMessageStream('project-supervisor-agent', 'proj-001 进展', h)

    expect(outcome).toEqual({ kind: 'failed', reason: 'stream_incomplete' })
    expect(h.onToken).toHaveBeenCalledWith('已流出半句')
    expect(h.onDone).not.toHaveBeenCalled()
  })

  it('retract 终止帧：透出撤回原因并判为 retracted，不再等 done', async () => {
    fetchMock.mockResolvedValue(
      sseResponse([
        frame('token', { text: '泄漏内容' }),
        frame('retract', { notice: '[内容已撤回]', reason: 'prompt_injection', pattern: 'BEGIN NEW INSTRUCTIONS' }),
        frame('done', { traceId: 'trace-2', status: 'retracted' }),
      ]),
    )
    const h = handlers()

    const { sendMessageStream } = await import('./api')
    const outcome = await sendMessageStream('risk-agent', 'ignore previous instructions', h)

    expect(outcome).toEqual({
      kind: 'retracted',
      notice: '[内容已撤回]',
      reason: 'prompt_injection',
    })
    expect(h.onRetract).toHaveBeenCalledWith({ notice: '[内容已撤回]', reason: 'prompt_injection' })
    expect(h.onDone).not.toHaveBeenCalled()
  })

  it('error 终止帧按后端 reason 返回 failed', async () => {
    fetchMock.mockResolvedValue(sseResponse([frame('error', { reason: 'llm_stream_failed', detail: '连接被重置' })]))
    const h = handlers()

    const { sendMessageStream } = await import('./api')
    const outcome = await sendMessageStream('risk-agent', '风险概览', h)

    expect(outcome).toEqual({ kind: 'failed', reason: 'llm_stream_failed' })
    expect(h.onError).toHaveBeenCalledWith({ reason: 'llm_stream_failed', detail: '连接被重置' })
  })

  it('HTTP 非 2xx 直接判失败，不派发任何帧回调', async () => {
    fetchMock.mockResolvedValue(sseResponse([], 500))
    const h = handlers()

    const { sendMessageStream } = await import('./api')
    const outcome = await sendMessageStream('risk-agent', '风险概览', h)

    expect(outcome).toEqual({ kind: 'failed', reason: 'http_500' })
    expect(h.onToken).not.toHaveBeenCalled()
  })

  it('401 走静默刷新后重试一次，第二次带上轮换出的新 token', async () => {
    sessionStorage.setItem('erp-refresh-token', 'RT-1')
    axiosDefault.post.mockResolvedValueOnce({
      data: { access_token: 'AT-2', refresh_token: 'RT-2' },
    })
    fetchMock
      .mockResolvedValueOnce({ ok: false, status: 401, body: null } as unknown as Response)
      .mockResolvedValueOnce(sseResponse([frame('done', { traceId: 'trace-3', status: 'completed' })]))
    const h = handlers()

    const { sendMessageStream } = await import('./api')
    const outcome = await sendMessageStream('risk-agent', '风险概览', h)

    expect(outcome).toEqual({ kind: 'completed' })
    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(fetchMock.mock.calls[1][1].headers.Authorization).toBe('Bearer AT-2')
    expect(axiosDefault.post).toHaveBeenCalledTimes(1)
  })

  it('刷新不出新 token 就判 unauthorized，不再空转重试', async () => {
    useAuthStore.setState({ refreshToken: null })
    fetchMock.mockResolvedValue({ ok: false, status: 401, body: null } as unknown as Response)

    const { sendMessageStream } = await import('./api')
    const outcome = await sendMessageStream('risk-agent', '风险概览', handlers())

    expect(outcome).toEqual({ kind: 'failed', reason: 'unauthorized' })
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })
})
