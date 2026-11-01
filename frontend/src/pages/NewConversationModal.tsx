/**
 * 新建对话弹窗：指定标题、工作区（会话与产物的存储目录）、
 * 智能体权限模式（完全权限/审批+自主/沙箱）与模型。
 */
import { useEffect, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { Bot, Check, FolderOpen, X } from 'lucide-react'
import api from '../lib/api'
import { useAppStore } from '../store'

const MODES = [
  { key: 'full_auto', label: '完全权限', desc: '所有任务由模型与智能体自主完成，模型可自主执行命令并在工作区内增删改查' },
  { key: 'approve_risky', label: '审批 + 自主', desc: '高风险指令由用户审批确认后执行；普通工作区文件写入由模型自主完成' },
  { key: 'sandbox', label: '沙箱模式', desc: '所有操作先在沙箱中执行，确认无风险后由人工审批，再写入本地工作区' },
]

export default function NewConversationModal() {
  const [search, setSearch] = useSearchParams()
  const nav = useNavigate()
  const bumpConv = useAppStore(s => s.bumpConv)
  const open = search.get('new') === '1'

  const [title, setTitle] = useState('')
  const [workspace, setWorkspace] = useState('')
  const [mode, setMode] = useState('approve_risky')
  const [modelId, setModelId] = useState(0)
  const [models, setModels] = useState<any[]>([])
  const [err, setErr] = useState('')
  const [creating, setCreating] = useState(false)

  useEffect(() => {
    if (!open) return
    api.get('/models/available').then(r => setModels(r.items || [])).catch(() => {})
  }, [open])

  if (!open) return null

  const close = () => {
    search.delete('new'); setSearch(search)
  }

  const create = async () => {
    setCreating(true); setErr('')
    try {
      const r = await api.post('/conversation', {
        title: title.trim() || '新对话',
        workspace: workspace.trim() || `工作区-${Date.now().toString().slice(-6)}`,
        agent_mode: mode,
        model_id: modelId,
      })
      bumpConv()
      close()
      nav(`/?c=${r.id}`, { replace: true })
    } catch (e: any) {
      setErr(e?.response?.data?.detail || '创建失败')
    } finally { setCreating(false) }
  }

  return (
    <div className="fixed inset-0 z-40 bg-ink/40 backdrop-blur-sm flex items-center justify-center p-4"
         onClick={close}>
      <div className="w-full max-w-lg bg-card rounded-2xl shadow-lift border border-border overflow-hidden"
           onClick={e => e.stopPropagation()}>
        <div className="h-1 bg-gradient-brand" />
        <div className="p-6 space-y-4">
          <div className="flex items-center justify-between">
            <h2 className="font-display text-lg font-bold text-ink flex items-center gap-2">
              <Bot size={19} className="text-navy" /> 新建智能体会话
            </h2>
            <button onClick={close} className="text-slate2 hover:text-alert"><X size={17} /></button>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <label className="block">
              <span className="text-xs text-slate2 mb-1 block">会话标题（选填）</span>
              <input value={title} onChange={e => setTitle(e.target.value)}
                     placeholder="新对话"
                     className="w-full border border-border rounded-lg px-3 py-2 text-sm focus:border-steel-light"/>
            </label>
            <label className="block">
              <span className="text-xs text-slate2 mb-1 block flex items-center gap-1">
                <FolderOpen size={11} /> 工作区（产物存储目录，选填）
              </span>
              <input value={workspace} onChange={e => setWorkspace(e.target.value)}
                     placeholder="如：季度报告"
                     className="w-full border border-border rounded-lg px-3 py-2 text-sm focus:border-steel-light"/>
            </label>
          </div>

          <div>
            <span className="text-xs text-slate2 mb-1.5 block">智能体模式</span>
            <div className="space-y-1.5">
              {MODES.map(m => (
                <button key={m.key} onClick={() => setMode(m.key)}
                        className={`w-full text-left rounded-xl border px-3.5 py-2.5 transition-all ${
                          mode === m.key ? 'border-navy bg-blue-50/60 shadow-soft' : 'border-border hover:border-steel-light'}`}>
                  <span className="flex items-center gap-2 text-sm font-medium text-ink">
                    {mode === m.key && <Check size={14} className="text-navy" />} {m.label}
                  </span>
                  <span className="block text-[11px] text-slate2 mt-0.5 leading-relaxed">{m.desc}</span>
                </button>
              ))}
            </div>
          </div>

          <label className="block">
            <span className="text-xs text-slate2 mb-1 block">模型（管理员接入的可用模型）</span>
            <select value={modelId} onChange={e => setModelId(Number(e.target.value))}
                    className="w-full border border-border rounded-lg px-3 py-2 text-sm bg-card">
              <option value={0}>系统默认模型</option>
              {models.map(m => <option key={m.id} value={m.id}>{m.name}（{m.protocol}）</option>)}
            </select>
          </label>

          {err && <p className="text-xs text-alert">{err}</p>}
          <button onClick={create} disabled={creating}
                  className="w-full bg-gradient-brand text-white rounded-lg py-2.5 text-sm font-medium shadow-glow hover:opacity-90 disabled:opacity-50">
            {creating ? '创建中…' : '创建并进入会话'}
          </button>
        </div>
      </div>
    </div>
  )
}
