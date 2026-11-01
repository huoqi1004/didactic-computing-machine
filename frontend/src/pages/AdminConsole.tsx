/** 管理台页（sys_admin/dept_admin：用户管理/审计日志/模型接入/额度管理）。 */
import { useCallback, useEffect, useState } from 'react'
import { Bot, Coins, KeyRound, Plus } from 'lucide-react'
import api from '../lib/api'
import { ROLE_LABELS, useAppStore } from '../store'

interface UserRow { id: number; username: string; display_name: string; role: string; department: string; status: string }
interface LogRow { entry_id: string; actor: string; action: string; result: string; timestamp: string }
interface ModelRow { id: number; name: string; protocol: string; base_url: string; api_key_masked: string; model_name: string; enabled: boolean; is_default: boolean }
interface QuotaRow { username: string; display_name: string; department: string; daily_limit: number; enabled: boolean; used_today: number; remaining: number }

const STATUS_LABELS: Record<string, string> = {
  active: '已激活', pending: '待审核', disabled: '已停用',
}
const TABS = [
  ['users', '用户管理'], ['models', '模型接入'], ['quotas', '额度管理'], ['logs', '审计日志'],
] as const

export default function AdminConsole() {
  const me = useAppStore(s => s.user)
  const [tab, setTab] = useState<string>('users')
  const [users, setUsers] = useState<UserRow[]>([])
  const [logs, setLogs] = useState<LogRow[]>([])
  const [models, setModels] = useState<ModelRow[]>([])
  const [quotas, setQuotas] = useState<QuotaRow[]>([])
  const [msg, setMsg] = useState('')

  const load = useCallback(async (t = tab) => {
    try {
      if (t === 'users') { const r = await api.get('/admin/users'); setUsers(r.items || []) }
      if (t === 'logs') { const r = await api.get('/admin/logs'); setLogs(r.items || []) }
      if (t === 'models') { const r = await api.get('/admin/models'); setModels(r.items || []) }
      if (t === 'quotas') { const r = await api.get('/admin/quotas'); setQuotas(r.items || []) }
    } catch {}
  }, [tab])
  useEffect(() => { load() }, [load])

  const setStatus = async (u: UserRow, status: string) => {
    setMsg('')
    try {
      await api.patch(`/admin/users/${u.id}/status`, { status })
      setMsg(`${u.username} 已${status === 'active' ? '激活' : '停用'}`)
      load('users')
    } catch (e: any) { setMsg(e?.response?.data?.detail || '操作失败') }
  }

  return (
    <div className="h-full overflow-y-auto p-6">
      <h1 className="font-display text-lg font-bold text-ink mb-4">系统管理台</h1>
      <div className="flex gap-2 mb-4">
        {TABS.map(([k, label]) => (
          <button key={k} onClick={() => setTab(k)}
                  className={`px-3 py-1.5 rounded text-sm ${tab === k ? 'bg-gradient-brand text-white shadow-glow' : 'bg-card border border-border text-slate2 hover:text-navy'}`}>
            {label}
          </button>
        ))}
      </div>
      {msg && <p className="text-xs text-accent mb-2">{msg}</p>}

      {tab === 'users' && <UsersTab users={users} me={me} setStatus={setStatus} />}
      {tab === 'models' && <ModelsTab models={models} reload={() => load('models')} setMsg={setMsg} />}
      {tab === 'quotas' && <QuotasTab quotas={quotas} reload={() => load('quotas')} setMsg={setMsg} />}
      {tab === 'logs' && (
        <div className="bg-card border border-border rounded-xl p-3 max-h-96 overflow-y-auto">
          {logs.length === 0 && <p className="text-xs text-slate2">暂无日志</p>}
          {logs.map(l => <p key={l.entry_id} className="text-xs font-mono">{l.timestamp?.slice(0,19)} [{l.action}] {l.actor} → {l.result}</p>)}
        </div>
      )}
    </div>
  )
}

/* ============ 用户管理 ============ */
function UsersTab({ users, me, setStatus }: any) {
  return (
    <table className="w-full bg-card border border-border rounded-xl text-sm shadow-soft">
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
  )
}

/* ============ 模型接入（OpenAI / Claude 双协议） ============ */
function ModelsTab({ models, reload, setMsg }: any) {
  const [showForm, setShowForm] = useState(false)
  const [form, setForm] = useState({ name: '', protocol: 'openai', base_url: '', api_key: '', model_name: '', is_default: false })

  const submit = async () => {
    try {
      await api.post('/admin/models', form)
      setMsg(`模型 ${form.name} 已接入`); setShowForm(false)
      setForm({ name: '', protocol: 'openai', base_url: '', api_key: '', model_name: '', is_default: false })
      reload()
    } catch (e: any) { setMsg(e?.response?.data?.detail || '接入失败') }
  }
  const del = async (m: ModelRow) => {
    if (!confirm(`确认移除模型 ${m.name}？`)) return
    await api.delete(`/admin/models/${m.id}`); reload()
  }
  const setDefault = async (m: ModelRow) => {
    await api.patch(`/admin/models/${m.id}`, { is_default: true }); reload()
  }
  const toggle = async (m: ModelRow) => {
    await api.patch(`/admin/models/${m.id}`, { enabled: !m.enabled }); reload()
  }

  return (
    <div className="space-y-3">
      <button onClick={() => setShowForm(!showForm)}
              className="flex items-center gap-1.5 bg-gradient-brand text-white rounded-lg px-3.5 py-2 text-sm shadow-glow hover:opacity-90">
        <Plus size={15} /> 接入新模型
      </button>
      {showForm && (
        <div className="bg-card border border-border rounded-xl shadow-soft p-4 grid grid-cols-2 gap-3 max-w-2xl">
          <input placeholder="展示名（如：GPT-4o）" value={form.name}
                 onChange={e => setForm({ ...form, name: e.target.value })}
                 className="border border-border rounded-lg px-3 py-2 text-sm"/>
          <select value={form.protocol} onChange={e => setForm({ ...form, protocol: e.target.value })}
                  className="border border-border rounded-lg px-3 py-2 text-sm bg-card">
            <option value="openai">OpenAI 兼容协议</option>
            <option value="claude">Claude 协议</option>
          </select>
          <input placeholder="Base URL（如 https://api.xxx.com/v1）" value={form.base_url}
                 onChange={e => setForm({ ...form, base_url: e.target.value })}
                 className="border border-border rounded-lg px-3 py-2 text-sm col-span-2"/>
          <input placeholder="API Key" type="password" value={form.api_key}
                 onChange={e => setForm({ ...form, api_key: e.target.value })}
                 className="border border-border rounded-lg px-3 py-2 text-sm"/>
          <input placeholder="模型 ID（如 gpt-4o / claude-sonnet-4-5）" value={form.model_name}
                 onChange={e => setForm({ ...form, model_name: e.target.value })}
                 className="border border-border rounded-lg px-3 py-2 text-sm"/>
          <label className="flex items-center gap-2 text-xs text-slate2">
            <input type="checkbox" checked={form.is_default}
                   onChange={e => setForm({ ...form, is_default: e.target.checked })}/>
            设为系统默认模型
          </label>
          <button onClick={submit}
                  className="bg-navy text-white rounded-lg py-2 text-sm hover:bg-navy-dark">确认接入</button>
        </div>
      )}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        {models.length === 0 && <p className="text-xs text-slate2">尚未接入任何模型，请点击"接入新模型"</p>}
        {models.map(m => (
          <div key={m.id} className="bg-card border border-border rounded-xl shadow-soft p-4 text-sm">
            <div className="flex items-center gap-2 mb-1.5">
              <Bot size={15} className="text-navy" />
              <span className="font-medium text-ink">{m.name}</span>
              <span className="text-[10px] bg-blue-50 text-navy rounded px-1.5 py-0.5">
                {m.protocol === 'claude' ? 'Claude 协议' : 'OpenAI 协议'}
              </span>
              {m.is_default && <span className="text-[10px] bg-emerald-50 text-ok rounded px-1.5 py-0.5">默认</span>}
              {!m.enabled && <span className="text-[10px] bg-red-50 text-alert rounded px-1.5 py-0.5">已停用</span>}
            </div>
            <p className="text-[11px] text-slate2 font-mono truncate">{m.model_name} · {m.base_url}</p>
            <p className="text-[11px] text-slate2 flex items-center gap-1 mt-0.5">
              <KeyRound size={10} /> {m.api_key_masked || '未配置密钥'}
            </p>
            <div className="flex gap-3 mt-2 text-xs">
              <button onClick={() => toggle(m)} className="text-navy underline">{m.enabled ? '停用' : '启用'}</button>
              {!m.is_default && <button onClick={() => setDefault(m)} className="text-accent underline">设为默认</button>}
              <button onClick={() => del(m)} className="text-alert underline">移除</button>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

/* ============ 额度管理 ============ */
function QuotasTab({ quotas, reload, setMsg }: any) {
  const [editing, setEditing] = useState<string | null>(null)
  const [draft, setDraft] = useState({ daily_limit: 100000 })

  const save = async (username: string, enabled?: boolean) => {
    try {
      const row = quotas.find((q: QuotaRow) => q.username === username)
      await api.post('/admin/quotas', {
        username, daily_limit: editing === username ? draft.daily_limit : row.daily_limit,
        enabled: enabled ?? row.enabled,
      })
      setMsg(`${username} 额度已更新`); setEditing(null); reload()
    } catch (e: any) { setMsg(e?.response?.data?.detail || '保存失败') }
  }

  return (
    <div>
      <p className="text-xs text-slate2 mb-2 flex items-center gap-1">
        <Coins size={12} /> 为每个用户分配每日 token 额度；停用后该用户将无法调用模型。
      </p>
      <table className="w-full bg-card border border-border rounded-xl text-sm shadow-soft">
        <thead>
          <tr className="bg-canvas">
            <th className="p-2 text-left">用户</th><th className="p-2 text-left">部门</th>
            <th className="p-2 text-left">日额度</th><th className="p-2 text-left">今日已用</th>
            <th className="p-2 text-left">状态</th><th className="p-2 text-left">操作</th>
          </tr>
        </thead>
        <tbody>
          {quotas.map((q: QuotaRow) => {
            const pct = q.daily_limit ? Math.min(100, Math.round(q.used_today / q.daily_limit * 100)) : 0
            return (
              <tr key={q.username} className="border-t border-border">
                <td className="p-2"><span className="font-medium">{q.username}</span>
                  <span className="text-slate2 text-xs ml-1.5">{q.display_name}</span></td>
                <td className="p-2">{q.department || '—'}</td>
                <td className="p-2">
                  {editing === q.username ? (
                    <input type="number" value={draft.daily_limit} autoFocus
                           onChange={e => setDraft({ daily_limit: Number(e.target.value) })}
                           className="w-28 border border-border rounded px-2 py-1 text-xs"/>
                  ) : q.daily_limit.toLocaleString()}
                </td>
                <td className="p-2">
                  <div className="flex items-center gap-2">
                    <div className="w-20 h-1.5 rounded-full bg-canvas overflow-hidden">
                      <div className={`h-full rounded-full ${pct > 90 ? 'bg-alert' : 'bg-navy'}`}
                           style={{ width: `${Math.max(pct, 2)}%` }} />
                    </div>
                    <span className="text-xs text-slate2">{q.used_today.toLocaleString()}</span>
                  </div>
                </td>
                <td className="p-2">
                  <span className={q.enabled ? 'text-ok' : 'text-alert'}>
                    {q.enabled ? '启用中' : '已停用'}
                  </span>
                </td>
                <td className="p-2 space-x-2 text-xs">
                  {editing === q.username ? (
                    <>
                      <button onClick={() => save(q.username)} className="text-accent underline">保存</button>
                      <button onClick={() => setEditing(null)} className="text-slate2 underline">取消</button>
                    </>
                  ) : (
                    <>
                      <button onClick={() => { setEditing(q.username); setDraft({ daily_limit: q.daily_limit }) }}
                              className="text-navy underline">调整额度</button>
                      <button onClick={() => save(q.username, !q.enabled)}
                              className={q.enabled ? 'text-alert underline' : 'text-accent underline'}>
                        {q.enabled ? '停用额度' : '恢复额度'}
                      </button>
                    </>
                  )}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
