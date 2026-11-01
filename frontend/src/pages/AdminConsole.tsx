/** 管理台页（sys_admin：用户管理/日志/参数）。 */
import { useCallback, useEffect, useState } from 'react'
import api from '../lib/api'
import { useAppStore } from '../store'

interface UserRow { id: number; username: string; display_name: string; role: string; department: string; status: string }
interface LogRow { entry_id: string; actor: string; action: string; result: string; timestamp: string }

export default function AdminConsole() {
  const [tab, setTab] = useState('users')
  const [users, setUsers] = useState<UserRow[]>([])
  const [logs, setLogs] = useState<LogRow[]>([])

  const load = useCallback(async (t = tab) => {
    try {
      if (t === 'users') { const r = await api.get('/admin/users'); setUsers(r.items || []) }
      if (t === 'logs') { const r = await api.get('/admin/logs'); setLogs(r.items || []) }
    } catch {}
  }, [tab])
  useEffect(() => { load() }, [load])

  return (
    <div className="min-h-screen bg-canvas p-6">
      <h1 className="text-lg font-bold text-ink mb-4">系统管理台</h1>
      <div className="flex gap-2 mb-4">
        <button onClick={() => setTab('users')} className={`px-3 py-1.5 rounded text-sm ${tab === 'users' ? 'bg-navy text-white' : 'bg-card border border-border'}`}>用户管理</button>
        <button onClick={() => setTab('logs')} className={`px-3 py-1.5 rounded text-sm ${tab === 'logs' ? 'bg-navy text-white' : 'bg-card border border-border'}`}>审计日志</button>
      </div>
      {tab === 'users' && (
        <table className="w-full bg-card border border-border rounded text-sm">
          <thead><tr className="bg-canvas"><th className="p-2 text-left">用户名</th><th className="p-2 text-left">角色</th><th className="p-2 text-left">状态</th></tr></thead>
          <tbody>{users.map(u => <tr key={u.id} className="border-t border-border"><td className="p-2">{u.username}</td><td className="p-2">{u.role}</td><td className="p-2">{u.status}</td></tr>)}</tbody>
        </table>
      )}
      {tab === 'logs' && (
        <div className="bg-card border border-border rounded p-3 max-h-96 overflow-y-auto">
          {logs.map(l => <p key={l.entry_id} className="text-xs font-mono">{l.timestamp?.slice(0,19)} [{l.action}] {l.actor} → {l.result}</p>)}
        </div>
      )}
    </div>
  )
}
