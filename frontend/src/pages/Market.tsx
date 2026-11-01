/** 能力市场页（FR-MKT1~3：专家/专家团/Skill/MCP 四市场 + 上传审核）。 */
import { useCallback, useEffect, useState } from 'react'
import { Upload } from 'lucide-react'
import api from '../lib/api'

interface Item { id: number; kind: string; name: string; description: string; uploader: string; downloads: number; status: string }

const KINDS = [ 'expert', 'team', 'skill', 'mcp' ]
const KIND_LABELS: Record<string, string> = { expert: '专家', team: '专家团', skill: 'Skill', mcp: 'MCP' }

export default function Market() {
  const [kind, setKind] = useState('expert')
  const [items, setItems] = useState<Item[]>([])
  const [showUpload, setShowUpload] = useState(false)
  const [form, setForm] = useState({ name: '', description: '' })

  const load = useCallback(async (k = kind) => {
    try { const r = await api.get(`/market/${k}`); setItems(r.items || []) } catch {}
  }, [kind])
  useEffect(() => { load() }, [load])

  const doUpload = async () => {
    try { await api.post(`/market/${kind}/upload`, form); setShowUpload(false); load() } catch {}
  }

  return (
    <div className="p-6">
      <h1 className="text-lg font-bold text-ink mb-1">能力市场</h1>
      <p className="text-xs text-slate2 mb-4">发现并复用全公司共享的专家、专家团、Skill 和 MCP 工具</p>
      <div className="flex gap-1 mb-4">
        {KINDS.map(k => (
          <button key={k} onClick={() => setKind(k)}
                  className={`px-3 py-1.5 rounded text-sm ${kind === k ? 'bg-navy text-white' : 'bg-card border border-border'}`}>
            {KIND_LABELS[k]}
          </button>
        ))}
        <button onClick={() => setShowUpload(true)}
                className="ml-auto flex items-center gap-1 bg-accent text-white rounded px-3 py-1.5 text-sm">
          <Upload size={14}/>上传
        </button>
      </div>
      <div className="grid gap-3">
        {items.map(i => (
          <div key={i.id} className="bg-card border border-border rounded-lg p-4">
            <p className="font-medium text-sm">{i.name}</p>
            <p className="text-xs text-slate2 mt-0.5">{i.description}</p>
            <p className="text-[10px] text-slate2 mt-1">上传者: {i.uploader} · 使用: {i.downloads} 次</p>
          </div>
        ))}
      </div>
      {showUpload && (
        <div className="fixed inset-0 bg-black/30 flex items-center justify-center z-50" onClick={() => setShowUpload(false)}>
          <div className="bg-card rounded-lg p-6 w-full max-w-md space-y-3" onClick={e => e.stopPropagation()}>
            <h3 className="font-semibold">上传{KIND_LABELS[kind]}</h3>
            <input value={form.name} onChange={e => setForm({...form, name: e.target.value})}
                   placeholder="名称" className="w-full border border-border rounded px-3 py-2 text-sm"/>
            <textarea value={form.description} onChange={e => setForm({...form, description: e.target.value})}
                      placeholder="描述" rows={3} className="w-full border border-border rounded px-3 py-2 text-sm"/>
            <button onClick={doUpload} className="w-full bg-accent text-white rounded py-2 text-sm">提交审核</button>
          </div>
        </div>
      )}
    </div>
  )
}
