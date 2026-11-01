/** 管理台页（sys_admin/dept_admin：用户管理含注册审核/审计日志）。 */
import { useCallback, useEffect, useState } from 'react'
import api from '../lib/api'
import { ROLE_LABELS, useAppStore } from '../store'

interface UserRow { id: number; username: string; display_name: string; role: string; department: string; status: string }
interface LogRow { entry_id: string; actor: string; action: string; result: string; timestamp: string }

const STATUS_LABELS: Record<string, string> = {
  active: '已激活', pending: '待审核', disabled: '已停用',
}

export default function AdminConsole() {
  const me = useAppStore(s => s.user)
  const [tab, setTab] = useState('users')
  const [users, setUsers] = useState<UserRow[]>([])
  const [logs, setLogs] = useState<LogRow[]>([])
  const [msg, setMsg] = useState('')

  const load = useCallback(async (t = tab) => {
    try {
      if (t === 'users') { const r = await api.get('/admin/users'); setUsers(r.items || []) }
      if (t === 'logs') { const r = await api.get('/admin/logs'); setLogs(r.items || []) }
    } catch {}
  }, [tab])
  useEffect(() => { load() }, [load])

  const setStatus = async (u: UserRow, status: string) => {
    setMsg('')
    try {
      await api.patch(`/admin/users/${u.id}/status`, { status })
      setMsg(`${u.username} 已${status === 'active' ? '激活' : '停用'}`)
      load('users')
    } catch (e: any) {
      setMsg(e?.response?.data?.detail || '操作失败')
    }
  }

  return (
    <div className="p-6">
      <h1 className="text-lg font-bold text-ink mb-4">系统管理台</h1>
      <div className="flex gap-2 mb-4">
        <button onClick={() => setTab('users')} className={`px-3 py-1.5 rounded text-sm ${tab === 'users' ? 'bg-navy text-white' : 'bg-card border border-border'}`}>用户管理</button>
        <button onClick={() => setTab('logs')} className={`px-3 py-1.5 rounded text-sm ${tab === 'logs' ? 'bg-navy text-white' : 'bg-card border border-border'}`}>审计日志</button>
      </div>
      {msg && <p className="text-xs text-accent mb-2">{msg}</p>}
      {tab === 'users' && (
        <table className="w-full bg-card border border-border rounded text-sm">
          <thead>
            <tr className="bg-canvas">
              <th className="p-2 text-left">用户名</th><th className="p-2 text-left">姓名</th>
              <th className="p-2 text-left">角色</th><th className="p-2 text-left">部门</th>
              <th className="p-2 text-left">状态</th><th className="p-2 text-left">操作</th>
            </tr>
          </thead>
          <tbody>
            {users.map(u => (
              <tr key={u.id} className="border-t border-border">
                <td className="p-2 font-medium">{u.username}</td>
                <td className="p-2">{u.display_name}</td>
                <td className="p-2">{ROLE_LABELS[u.role] || u.role}</td>
                <td className="p-2">{u.department || '—'}</td>
                <td className="p-2">
                  <span className={u.status === 'active' ? 'text-ok' : u.status === 'pending' ? 'text-warn' : 'text-alert'}>
                    {STATUS_LABELS[u.status] || u.status}
                  </span>
                </td>
                <td className="p-2 space-x-2">
                  {u.status !== 'active' && me?.role === 'sys_admin' && (
                    <button onClick={() => setStatus(u, 'active')} className="text-accent underline text-xs">激活</button>
                  )}
                  {u.status === 'active' && u.role !== 'sys_admin' && me?.role === 'sys_admin' && (
                    <button onClick={() => setStatus(u, 'disabled')} className="text-alert underline text-xs">停用</button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {tab === 'logs' && (
        <div className="bg-card border border-border rounded p-3 max-h-96 overflow-y-auto">
          {logs.length === 0 && <p className="text-xs text-slate2">暂无日志</p>}
          {logs.map(l => <p key={l.entry_id} className="text-xs font-mono">{l.timestamp?.slice(0,19)} [{l.action}] {l.actor} → {l.result}</p>)}
        </div>
      )}
    </div>
  )
}
