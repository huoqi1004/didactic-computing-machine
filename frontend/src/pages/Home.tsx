/**
 * 首页（AI 对话框 + 待办卡片，PRD FR-T1）；导航由 Shell 侧边栏承担。
 */
import { useEffect, useState } from 'react'
import { CalendarClock, Send, Sparkles } from 'lucide-react'
import api from '../lib/api'
import { useAppStore } from '../store'

interface Todo { id: number; source: string; title: string; link: string }

// 待办来源 → 彩色徽标（饱满而不喧闹）
const SOURCE_STYLES: Record<string, string> = {
  system: 'bg-blue-50 text-blue-600 border border-blue-100',
  知识库: 'bg-cyan-50 text-cyan-600 border border-cyan-100',
  资料: 'bg-indigo-50 text-indigo-600 border border-indigo-100',
  IM: 'bg-sky-50 text-sky-600 border border-sky-100',
  市场: 'bg-teal-50 text-teal-600 border border-teal-100',
}

export default function Home() {
  const user = useAppStore(s => s.user)
  const [message, setMessage] = useState('')
  const [todos, setTodos] = useState<Todo[]>([])
  const [reply, setReply] = useState('')
  const [sending, setSending] = useState(false)

  useEffect(() => {
    api.get('/todos').then(r => setTodos(r.items)).catch(() => {})
  }, [])

  const send = async () => {
    if (!message.trim() || sending) return
    setSending(true)
    try {
      const r = await api.post('/conversation/chat', { message })
      setReply(r.reply || r.content || '已处理')
    } catch { setReply('发送失败，请稍后重试') }
    finally { setSending(false) }
  }

  return (
    <div className="p-6 lg:p-8 space-y-6 max-w-5xl">
      <div>
        <h1 className="font-display text-xl font-bold text-ink">
          你好，{user?.display_name}
        </h1>
        <p className="text-sm text-slate2 mt-1 flex items-center gap-1.5">
          <Sparkles size={14} className="text-steel-light" />
          今天有什么可以帮您？
        </p>
      </div>

      {/* AI 对话框 */}
      <div className="bg-card rounded-2xl border border-border shadow-soft overflow-hidden">
        <div className="h-1 bg-gradient-brand" />
        <div className="p-5">
          <textarea value={message} onChange={e => setMessage(e.target.value)}
                    onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send() } }}
                    placeholder="输入您的问题或指令…（Enter 发送，Shift+Enter 换行）" rows={3}
                    className="w-full border border-border rounded-xl p-3.5 text-sm resize-none bg-white transition-shadow focus:shadow-glow focus:border-steel-light"/>
          <div className="mt-3 flex justify-end">
            <button onClick={send} disabled={sending || !message.trim()}
                    className="flex items-center gap-1.5 bg-gradient-brand text-white px-5 py-2 rounded-lg text-sm font-medium shadow-glow hover:opacity-90 transition-opacity disabled:opacity-50">
              <Send size={14} /> {sending ? '思考中…' : '发送'}
            </button>
          </div>
          {reply && (
            <div className="mt-4 p-4 bg-canvas rounded-xl text-sm whitespace-pre-wrap border border-border leading-relaxed">{reply}</div>
          )}
        </div>
      </div>

      {/* 待办卡片（FR-T1） */}
      <div>
        <h2 className="text-sm font-semibold text-ink mb-3 flex items-center gap-1.5">
          <CalendarClock size={15} className="text-navy" /> 待办事项
        </h2>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          {todos.length === 0 && (
            <p className="text-xs text-slate2 bg-card border border-dashed border-border rounded-xl p-4">暂无待办</p>
          )}
          {todos.map(t => (
            <div key={t.id} className="card-lift bg-card rounded-xl border border-border shadow-soft p-4 text-xs">
              <span className={`inline-block rounded px-1.5 py-0.5 mb-2 font-medium ${SOURCE_STYLES[t.source] || SOURCE_STYLES.system}`}>
                {t.source}
              </span>
              <p className="font-medium text-ink text-[13px] leading-snug">{t.title}</p>
              {t.link && <a href={t.link} className="text-accent underline mt-1.5 inline-block">处理</a>}
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
