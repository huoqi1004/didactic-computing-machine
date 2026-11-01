/**
 * 对话降级显性化、创作附件与流式渲染测试（工作包一 / 工作包三，裁决 D-A、D-C、D-D）
 * ==================================================================
 * 1. degraded 回复必须带可见徽标，人工队列与规则降级文案不同——
 *    用户要能分辨"这是兜底"还是"这是 Agent 的正式回答"；
 * 2. dataSource=agent_native 要说明数据来自 Agent 本体；
 * 3. 附件下载必须走鉴权端点（apiService.downloadDocument）再触发浏览器下载，
 *    空 Blob（文件不存在/被清理）要报错，不能静默；
 * 4. store 必须把后端降级字段逐条带进消息，丢字段就等于把兜底冒充成正式回答；
 * 5. 流式通道：帧序要落进同一条气泡且 done 不得回写正文，半截回答不进历史对话，
 *    撤回要截断留痕，断线交回一次性重答（streamFallback ≠ degraded）。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'

import { apiService, sendMessageStream, type ChatStreamHandlers, type ChatStreamOutcome } from '../services/api'
import { useSystemStore } from '../stores/systemStore'
import type { ChatMessage } from '../stores/systemStore'
import { MessageBubble } from '../pages/Conversation'

vi.mock('../services/api', () => ({
  apiService: {
    sendMessage: vi.fn(),
    downloadDocument: vi.fn(),
  },
  sendMessageStream: vi.fn(),
}))

const base = {
  id: 'msg-1',
  role: 'agent' as const,
  agentId: 'project-supervisor-agent',
  agentName: '项目监理agent',
  content: '实时监理数据：在监 3 个项目……',
  timestamp: new Date().toISOString(),
}

function renderBubble(override: Partial<ChatMessage> = {}) {
  const message: ChatMessage = { ...base, ...override }
  return render(<MessageBubble message={message} agentIcon="ClipboardCheck" />)
}

describe('MessageBubble 降级徽标', () => {
  beforeEach(() => {
    vi.mocked(apiService.downloadDocument).mockReset()
  })

  it('未降级的回复不显示降级徽标', () => {
    renderBubble({ degraded: false })
    expect(screen.queryByTestId('degraded-badge')).toBeNull()
  })

  it('查询类降级显示"规则降级回复"并点明数据源', () => {
    renderBubble({
      degraded: true,
      degradedReason: 'llm_unavailable',
      dataSource: 'agent_native',
    })
    expect(screen.getByTestId('degraded-badge')).toHaveTextContent('规则降级回复')
    expect(screen.getByText('原因：模型通道未启用')).toBeInTheDocument()
    expect(screen.getByText('数据源：Agent 本体实时数据')).toBeInTheDocument()
  })

  it('取不到实时数据时明示未给出业务结论', () => {
    renderBubble({ degraded: true, degradedReason: 'llm_failed', dataSource: 'unavailable' })
    expect(screen.getByText('原因：模型调用失败')).toBeInTheDocument()
    expect(screen.getByText('实时数据不可用，未给出业务结论')).toBeInTheDocument()
  })

  it('创作类降级显示"已转人工队列"并给出待办编号', () => {
    renderBubble({
      degraded: true,
      degradedReason: 'doc_failed',
      manualQueue: true,
      todoId: 'todo:abcdef123456',
      content: '创作服务暂不可用（doc_failed），本次未生成文档草稿。',
    })
    expect(screen.getByTestId('degraded-badge')).toHaveTextContent('服务暂不可用，已转人工队列')
    expect(screen.getByText('待办 todo:abcdef123456')).toBeInTheDocument()
  })
})

describe('MessageBubble 创作附件下载', () => {
  const attachment = {
    name: 'ERP系统实施项目监理大纲',
    filename: 'ERP系统实施项目监理大纲-20261004170000-abc123.docx',
    url: '/api/v1/document/download/ERP.docx',
    format: 'docx',
    size: 40960,
  }

  beforeEach(() => {
    vi.mocked(apiService.downloadDocument).mockReset()
    Object.defineProperty(URL, 'createObjectURL', {
      value: vi.fn(() => 'blob:mock'), configurable: true,
    })
    Object.defineProperty(URL, 'revokeObjectURL', {
      value: vi.fn(), configurable: true,
    })
  })

  it('点击附件经鉴权端点取 Blob 后触发下载', async () => {
    const blob = new Blob(['x'], { type: 'application/octet-stream' })
    vi.mocked(apiService.downloadDocument).mockResolvedValue(blob)

    renderBubble({ attachments: [attachment], traceId: 'trace-xyz', degraded: false })
    fireEvent.click(screen.getByText('ERP系统实施项目监理大纲'))

    await waitFor(() => {
      expect(apiService.downloadDocument).toHaveBeenCalledWith(attachment.filename)
      expect(URL.createObjectURL).toHaveBeenCalledWith(blob)
    })
    expect(screen.getByText('trace_id trace-xyz')).toBeInTheDocument()
    expect(screen.getByText(/AI 生成草稿 · 40.0 KB/)).toBeInTheDocument()
  })

  it('下载拿到空文件要报错，不能静默失败', async () => {
    vi.mocked(apiService.downloadDocument).mockResolvedValue(new Blob())

    renderBubble({ attachments: [attachment] })
    fireEvent.click(screen.getByText('ERP系统实施项目监理大纲'))

    await waitFor(() => {
      expect(screen.getByText(`下载失败：${attachment.filename} 不存在或已被清理`)).toBeInTheDocument()
    })
    expect(URL.createObjectURL).not.toHaveBeenCalled()
  })

  it('请求异常时提示稍后重试', async () => {
    vi.mocked(apiService.downloadDocument).mockRejectedValue(new Error('network'))

    renderBubble({ attachments: [attachment] })
    fireEvent.click(screen.getByText('ERP系统实施项目监理大纲'))

    await waitFor(() => {
      expect(screen.getByText('下载失败，请稍后重试')).toBeInTheDocument()
    })
  })
})

describe('MessageBubble 流式状态', () => {
  it('边到边渲染时给出流式指示，等下一次 token 落进同一条气泡', () => {
    renderBubble({ status: 'sending', content: '实时监理数据：在监 3 个' })
    expect(screen.getByTestId('streaming-caret')).toBeInTheDocument()
  })

  it('护栏撤回：正文已截断为终止文案，界面要说明命中了什么模式', () => {
    renderBubble({
      content: '[内容已撤回]',
      retracted: true,
      retractReason: 'prompt_injection',
      status: 'sent',
    })
    const notice = screen.getByTestId('retract-notice')
    expect(notice).toHaveTextContent('输出护栏已撤回本条回答')
    expect(notice).toHaveTextContent('命中模式：prompt_injection')
    // 撤回是护栏动作，不是规则兜底，不该同时挂降级徽标
    expect(screen.queryByTestId('degraded-badge')).toBeNull()
  })

  it('断线一次性重答：说明换了通道，但不冒充模型降级', () => {
    renderBubble({
      content: '一次性重答的完整回答',
      streamFallback: true,
      streamFallbackReason: 'stream_incomplete',
      status: 'sent',
    })
    expect(screen.getByTestId('stream-fallback')).toHaveTextContent('流式通道中断')
    expect(screen.queryByTestId('degraded-badge')).toBeNull()
  })
})

describe('systemStore 流式对话', () => {
  const card = {
    type: 'table' as const,
    title: '在监项目',
    columns: [{ key: 'project', label: '项目' }],
    rows: [{ project: 'proj-001' }],
  }

  beforeEach(() => {
    localStorage.clear()
    vi.mocked(sendMessageStream).mockReset()
    vi.mocked(apiService.sendMessage).mockReset()
    useSystemStore.setState({
      activeAgentId: 'project-supervisor-agent',
      chatAgents: [{
        id: 'project-supervisor-agent',
        name: '项目监理agent',
        description: '',
        status: 'running',
        icon: 'ClipboardCheck',
      } as never],
      conversationMessages: [],
      conversationLoading: false,
    })
  })

  /** 驱动一次流：按序回调帧 → 返回终止状态，返回落到消息列表末条的最终态 */
  async function sendWith(
    drive: (handlers: ChatStreamHandlers) => void,
    outcome: ChatStreamOutcome,
    text = '在监项目进展如何？',
  ) {
    vi.mocked(sendMessageStream).mockImplementation(async (_agentId, _message, handlers) => {
      drive(handlers)
      return outcome
    })
    await useSystemStore.getState().sendMessage(text)
    return useSystemStore.getState().conversationMessages.slice(-1)[0]
  }

  const persistedHistory = () => {
    const raw = localStorage.getItem('erp_conversation_history')
    const all = raw ? JSON.parse(raw) : {}
    return (all['project-supervisor-agent'] || []) as ChatMessage[]
  }

  it('帧序逐段落进同一条消息，done 帧不得覆盖已流出的正文', async () => {
    const last = await sendWith((h) => {
      h.onThinking('识别意图：查询类')
      h.onToken('在监 3 个项目，')
      h.onToken('proj-001 预算使用率 40.0%')
      h.onDataCard(card)
      // done 帧只带元数据：content 为空、thinkingProcess/dataCard 缺省
      h.onDone({
        content: '',
        thinkingProcess: [],
        traceId: 'trace-9',
        degraded: false,
        intent: 'query',
        classifier: 'llm',
        dataSource: 'agent_native',
      })
    }, { kind: 'completed' })

    expect(last.content).toBe('在监 3 个项目，proj-001 预算使用率 40.0%')
    expect(last.thinkingProcess).toEqual(['识别意图：查询类'])
    expect(last.dataCard).toEqual(card)
    expect(last.traceId).toBe('trace-9')
    expect(last.degraded).toBe(false)
    expect(last.dataSource).toBe('agent_native')
    expect(last.status).toBe('sent')
    expect(useSystemStore.getState().conversationLoading).toBe(false)
  })

  it('done 帧把降级/人工队列/附件字段逐条带进消息（裁决 D-A 在流式通道同样成立）', async () => {
    const last = await sendWith((h) => {
      h.onToken('创作服务暂不可用，已转人工队列')
      h.onDone({
        content: '',
        thinkingProcess: [],
        degraded: true,
        degradedReason: 'doc_failed',
        manualQueue: true,
        todoId: 'todo:abcdef123456',
        dataSource: 'unavailable',
        attachments: [{
          name: '监理大纲', filename: 'a.docx', url: '/api/v1/document/download/a.docx',
          format: 'docx', size: 1024,
        }],
      })
    }, { kind: 'completed' })

    expect(last.degraded).toBe(true)
    expect(last.degradedReason).toBe('doc_failed')
    expect(last.manualQueue).toBe(true)
    expect(last.todoId).toBe('todo:abcdef123456')
    expect(last.attachments?.[0].filename).toBe('a.docx')
  })

  it('半截回答不进历史对话，终止后才持久化', async () => {
    let midStream: string[] = []
    const last = await sendWith((h) => {
      h.onToken('还没说完的回答')
      midStream = persistedHistory().map((m) => m.content)
    }, { kind: 'completed' })

    expect(midStream).toEqual(['在监项目进展如何？'])
    expect(persistedHistory().slice(-1)[0].content).toBe(last.content)
  })

  it('护栏撤回：正文截断为终止帧文案并留命中原因，不再走重答', async () => {
    const last = await sendWith((h) => {
      h.onToken('已泄漏的前半段')
      h.onRetract({ notice: '[内容已撤回]', reason: 'prompt_injection' })
    }, { kind: 'retracted', notice: '[内容已撤回]', reason: 'prompt_injection' })

    expect(last.content).toBe('[内容已撤回]')
    expect(last.retracted).toBe(true)
    expect(last.retractReason).toBe('prompt_injection')
    expect(apiService.sendMessage).not.toHaveBeenCalled()
  })

  it('流中断走一次性重答：标 streamFallback，不冒充模型降级', async () => {
    vi.mocked(apiService.sendMessage).mockResolvedValue({
      content: '一次性重答的完整回答',
      thinkingProcess: ['装配实时数据'],
      intent: 'query',
      traceId: 'trace-10',
    })

    const last = await sendWith(
      (h) => h.onToken('断在半路的内容'),
      { kind: 'failed', reason: 'stream_incomplete' },
    )

    expect(apiService.sendMessage).toHaveBeenCalledWith('project-supervisor-agent', '在监项目进展如何？')
    expect(last.content).toBe('一次性重答的完整回答')
    expect(last.thinkingProcess).toEqual(['装配实时数据'])
    expect(last.streamFallback).toBe(true)
    expect(last.streamFallbackReason).toBe('stream_incomplete')
    // 通道降级 ≠ 模型降级：degraded 由后端一次性回复自己决定，这里不替它写
    expect(last.degraded).toBeUndefined()
    expect(last.status).toBe('sent')
  })

  it('重答也失败才标 error，占位消息不能停在"发送中"', async () => {
    vi.mocked(apiService.sendMessage).mockRejectedValue(new Error('boom'))

    const last = await sendWith(
      (h) => h.onToken('断在半路的内容'),
      { kind: 'failed', reason: 'http_503' },
    )

    expect(last.status).toBe('error')
    expect(last.content).toBe('抱歉，我暂时无法响应，请稍后重试。')
    expect(useSystemStore.getState().conversationLoading).toBe(false)
  })
})
