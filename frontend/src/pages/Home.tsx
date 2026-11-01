/**
 * 首页（AI 对话框 + 待办卡片，PRD FR-T1）；导航由 Shell 侧边栏承担。
 */
import { useEffect, useState } from 'react'
import { Send } from 'lucide-react'
import api from '../lib/api'
import { useAppStore } from '../store'

interface Todo { id: number; source: string; title: string; link: string }

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
    <div className="p-6 space-y-6 max-w-5xl">
      <div>
        <h1 className="text-lg font-semibold text-ink">你好，{user?.display_name}</h1>
        <p className="text-sm text-slate2 mt-0.5">今天有什么可以帮您？</p>
      </div>

      {/* AI 对话框 */}
      <div className="bg-card rounded-lg border border-border p-4">
        <textarea value={message} onChange={e => setMessage(e.target.value)}
                  onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send() } }}
                  placeholder="输入您的问题或指令…（Enter 发送，Shift+Enter 换行）" rows={3}
                  className="w-full border border-border rounded p-3 text-sm resize-none focus:outline-none focus:ring-1 focus:ring-navy"/>
        <div className="mt-2 flex justify-end">
          <button onClick={send} disabled={sending || !message.trim()}
                  className="flex items-center gap-1.5 bg-navy text-white px-4 py-1.5 rounded text-sm hover:bg-navy-dark disabled:opacity-50">
            <Send size={14} /> {sending ? '思考中…' : '发送'}
          </button>
        </div>
        {reply && (
          <div className="mt-3 p-3 bg-canvas rounded text-sm whitespace-pre-wrap border border-border">{reply}</div>
        )}
      </div>

      {/* 待办卡片（FR-T1） */}
      <div>
        <h2 className="text-sm font-semibold text-ink mb-2">待办事项</h2>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          {todos.length === 0 && <p className="text-xs text-slate2">暂无待办</p>}
          {todos.map(t => (
            <div key={t.id} className="bg-card rounded border border-border p-3 text-xs hover:border-navy transition-colors">
              <span className="inline-block bg-accent/10 text-accent rounded px-1.5 py-0.5 mb-1">{t.source}</span>
              <p className="font-medium">{t.title}</p>
              {t.link && <a href={t.link} className="text-accent underline">处理</a>}
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
