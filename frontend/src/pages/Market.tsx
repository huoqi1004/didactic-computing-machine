/** 能力市场页（FR-MKT1~3：专家/专家团/Skill/MCP 四市场 + 上传审核）。 */
import { useCallback, useEffect, useState } from 'react'
import { Crown, Layers, Upload, Users } from 'lucide-react'
import api from '../lib/api'
import { useAppStore } from '../store'

interface Item {
  id: number; kind: string; name: string; description: string
  uploader: string; downloads: number; status: string
  domain_label?: string; department?: string; skills?: string[]; members?: string[]
}

const KINDS = ['expert', 'team', 'skill', 'mcp', 'plugin']
const KIND_LABELS: Record<string, string> = { expert: '专家', team: '专家团', skill: 'Skill', mcp: 'MCP', plugin: '插件' }

export default function Market() {
  const bumpConv = useAppStore(s => s.bumpConv)
  const [kind, setKind] = useState('expert')
  const [items, setItems] = useState<Item[]>([])
  const [domain, setDomain] = useState('')
  const [showUpload, setShowUpload] = useState(false)
  const [form, setForm] = useState({ name: '', description: '' })
  const [scanResult, setScanResult] = useState<any>(null)
  const [uploadErr, setUploadErr] = useState('')

  const load = useCallback(async (k = kind) => {
    try { const r = await api.get(`/market/${k}`); setItems(r.items || []) } catch {}
  }, [kind])
  useEffect(() => { load() }, [load])

  const doUpload = async () => {
    setUploadErr('')
    try {
      const r = await api.post(`/market/${kind}/upload`, form)
      setScanResult(r)   // 弹窗内展示安全扫描结果（M10）
      load()
    } catch (e: any) { setUploadErr(e?.response?.data?.detail || '上传失败') }
  }

  const domains = Array.from(new Set(items.map(i => i.domain_label).filter(Boolean)))
  const shown = domain ? items.filter(i => i.domain_label === domain) : items

  return (
    <div className="h-full overflow-y-auto p-6">
      <h1 className="font-display text-lg font-bold text-ink mb-1">能力市场</h1>
      <p className="text-xs text-slate2 mb-4">
        发现并复用全公司共享的专家、专家团、Skill 和 MCP 工具——对话中可随时引用专家人设
      </p>
      <div className="flex gap-1.5 mb-3 flex-wrap">
        {KINDS.map(k => (
          <button key={k} onClick={() => { setKind(k); setDomain('') }}
                  className={`px-3 py-1.5 rounded-lg text-sm ${kind === k ? 'bg-gradient-brand text-white shadow-glow' : 'bg-card border border-border text-slate2 hover:text-navy'}`}>
            {KIND_LABELS[k]}
          </button>
        ))}
        <button onClick={() => setShowUpload(true)}
                className="ml-auto flex items-center gap-1 bg-accent text-white rounded-lg px-3 py-1.5 text-sm hover:opacity-90">
          <Upload size={14}/>上传
        </button>
      </div>
      {/* 领域过滤 */}
      {domains.length > 1 && (
        <div className="flex gap-1.5 mb-4 flex-wrap">
          <button onClick={() => setDomain('')}
                  className={`px-2.5 py-1 rounded-full text-xs border ${!domain ? 'bg-navy text-white border-navy' : 'border-border text-slate2 hover:text-navy'}`}>
            全部（{items.length}）
          </button>
          {domains.map(d => (
            <button key={d} onClick={() => setDomain(d)}
                    className={`px-2.5 py-1 rounded-full text-xs border ${domain === d ? 'bg-navy text-white border-navy' : 'border-border text-slate2 hover:text-navy'}`}>
              {d}（{items.filter(i => i.domain_label === d).length}）
            </button>
          ))}
        </div>
      )}
      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
        {shown.map(i => (
          <div key={i.id} className="card-lift bg-card border border-border rounded-2xl shadow-soft p-4 flex flex-col">
            <div className="flex items-start gap-2.5 mb-1.5">
              <span className={`w-9 h-9 rounded-xl flex items-center justify-center shrink-0 ${i.kind === 'team' ? 'bg-gradient-brand' : 'bg-blue-50 text-navy'}`}>
                {i.kind === 'team' ? <Users size={17} className="text-white" /> : <Crown size={17} />}
              </span>
              <div className="min-w-0 flex-1">
                <p className="font-medium text-sm text-ink flex items-center gap-1.5 flex-wrap">
                  {i.name}
                  {i.domain_label && (
                    <span className="text-[10px] bg-cyan-50 text-cyan-600 border border-cyan-100 rounded px-1.5 py-0.5">
                      {i.domain_label}
                    </span>
                  )}
                  {i.uploader === 'system' && (
                    <span className="text-[10px] bg-emerald-50 text-ok border border-emerald-100 rounded px-1.5 py-0.5">官方</span>
                  )}
                </p>
                {i.department && <p className="text-[11px] text-slate2">{i.department}</p>}
              </div>
            </div>
            <p className="text-xs text-slate2 leading-relaxed line-clamp-4">{i.description}</p>
            {/* 技能标签 / 团队成员 */}
            {i.skills && i.skills.length > 0 && (
              <div className="flex flex-wrap gap-1 mt-2.5">
                {i.skills.map(s => (
                  <span key={s} className="text-[10px] bg-canvas text-slate2 rounded px-1.5 py-0.5">{s}</span>
                ))}
              </div>
            )}
            {i.members && i.members.length > 0 && (
              <p className="text-[11px] text-slate2 mt-2.5 flex items-start gap-1">
                <Layers size={11} className="mt-0.5 shrink-0 text-navy" />
                <span className="line-clamp-2">{i.members.join(' · ')}</span>
              </p>
            )}
            <p className="text-[10px] text-slate2 mt-auto pt-2 border-t border-border/60 mt-2">
              上传者: {i.uploader === 'system' ? '系统内置' : i.uploader} · 引用: {i.downloads} 次
            </p>
          </div>
        ))}
      </div>
      {shown.length === 0 && (
        <p className="text-sm text-slate2 text-center py-16">暂无{KIND_LABELS[kind]}条目</p>
      )}
      {showUpload && (
        <div className="fixed inset-0 bg-ink/40 backdrop-blur-sm flex items-center justify-center z-50 p-4" onClick={() => { setShowUpload(false); setScanResult(null) }}>
          <div className="bg-card rounded-2xl shadow-lift border border-border p-6 w-full max-w-md space-y-3" onClick={e => e.stopPropagation()}>
            <h3 className="font-semibold">上传{KIND_LABELS[kind]}（提交后待管理员审核）</h3>
            {!scanResult ? (
              <>
                <input value={form.name} onChange={e => setForm({...form, name: e.target.value})}
                       placeholder="名称" className="w-full border border-border rounded-lg px-3 py-2 text-sm"/>
                <textarea value={form.description} onChange={e => setForm({...form, description: e.target.value})}
                          placeholder="描述（能力说明、适用场景）" rows={3} className="w-full border border-border rounded-lg px-3 py-2 text-sm"/>
                {uploadErr && <p className="text-xs text-alert">{uploadErr}</p>}
                <button onClick={doUpload} className="w-full bg-gradient-brand text-white rounded-lg py-2 text-sm shadow-glow">提交审核</button>
              </>
            ) : (
              /* M10：安全扫描结果回执 */
              <div className="space-y-3">
                <div className={`rounded-xl border p-4 ${
                  scanResult.scan?.verdict === 'clean' ? 'border-emerald-200 bg-emerald-50'
                  : scanResult.scan?.verdict === 'suspicious' ? 'border-amber-200 bg-amber-50'
                  : 'border-red-200 bg-red-50'}`}>
                  <p className="text-sm font-semibold">
                    {scanResult.scan?.verdict === 'clean' ? '✅ 安全扫描通过'
                     : scanResult.scan?.verdict === 'suspicious' ? '⚠️ 扫描发现可疑内容'
                     : '⛔ 扫描发现恶意内容'}
                  </p>
                  {scanResult.scan?.risk_types?.length > 0 && (
                    <p className="text-xs mt-1.5">风险类型：<b>{scanResult.scan.risk_types.join('、')}</b></p>
                  )}
                  <p className="text-xs text-slate2 mt-1">{scanResult.message}</p>
                </div>
                {scanResult.risk_notice && (
                  <div className="text-xs text-slate2 bg-canvas rounded-lg p-3">
                    <p className="font-medium text-ink mb-1">命中详情（已同步告知管理员）：</p>
                    {scanResult.risk_notice.findings.map((f: string, i: number) => (
                      <p key={i} className="font-mono truncate">{f}</p>
                    ))}
                    <p className="mt-1.5">{scanResult.risk_notice.advice}</p>
                  </div>
                )}
                <button onClick={() => { setShowUpload(false); setScanResult(null); setForm({ name: '', description: '' }) }}
                        className="w-full bg-navy text-white rounded-lg py-2 text-sm">知道了</button>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
