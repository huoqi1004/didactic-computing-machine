/** IM 页（FR-M1~M4：全员群/部门群/数字分身私聊）。 */
import { useCallback, useEffect, useState } from 'react'
import api from '../lib/api'
import { useAppStore } from '../store'

interface Channel { id: number; type: string; name: string; department: string }
interface Message { id: number; sender_type: string; sender_id: string; content: string }

const TYPE_LABELS: Record<string, string> = {
  company: '全员群', department: '部门群', avatar_private: '分身私聊', admin_alert: '安全告警',
}

export default function IM() {
  const user = useAppStore(s => s.user)
  const [channels, setChannels] = useState<Channel[]>([])
  const [active, setActive] = useState<number | null>(null)
  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState('')

  const load = useCallback(async () => {
    try { const r = await api.get('/im/channels'); setChannels(r.items || []) } catch {}
  }, [])
  useEffect(() => { load() }, [load])

  const openChannel = async (id: number) => {
    setActive(id)
    try { const r = await api.get(`/im/${id}/messages`); setMessages(r.items || []) } catch {}
  }

  const send = async () => {
    if (!input.trim() || active === null) return
    try { await api.post(`/im/${active}/messages`, { content: input }); setInput(''); openChannel(active) } catch {}
  }

  return (
    <div className="min-h-screen bg-canvas flex">
      <div className="w-64 bg-navy text-white p-3 space-y-1 overflow-y-auto">
        <h2 className="font-semibold text-sm px-2 py-1">消息</h2>
        {channels.map(ch => (
          <button key={ch.id} onClick={() => openChannel(ch.id)}
                  className="w-full text-left px-2 py-1.5 rounded text-xs hover:bg-navy-light">
            [{TYPE_LABELS[ch.type] || ch.type}] {ch.name || `频道#${ch.id}`}
          </button>
        ))}
      </div>
      <div className="flex-1 flex flex-col">
        {active !== null && (
          <>
            <div className="flex-1 p-4 space-y-2 overflow-y-auto bg-card">
              {messages.map(m => (
                <div key={m.id} className="text-sm">
                  <span className="font-medium text-navy">{m.sender_id}</span>
                  <span className="text-slate2 text-xs ml-2">{m.sender_type}</span>
                  <p className="mt-0.5">{m.content}</p>
                </div>
              ))}
            </div>
            <div className="p-3 border-t border-border flex gap-2">
              <input value={input} onChange={e => setInput(e.target.value)}
                     onKeyDown={e => e.key === 'Enter' && send()}
                     className="flex-1 border border-border rounded px-3 py-2 text-sm"
                     placeholder="输入消息…"/>
              <button onClick={send} className="bg-accent text-white rounded px-4 text-sm">发送</button>
            </div>
          </>
        )}
      </div>
    </div>
  )
}
