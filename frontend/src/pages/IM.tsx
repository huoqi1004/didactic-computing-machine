/**
 * IM 页（M11：数字分身私聊 / 公司全员群 / 部门群 / 员工单聊 / 安全告警）。
 * 左侧频道分组展示 + 发起单聊选人；右侧统一会话视图。
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { Bell, Bot, MessageSquarePlus, Send, Shield, Users } from 'lucide-react'
import api from '../lib/api'

interface Channel { id: number; type: string; name: string; category: string; department?: string }
interface Message { id: number; sender_type: string; sender_id: string; content: string }
interface UserRow { username: string; display_name: string; department: string; role: string }

const GROUPS = [
  { key: '数字分身', label: '数字分身', icon: Bot },
  { key: '群聊', label: '群聊（全员/部门）', icon: Users },
  { key: '单聊', label: '员工单聊', icon: MessageSquarePlus },
  { key: '安全告警', label: '安全告警', icon: Shield },
]

export default function IM() {
  const [channels, setChannels] = useState<Channel[]>([])
  const [active, setActive] = useState<number | null>(null)
  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState('')
  const [pickerOpen, setPickerOpen] = useState(false)
  const [users, setUsers] = useState<UserRow[]>([])
  const [readErr, setReadErr] = useState('')
  const bottomRef = useRef<HTMLDivElement>(null)

  const load = useCallback(async () => {
    try { const r = await api.get('/im/channels'); setChannels(r.items || []) } catch {}
  }, [])
  useEffect(() => { load() }, [load])

  const openChannel = useCallback(async (id: number) => {
    setActive(id)
    setReadErr('')
    try {
      const r = await api.get(`/im/${id}/messages`)
      setMessages(r.items || [])
    } catch (e: any) {
      setMessages([])
      setReadErr(e?.response?.data?.detail || '无权查看')
    }
  }, [])
  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: 'smooth' }) }, [messages])

  const openPicker = async () => {
    setPickerOpen(true)
    try { const r = await api.get('/im/users'); setUsers(r.items || []) } catch {}
  }

  const startDm = async (username: string) => {
    try {
      const ch = await api.post(`/im/dm/${username}`)
      setPickerOpen(false)
      await load()
      openChannel(ch.id)
    } catch (e: any) { alert(e?.response?.data?.detail || '发起单聊失败') }
  }

  const send = async () => {
    if (!input.trim() || active === null) return
    try {
      await api.post(`/im/${active}/messages`, { content: input })
      setInput('')
      openChannel(active)
    } catch (e: any) { alert(e?.response?.data?.detail || '发送失败') }
  }

  const activeCh = channels.find(c => c.id === active)
  const grouped = GROUPS.map(g => ({ ...g, items: channels.filter(c => c.category === g.key) }))

  return (
    <div className="h-full bg-canvas flex">
      {/* ===== 频道栏 ===== */}
      <div className="w-64 shrink-0 bg-card border-r border-border p-3 overflow-y-auto">
        <div className="flex items-center justify-between mb-2">
          <h2 className="font-semibold text-sm text-ink">频道</h2>
          <button onClick={openPicker} title="发起员工单聊"
                  className="flex items-center gap-1 text-[11px] bg-navy text-white rounded-lg px-2 py-1 hover:bg-navy-dark">
            <MessageSquarePlus size={12} /> 单聊
          </button>
        </div>
        {grouped.map(g => g.items.length > 0 && (
          <div key={g.key} className="mb-3">
            <p className="text-[10px] text-slate2 font-medium px-1 mb-1 flex items-center gap-1">
              <g.icon size={10} /> {g.label}（{g.items.length}）
            </p>
            {g.items.map(ch => (
              <button key={ch.id} onClick={() => openChannel(ch.id)}
                      className={`w-full text-left px-2 py-1.5 rounded-lg text-xs mb-0.5 transition-colors ${
                        active === ch.id ? 'bg-navy text-white' : 'text-ink hover:bg-canvas'}`}>
                {ch.type === 'dm' || ch.type === 'avatar_private' ? '' : ''}
                {ch.name || `频道#${ch.id}`}
              </button>
            ))}
          </div>
        ))}
        {channels.length === 0 && <p className="text-xs text-slate2 p-2">暂无可见频道</p>}
      </div>

      {/* ===== 会话区 ===== */}
      <div className="flex-1 flex flex-col min-w-0">
        {active === null && (
          <div className="flex-1 flex items-center justify-center text-sm text-slate2">
            从左侧选择频道开始会话，或点击「单聊」发起同事对话
          </div>
        )}
        {active !== null && (
          <>
            <div className="h-12 shrink-0 bg-card border-b border-border px-4 flex items-center gap-2">
              <span className="text-sm font-semibold text-ink">{activeCh?.name}</span>
              {activeCh?.type === 'avatar_private' && <span className="text-[10px] bg-blue-50 text-navy rounded px-1.5 py-0.5">数字分身对话</span>}
              {activeCh?.type === 'dm' && <span className="text-[10px] bg-cyan-50 text-cyan-600 rounded px-1.5 py-0.5">员工单聊</span>}
              {activeCh?.type === 'company' && <span className="text-[10px] bg-indigo-50 text-indigo-600 rounded px-1.5 py-0.5">公司全员群</span>}
              {activeCh?.type === 'department' && <span className="text-[10px] bg-sky-50 text-sky-600 rounded px-1.5 py-0.5">部门群</span>}
            </div>
            <div className="flex-1 p-4 space-y-2.5 overflow-y-auto bg-card">
              {readErr && <p className="text-xs text-alert">{readErr}</p>}
              {messages.map(m => (
                <div key={m.id} className={`flex gap-2 ${m.sender_type === 'user' && m.sender_id === localStorage.getItem('erp-username') ? '' : ''}`}>
                  <span className={`w-7 h-7 rounded-full flex items-center justify-center text-[11px] shrink-0 mt-0.5 ${
                    m.sender_type === 'system' ? 'bg-alert/10 text-alert'
                    : m.sender_type === 'avatar' ? 'bg-gradient-brand text-white' : 'bg-steel/20 text-navy'}`}>
                    {m.sender_type === 'system' ? <Bell size={12} />
                     : m.sender_type === 'avatar' ? <Bot size={12} />
                     : (m.sender_id || '?')[0]}
                  </span>
                  <div className="max-w-2xl">
                    <p className="text-[11px] text-slate2">
                      {m.sender_type === 'system' ? '系统' : m.sender_type === 'avatar' ? `数字分身 · ${m.sender_id}` : m.sender_id}
                    </p>
                    <div className={`inline-block rounded-xl px-3 py-2 text-sm whitespace-pre-wrap ${
                      m.sender_type === 'system' ? 'bg-red-50 text-alert border border-red-100'
                      : 'bg-canvas text-ink border border-border'}`}>
                      {m.content}
                    </div>
                  </div>
                </div>
              ))}
              {messages.length === 0 && !readErr && <p className="text-xs text-slate2">暂无消息，来打个招呼吧</p>}
              <div ref={bottomRef} />
            </div>
            <div className="p-3 border-t border-border flex gap-2">
              <input value={input} onChange={e => setInput(e.target.value)}
                     onKeyDown={e => e.key === 'Enter' && send()}
                     className="flex-1 border border-border rounded-lg px-3 py-2 text-sm focus:border-steel-light"
                     placeholder="输入消息…（Enter 发送）"/>
              <button onClick={send} className="bg-accent text-white rounded-lg px-4 text-sm hover:opacity-90">发送</button>
            </div>
          </>
        )}
      </div>

      {/* 单聊选人弹窗 */}
      {pickerOpen && (
        <div className="fixed inset-0 z-50 bg-ink/40 backdrop-blur-sm flex items-center justify-center p-4"
             onClick={() => setPickerOpen(false)}>
          <div className="w-full max-w-sm bg-card rounded-2xl shadow-lift border border-border p-5"
               onClick={e => e.stopPropagation()}>
            <h3 className="font-semibold text-ink mb-3">发起员工单聊</h3>
            <div className="max-h-72 overflow-y-auto space-y-1">
              {users.map(u => (
                <button key={u.username} onClick={() => startDm(u.username)}
                        className="w-full flex items-center gap-2.5 px-3 py-2 rounded-lg hover:bg-canvas text-left">
                  <span className="w-7 h-7 rounded-full bg-steel/20 text-navy flex items-center justify-center text-xs">
                    {(u.display_name || u.username)[0]}
                  </span>
                  <span className="min-w-0">
                    <span className="block text-sm text-ink truncate">{u.display_name}（{u.username}）</span>
                    <span className="block text-[11px] text-slate2">{u.department || '未分配'}</span>
                  </span>
                </button>
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
