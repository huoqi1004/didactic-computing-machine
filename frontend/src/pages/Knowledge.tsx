/**
 * 知识库页（M9 · 三级权限体系）：
 * - 公司知识库：全员可读，仅 sys_admin 可维护
 * - 部门知识库：本部门可读，仅本部门 dept_admin 可维护
 * - 个人知识库：仅本人可见可维护；可向部门管理员/系统管理员申请共享
 */
import { useCallback, useEffect, useState } from 'react'
import { Building2, Check, FolderKanban, Lock, Plus, Search,
         Share2, Trash2, User, X } from 'lucide-react'
import api from '../lib/api'
import { useAppStore } from '../store'

interface Entry { id: number; title: string; scope: string; tags: string; owner: string; department: string; content?: string }
interface ShareReq { id: number; entry_id: number; entry_title: string; requester: string; requester_department: string; target_scope: string; status: string; reviewer: string }

const TABS = [
  { key: 'company', label: '公司知识库', icon: Building2,
    desc: '全体成员可调用 · 仅系统管理员可维护' },
  { key: 'department', label: '部门知识库', icon: FolderKanban,
    desc: '本部门成员可调用 · 仅部门管理员可维护' },
  { key: 'personal', label: '个人知识库', icon: Lock,
    desc: '仅本人与本人的数字分身可见可用 · 可申请共享至部门/公司' },
] as const

export default function Knowledge() {
  const me = useAppStore(s => s.user)
  const [tab, setTab] = useState<string>('company')
  const [entries, setEntries] = useState<Entry[]>([])
  const [searchQ, setSearchQ] = useState('')
  const [showCreate, setShowCreate] = useState(false)
  const [form, setForm] = useState({ title: '', content: '', tags: '' })
  const [detail, setDetail] = useState<Entry | null>(null)
  const [msg, setMsg] = useState('')
  const [requests, setRequests] = useState<{ to_review: ShareReq[]; mine: ShareReq[] }>({ to_review: [], mine: [] })

  const canWrite = tab === 'company' ? me?.role === 'sys_admin'
    : tab === 'department' ? me?.role === 'dept_admin'
    : true // personal：本人

  const load = useCallback(async (t = tab) => {
    try { const r = await api.get('/knowledge', { params: { scope: t } }); setEntries(r.items || []) } catch {}
  }, [tab])
  const loadReqs = useCallback(async () => {
    try { const r = await api.get('/knowledge/share-requests'); setRequests(r) } catch {}
  }, [])
  useEffect(() => { load(); loadReqs() }, [load, loadReqs])

  const doSearch = async () => {
    if (!searchQ.trim()) return load()
    try {
      const r = await api.get('/knowledge/search', { params: { q: searchQ, scope: tab } })
      setEntries((r.items || []).map((i: any) => ({ ...i, content: '' })))
    } catch {}
  }

  const doCreate = async () => {
    setMsg('')
    try {
      await api.post('/knowledge', { ...form, scope: tab, is_public: false })
      setShowCreate(false); setForm({ title: '', content: '', tags: '' }); load()
    } catch (e: any) { setMsg(e?.response?.data?.detail || '创建失败') }
  }

  const doDelete = async (id: number) => {
    if (!confirm('确认删除该知识条目？')) return
    setMsg('')
    try { await api.delete(`/knowledge/${id}`); load() }
    catch (e: any) { setMsg(e?.response?.data?.detail || '删除失败') }
  }

  const doShare = async (id: number, target: string) => {
    setMsg('')
    try {
      const r = await api.post(`/knowledge/${id}/share-request`, { target })
      setMsg(r.message || '共享申请已提交'); loadReqs()
    } catch (e: any) { setMsg(e?.response?.data?.detail || '申请失败') }
  }

  const doReview = async (id: number, approve: boolean) => {
    setMsg('')
    try {
      const r = await api.post(`/knowledge/share-requests/${id}/review`, { approve })
      setMsg(r.message); loadReqs(); load()
    } catch (e: any) { setMsg(e?.response?.data?.detail || '审批失败') }
  }

  return (
    <div className="h-full overflow-y-auto p-6">
      <h1 className="font-display text-lg font-bold text-ink mb-4">知识库</h1>

      {/* 三级范围 Tab */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-3 mb-4">
        {TABS.map(t => (
          <button key={t.key} onClick={() => { setTab(t.key); setSearchQ('') }}
                  className={`text-left rounded-xl border px-4 py-3 transition-all ${
                    tab === t.key ? 'border-navy bg-blue-50/60 shadow-soft' : 'border-border bg-card hover:border-steel-light'}`}>
            <span className="flex items-center gap-2 text-sm font-medium text-ink">
              <t.icon size={15} className={tab === t.key ? 'text-navy' : 'text-slate2'} /> {t.label}
            </span>
            <span className="block text-[11px] text-slate2 mt-0.5">{t.desc}</span>
          </button>
        ))}
      </div>

      {/* 搜索 + 新建 */}
      <div className="flex gap-2 mb-3">
        <input value={searchQ} onChange={e => setSearchQ(e.target.value)}
               onKeyDown={e => e.key === 'Enter' && doSearch()}
               placeholder="搜索当前知识库…" className="flex-1 border border-border rounded-lg px-3 py-2 text-sm"/>
        <button onClick={doSearch} className="bg-navy text-white rounded-lg px-4 py-2 text-sm">搜索</button>
        {canWrite && (
          <button onClick={() => setShowCreate(true)}
                  className="bg-accent text-white rounded-lg px-4 py-2 text-sm flex items-center gap-1 hover:opacity-90">
            <Plus size={14}/>新建
          </button>
        )}
      </div>
      {!canWrite && (
        <p className="text-xs text-slate2 mb-3 flex items-center gap-1">
          <Lock size={11} /> 当前范围仅可浏览{tab === 'personal' ? '' : '；维护权限见上方说明'}
        </p>
      )}
      {msg && <p className="text-xs text-accent mb-2">{msg}</p>}

      {/* 共享审批区（dept_admin/sys_admin） */}
      {requests.to_review.length > 0 && (
        <div className="bg-amber-50 border border-amber-200 rounded-xl p-3 mb-4">
          <p className="text-sm font-medium text-amber-700 mb-2">待审批的共享申请（{requests.to_review.length}）</p>
          {requests.to_review.map(r => (
            <div key={r.id} className="flex items-center gap-2 text-xs py-1 border-t border-amber-100 first:border-0">
              <span className="text-ink font-medium">{r.entry_title}</span>
              <span className="text-slate2">{r.requester}（{r.requester_department}）</span>
              <span className="text-amber-700">→ 共享到{r.target_scope === 'company' ? '公司' : '部门'}</span>
              <span className="ml-auto flex gap-2">
                <button onClick={() => doReview(r.id, true)}
                        className="flex items-center gap-0.5 bg-ok text-white rounded px-2 py-0.5"><Check size={11}/>批准</button>
                <button onClick={() => doReview(r.id, false)}
                        className="flex items-center gap-0.5 bg-slate2 text-white rounded px-2 py-0.5"><X size={11}/>拒绝</button>
              </span>
            </div>
          ))}
        </div>
      )}
      {/* 我的申请状态 */}
      {tab === 'personal' && requests.mine.length > 0 && (
        <p className="text-[11px] text-slate2 mb-3">
          我的共享申请：{requests.mine.slice(0, 5).map(r =>
            `${r.entry_title.slice(0, 10)}(${r.status === 'pending' ? '审批中' : r.status === 'approved' ? '已共享' : '被拒绝'})`).join('、')}
        </p>
      )}

      {/* 条目列表 */}
      <div className="grid gap-3">
        {entries.length === 0 && (
          <p className="text-sm text-slate2 text-center py-12 border border-dashed border-border rounded-xl">
            {tab === 'personal' ? '个人知识库还是空的——这里的内容只有您本人（和您的数字分身）可以看到' : '暂无条目'}
          </p>
        )}
        {entries.map(e => (
          <div key={e.id} className="card-lift bg-card border border-border rounded-xl shadow-soft p-4">
            <div className="flex items-start justify-between gap-3">
              <button onClick={() => setDetail(e)} className="text-left min-w-0">
                <p className="font-medium text-sm text-ink truncate">{e.title}</p>
                <p className="text-[11px] text-slate2 mt-0.5">
                  {e.owner === 'system' ? '系统内置' : e.owner} · {e.department || '全公司'}
                  {e.tags && ` · ${e.tags}`}
                </p>
                {e.content && <p className="text-xs text-slate2 mt-1 line-clamp-2">{e.content}</p>}
              </button>
              <span className="flex items-center gap-2 shrink-0">
                {e.scope === 'personal' && e.owner === me?.username && (
                  <span className="flex gap-2 text-xs">
                    <button onClick={() => doShare(e.id, 'department')}
                            className="text-navy underline flex items-center gap-0.5" title="申请共享到部门">
                      <Share2 size={11}/>共享到部门
                    </button>
                    <button onClick={() => doShare(e.id, 'company')}
                            className="text-accent underline flex items-center gap-0.5" title="申请共享到公司">
                      <Share2 size={11}/>共享到公司
                    </button>
                  </span>
                )}
                {canWrite && e.owner === me?.username && (
                  <button onClick={() => doDelete(e.id)}
                          className="text-alert hover:underline" title="删除"><Trash2 size={13}/></button>
                )}
              </span>
            </div>
          </div>
        ))}
      </div>

      {/* 条目详情 */}
      {detail && (
        <div className="fixed inset-0 z-50 bg-ink/40 backdrop-blur-sm flex items-center justify-center p-4"
             onClick={() => setDetail(null)}>
          <div className="w-full max-w-xl bg-card rounded-2xl shadow-lift border border-border p-6 max-h-[80vh] overflow-y-auto"
               onClick={ev => ev.stopPropagation()}>
            <div className="flex items-center justify-between mb-3">
              <h3 className="font-display text-base font-bold text-ink">{detail.title}</h3>
              <button onClick={() => setDetail(null)} className="text-slate2 hover:text-alert"><X size={16}/></button>
            </div>
            <p className="text-[11px] text-slate2 mb-3">
              {detail.scope === 'company' ? '公司知识库' : detail.scope === 'department' ? '部门知识库' : '个人知识库'}
              · {detail.owner === 'system' ? '系统内置' : detail.owner}
            </p>
            <p className="text-sm text-ink whitespace-pre-wrap leading-relaxed">{detail.content}</p>
          </div>
        </div>
      )}

      {/* 新建弹窗 */}
      {showCreate && (
        <div className="fixed inset-0 z-50 bg-ink/40 backdrop-blur-sm flex items-center justify-center p-4"
             onClick={() => setShowCreate(false)}>
          <div className="bg-card rounded-2xl shadow-lift border border-border p-6 w-full max-w-md space-y-3"
               onClick={e => e.stopPropagation()}>
            <h3 className="font-semibold flex items-center gap-1.5">
              <User size={15} className="text-navy" />
              新建{TABS.find(t => t.key === tab)?.label}条目
            </h3>
            <input value={form.title} onChange={e => setForm({...form, title: e.target.value})}
                   placeholder="标题" className="w-full border border-border rounded-lg px-3 py-2 text-sm"/>
            <textarea value={form.content} onChange={e => setForm({...form, content: e.target.value})}
                      placeholder="内容" rows={5} className="w-full border border-border rounded-lg px-3 py-2 text-sm"/>
            <input value={form.tags} onChange={e => setForm({...form, tags: e.target.value})}
                   placeholder="标签（逗号分隔，选填）" className="w-full border border-border rounded-lg px-3 py-2 text-sm"/>
            <button onClick={doCreate}
                    className="w-full bg-gradient-brand text-white rounded-lg py-2 text-sm shadow-glow">保存</button>
          </div>
        </div>
      )}
    </div>
  )
}
