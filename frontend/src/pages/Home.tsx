/**
 * 首页（对话框 + 待办卡片，PRD FR-T1）
 */
import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import api, { clearToken } from '../lib/api'
import { useAppStore, ROLE_LABELS } from '../store'

interface Todo { id: number; source: string; title: string; link: string }

export default function Home() {
  const { user, clearUser } = useAppStore()
  const [message, setMessage] = useState('')
  const [todos, setTodos] = useState<Todo[]>([])
  const [reply, setReply] = useState('')

  useEffect(() => {
    api.get('/todos').then(r => setTodos(r.items)).catch(() => {})
  }, [])

  const send = async () => {
    if (!message.trim()) return
    try {
      const r = await api.post('/conversation/chat', { message })
      setReply(r.reply || r.content || '已处理')
    } catch { setReply('发送失败') }
  }

  return (
    <div className="min-h-screen bg-canvas">
      {/* 顶栏 */}
      <header className="bg-navy text-white px-6 py-3 flex items-center justify-between">
        <span className="font-semibold">企业数字化大脑</span>
        <div className="flex items-center gap-3 text-xs">
          <span>{user?.display_name}（{ROLE_LABELS[user?.role || ''] || user?.role}）</span>
          <button onClick={() => { clearToken(); clearUser(); window.location.href = '/login' }}
                  className="underline opacity-70 hover:opacity-100">退出</button>
        </div>
      </header>
      <div className="max-w-4xl mx-auto px-4 py-6 space-y-6">
        {/* 对话框 */}
        <div className="bg-card rounded-lg border border-border p-4">
          <textarea value={message} onChange={e => setMessage(e.target.value)}
                    placeholder="输入您的问题或指令…" rows={3}
                    className="w-full border border-border rounded p-3 text-sm resize-none"/>
          <button onClick={send}
                  className="mt-2 bg-navy text-white px-4 py-1.5 rounded text-sm hover:bg-navy-dark">发送</button>
          {reply && <div className="mt-3 p-3 bg-canvas rounded text-sm">{reply}</div>}
        </div>
        {/* 待办卡片（FR-T1） */}
        <div>
          <h2 className="text-sm font-semibold text-ink mb-2">待办事项</h2>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
            {todos.length === 0 && <p className="text-xs text-slate2">暂无待办</p>}
            {todos.map(t => (
              <div key={t.id} className="bg-card rounded border border-border p-3 text-xs">
                <span className="inline-block bg-accent/10 text-accent rounded px-1.5 py-0.5 mb-1">{t.source}</span>
                <p className="font-medium">{t.title}</p>
                {t.link && <a href={t.link} className="text-accent underline">处理</a>}
              </div>
            ))}
          </div>
        </div>
        {/* 快捷入口 */}
        <nav className="grid grid-cols-2 md:grid-cols-4 gap-3">
          {[
            { to: '/knowledge', label: '知识库' }, { to: '/records', label: '资料库' },
            { to: '/im', label: '消息' }, { to: '/market', label: '能力市场' },
            { to: '/dashboard', label: '看板' }, { to: '/admin', label: '管理台' },
          ].map(l => (
            <Link key={l.to} to={l.to}
                  className="bg-card border border-border rounded-lg p-4 text-center text-sm font-medium hover:border-navy">{l.label}</Link>
          ))}
        </nav>
      </div>
    </div>
  )
}
