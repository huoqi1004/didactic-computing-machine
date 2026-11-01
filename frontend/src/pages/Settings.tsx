/**
 * 个人设置（用户信息展示 + 修改密码）。
 */
import { useEffect, useState } from 'react'
import { Gauge, KeyRound } from 'lucide-react'
import api from '../lib/api'
import { useAppStore, ROLE_LABELS } from '../store'

export default function Settings() {
  const user = useAppStore(s => s.user)
  const [oldPwd, setOldPwd] = useState('')
  const [newPwd, setNewPwd] = useState('')
  const [confirmPwd, setConfirmPwd] = useState('')
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)
  const [saving, setSaving] = useState(false)

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setMsg(null)
    if (newPwd !== confirmPwd) { setMsg({ ok: false, text: '两次输入的新密码不一致' }); return }
    setSaving(true)
    try {
      await api.post('/auth/change_password', { old_password: oldPwd, new_password: newPwd })
      setMsg({ ok: true, text: '密码已更新' })
      setOldPwd(''); setNewPwd(''); setConfirmPwd('')
    } catch (err: any) {
      setMsg({ ok: false, text: err?.response?.data?.detail || '修改失败' })
    } finally { setSaving(false) }
  }

  return (
    <div className="p-6 max-w-2xl space-y-6">
      <h1 className="font-display text-xl font-bold text-ink">个人设置</h1>

      {/* 用户信息 */}
      <section className="bg-card rounded-2xl border border-border shadow-soft p-6">
        <h2 className="text-sm font-semibold text-ink mb-3">基本信息</h2>
        <div className="grid grid-cols-2 gap-4 text-sm">
          <div><span className="text-slate2">用户名：</span>{user?.username}</div>
          <div><span className="text-slate2">姓名：</span>{user?.display_name}</div>
          <div><span className="text-slate2">角色：</span>{ROLE_LABELS[user?.role || ''] || user?.role}</div>
          <div><span className="text-slate2">部门：</span>{user?.department || '—'}</div>
        </div>
      </section>

      {/* 今日 token 额度 */}
      <QuotaCard />

      {/* 修改密码 */}
      <section className="bg-card rounded-2xl border border-border shadow-soft p-6">
        <h2 className="text-sm font-semibold text-ink mb-3 flex items-center gap-1.5">
          <KeyRound size={15} /> 修改密码
        </h2>
        <form onSubmit={submit} className="space-y-3 max-w-sm">
          <input type="password" value={oldPwd} onChange={e => setOldPwd(e.target.value)}
                 placeholder="原密码" required
                 className="w-full border border-border rounded px-3 py-2 text-sm"/>
          <input type="password" value={newPwd} onChange={e => setNewPwd(e.target.value)}
                 placeholder="新密码（至少6位）" required minLength={6}
                 className="w-full border border-border rounded px-3 py-2 text-sm"/>
          <input type="password" value={confirmPwd} onChange={e => setConfirmPwd(e.target.value)}
                 placeholder="确认新密码" required minLength={6}
                 className="w-full border border-border rounded px-3 py-2 text-sm"/>
          {msg && <p className={`text-xs ${msg.ok ? 'text-ok' : 'text-alert'}`}>{msg.text}</p>}
          <button disabled={saving}
                  className="bg-navy text-white px-4 py-2 rounded text-sm hover:bg-navy-dark disabled:opacity-50">
            {saving ? '保存中…' : '保存'}
          </button>
        </form>
      </section>
    </div>
  )
}

/* ============ 今日 token 额度 ============ */
function QuotaCard() {
  const [quota, setQuota] = useState<any>(null)
  useEffect(() => {
    api.get('/me/quota').then(setQuota).catch(() => {})
  }, [])
  if (!quota) return null
  const pct = quota.limit ? Math.min(100, Math.round(quota.used / quota.limit * 100)) : 0
  return (
    <section className="bg-card rounded-2xl border border-border shadow-soft p-6">
      <h2 className="text-sm font-semibold text-ink mb-3 flex items-center gap-1.5">
        <Gauge size={15} className="text-navy" /> 今日模型额度
      </h2>
      <div className="flex items-center justify-between text-xs text-slate2 mb-1.5">
        <span>已用 {quota.used.toLocaleString()} tokens</span>
        <span>剩余 {quota.remaining.toLocaleString()} / 总额 {quota.limit.toLocaleString()}</span>
      </div>
      <div className="h-2 rounded-full bg-canvas overflow-hidden">
        <div className={`h-full rounded-full ${pct > 90 ? 'bg-alert' : 'bg-gradient-brand'}`}
             style={{ width: `${Math.max(pct, 2)}%` }} />
      </div>
      {!quota.enabled && <p className="text-xs text-alert mt-2">额度已被停用，请联系系统管理员</p>}
    </section>
  )
}
