/** 知识库页（FR-K1~K5：个人/部门/公司三层 + 搜索 + CRUD）。 */
import { useCallback, useEffect, useState } from 'react'
import { Plus, Search, Trash2 } from 'lucide-react'
import api from '../lib/api'

interface Entry { id: number; title: string; scope: string; tags: string; owner: string; content?: string }

const SCOPE_LABELS: Record<string, string> = { personal: '个人', department: '部门', company: '公司' }

export default function Knowledge() {
  const [scope, setScope] = useState('')
  const [entries, setEntries] = useState<Entry[]>([])
  const [searchQ, setSearchQ] = useState('')
  const [showCreate, setShowCreate] = useState(false)
  const [form, setForm] = useState({ title: '', content: '', scope: 'personal', tags: '', is_public: false })
  const [detail, setDetail] = useState<Entry | null>(null)

  const load = useCallback(async (s = scope) => {
    try { const r = await api.get('/knowledge', { params: { scope: s } }); setEntries(r.items || []) } catch {}
  }, [scope])

  useEffect(() => { load() }, [load])

  const doSearch = async () => {
    if (!searchQ.trim()) return load()
    try { const r = await api.get('/knowledge/search', { params: { q: searchQ } }); setEntries(r.items || []) } catch {}
  }

  const doCreate = async () => {
    try { await api.post('/knowledge', form); setShowCreate(false); setForm({ title: '', content: '', scope: 'personal', tags: '', is_public: false }); load() } catch {}
  }

  const doDelete = async (id: number) => {
    try { await api.delete(`/knowledge/${id}`); load() } catch {}
  }

  return (
    <div className="min-h-screen bg-canvas p-6">
      <h1 className="text-lg font-bold text-ink mb-4">知识库</h1>
      <div className="flex gap-2 mb-4">
        <input value={searchQ} onChange={e => setSearchQ(e.target.value)}
               onKeyDown={e => e.key === 'Enter' && doSearch()}
               placeholder="搜索知识…" className="flex-1 border border-border rounded px-3 py-2 text-sm"/>
        <button onClick={doSearch} className="bg-navy text-white rounded px-4 py-2 text-sm">搜索</button>
        <button onClick={() => setShowCreate(true)}
                className="bg-accent text-white rounded px-4 py-2 text-sm flex items-center gap-1"><Plus size={14}/>新建</button>
      </div>
      <div className="grid gap-3">
        {entries.map(e => (
          <div key={e.id} className="bg-card border border-border rounded-lg p-4 flex items-center justify-between">
            <div>
              <p className="font-medium text-sm">{e.title}</p>
              <p className="text-xs text-slate2 mt-0.5">{SCOPE_LABELS[e.scope] || e.scope} · {e.owner}</p>
            </div>
            {e.owner === localStorage.getItem('erp-username') && (
              <button onClick={() => doDelete(e.id)} className="text-alert text-xs hover:underline">删除</button>
            )}
          </div>
        ))}
      </div>
      {showCreate && (
        <div className="fixed inset-0 bg-black/30 flex items-center justify-center z-50" onClick={() => setShowCreate(false)}>
          <div className="bg-card rounded-lg p-6 w-full max-w-md space-y-3" onClick={e => e.stopPropagation()}>
            <h3 className="font-semibold">新建知识条目</h3>
            <input value={form.title} onChange={e => setForm({...form, title: e.target.value})}
                   placeholder="标题" className="w-full border border-border rounded px-3 py-2 text-sm"/>
            <textarea value={form.content} onChange={e => setForm({...form, content: e.target.value})}
                      placeholder="内容" rows={4} className="w-full border border-border rounded px-3 py-2 text-sm"/>
            <select value={form.scope} onChange={e => setForm({...form, scope: e.target.value})}
                    className="w-full border border-border rounded px-3 py-2 text-sm">
              <option value="personal">个人</option>
              <option value="department">部门</option>
              <option value="company">公司</option>
            </select>
            <button onClick={doCreate} className="w-full bg-accent text-white rounded py-2 text-sm">保存</button>
          </div>
        </div>
      )}
    </div>
  )
}
