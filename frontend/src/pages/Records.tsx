/** 实时资料库页（FR-D1~D5：七类型+权限+定稿防篡改）。 */
import { useCallback, useEffect, useState } from 'react'
import { FileLock, Plus, Stamp } from 'lucide-react'
import api from '../lib/api'

interface Doc { id: number; doc_type: string; title: string; status: string;
  version: number; owner_id: string; department: string; content_hash: string; watermark: string }

const DOC_TYPES = ['invoice','expense','tax','contract','asset','inventory','payslip']
const TYPE_LABELS: Record<string, string> = { invoice: '发票', expense: '报销单', tax: '报税',
  contract: '合同', asset: '资产', inventory: '库存台账', payslip: '工资单' }

export default function Records() {
  const [docs, setDocs] = useState<Doc[]>([])
  const [showCreate, setShowCreate] = useState(false)
  const [form, setForm] = useState({ doc_type: 'invoice', title: '', payload: '{}' })

  const load = useCallback(async () => {
    try { const r = await api.get('/records/all'); setDocs(r.items || []) } catch {}
  }, [])
  useEffect(() => { load() }, [load])

  const doCreate = async () => {
    try { await api.post('/records', form); setShowCreate(false); load() } catch {}
  }

  const doFinalize = async (id: number) => {
    try { await api.post(`/records/${id}/finalize`); load() } catch (e: any) {
      alert(e?.response?.data?.detail || '定稿失败')
    }
  }

  return (
    <div className="p-6">
      <h1 className="font-display text-lg font-bold text-ink mb-4">实时资料库</h1>
      <div className="flex gap-2 mb-4">
        <select value={form.doc_type} onChange={e => setForm({...form, doc_type: e.target.value})}
                className="border border-border rounded px-3 py-2 text-sm">
          {DOC_TYPES.map(t => <option key={t} value={t}>{TYPE_LABELS[t]}</option>)}
        </select>
        <button onClick={() => setShowCreate(true)}
                className="bg-accent text-white rounded px-4 py-2 text-sm flex items-center gap-1">
          <Plus size={14}/>新建{TYPE_LABELS[form.doc_type]}
        </button>
      </div>
      <div className="grid gap-3">
        {docs.map(d => (
          <div key={d.id} className="bg-card border border-border rounded-lg p-4">
            <div className="flex items-center justify-between">
              <div>
                <p className="font-medium text-sm">{d.title}</p>
                <p className="text-xs text-slate2 mt-0.5">
                  {TYPE_LABELS[d.doc_type]} · v{d.version} · {d.owner_id}
                  {d.status === 'final' && <span className="ml-2 text-ok">已定稿</span>}
                </p>
                {d.watermark && <p className="text-[10px] text-slate2 mt-1">{d.watermark}</p>}
              </div>
              {d.status === 'draft' && (
                <button onClick={() => doFinalize(d.id)}
                        className="flex items-center gap-1 bg-navy text-white rounded px-3 py-1 text-xs">
                  <Stamp size={12}/>定稿
                </button>
              )}
            </div>
          </div>
        ))}
      </div>
      {showCreate && (
        <div className="fixed inset-0 bg-black/30 flex items-center justify-center z-50" onClick={() => setShowCreate(false)}>
          <div className="bg-card rounded-lg p-6 w-full max-w-md space-y-3" onClick={e => e.stopPropagation()}>
            <h3 className="font-semibold">新建{TYPE_LABELS[form.doc_type]}</h3>
            <input value={form.title} onChange={e => setForm({...form, title: e.target.value})}
                   placeholder="标题" className="w-full border border-border rounded px-3 py-2 text-sm"/>
            <button onClick={doCreate} className="w-full bg-accent text-white rounded py-2 text-sm">创建草稿</button>
          </div>
        </div>
      )}
    </div>
  )
}
