/**
 * 智能体会话页（WorkBuddy 式对话 · "/" 路由）。
 * 上方：消息流（每条助手消息上方渲染思考/执行步骤时间线 + 沙箱待审批卡）；
 * 下方：输入区（附件上传 / 能力引用：知识库·资料库·专家·Skill·MCP·分身 /
 *        三种智能体模式 / 模型选择 / 生成文件开关）。
 * 右侧：工作区文件栏（会话产物，可下载）。
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import {
  AlertTriangle, Ban, Bot, Check, ChevronDown, Cpu, FileText, Files, Loader2,
  MessageSquarePlus, Paperclip, Puzzle, Send, Shield, ShieldAlert, Sparkles,
  Target, User, Wrench, X,
} from 'lucide-react'
import Dropdown from '../components/Dropdown'
import api from '../lib/api'
import { useAppStore } from '../store'

interface Step { kind: string; detail: string; status?: string; path?: string; staged?: string }
interface Msg { id: number; role: string; content: string; thinking: Step[]; attachments: any[] }
interface Conv { id: number; title: string; workspace: string; agent_mode: string; model_id: number }
interface WsFile { path: string; size: number }

const MODES = [
  { key: 'full_auto', label: '完全权限', desc: '智能体自主执行全部任务，工作区可增删改查' },
  { key: 'approve_risky', label: '审批 + 自主', desc: '普通文件写入自动完成，高风险动作需人工审批' },
  { key: 'sandbox', label: '沙箱模式', desc: '所有操作先在沙箱执行，确认无风险后审批落盘' },
]
const MODE_LABEL: Record<string, string> = Object.fromEntries(MODES.map(m => [m.key, m.label]))

const STEP_ICON: Record<string, any> = {
  intent: Target, capability: Puzzle, model: Cpu, attachments: Paperclip,
  file_write: FileText, quota: AlertTriangle, guard: ShieldAlert, blocked: Ban,
  artifact_decision: FileText, memory: Sparkles, title: Sparkles, web: Sparkles,
}
const STATUS_COLOR: Record<string, string> = {
  done: 'text-ok', running: 'text-navy', error: 'text-alert',
  pending: 'text-warn', approved: 'text-ok', rejected: 'text-slate2', auto: 'text-ok',
}

export default function Chat() {
  const [search] = useSearchParams()
  const nav = useNavigate()
  const bumpConv = useAppStore(s => s.bumpConv)
  const convId = Number(search.get('c') || 0)
  const showNew = search.get('new') === '1'

  const [conv, setConv] = useState<Conv | null>(null)
  const [msgs, setMsgs] = useState<Msg[]>([])
  const [files, setFiles] = useState<WsFile[]>([])
  const [pendingOps, setPendingOps] = useState<any[]>([])
  const [models, setModels] = useState<any[]>([])
  const [filesOpen, setFilesOpen] = useState(false)
  const [loading, setLoading] = useState(false)

  const load = useCallback(async () => {
    if (!convId) { setConv(null); setMsgs([]); setFiles([]); return }
    try {
      const r = await api.get(`/conversation/${convId}/messages`)
      setConv({ id: convId, title: r.items[0]?.content?.slice(0, 20) || '会话',
                workspace: r.workspace, agent_mode: r.agent_mode, model_id: r.model_id })
      setMsgs(r.items || [])
      setFiles(r.files || [])
      setPendingOps(r.pending_ops || [])
    } catch { nav('/', { replace: true }) }
  }, [convId, nav])
  useEffect(() => { load() }, [load])
  useEffect(() => {
    api.get('/models/available').then(r => setModels(r.items || [])).catch(() => {})
  }, [])

  const patchConv = useCallback(async (body: object) => {
    try {
      const r = await api.patch(`/conversation/${convId}`, body)
      setConv((c: any) => (c ? { ...c, ...r } : c))
    } catch {}
  }, [convId])

  if (!convId) {
    // 草稿态：模式/工作区/模型在输入框周围直接选择，首条消息发送时自动创建会话
    return (
      <div className="h-full flex">
        <div className="flex-1 flex flex-col min-w-0">
          <div className="flex-1 overflow-y-auto px-6 py-5">
            <EmptyState />
          </div>
          <Composer convId={0} conv={null} models={models}
                    onCreated={(id: number) => { nav(`/?c=${id}`, { replace: true }); bumpConv() }}
                    onSent={() => { load(); bumpConv() }}
                    setLoading={setLoading} />
        </div>
      </div>
    )
  }
  return (
    <div className="h-full flex">
      <div className="flex-1 flex flex-col min-w-0">
        {/* ===== 顶部会话信息栏 ===== */}
        {conv && <ChatHeader conv={conv} setConv={setConv} models={models}
                             filesOpen={filesOpen} setFilesOpen={setFilesOpen}
                             fileCount={files.length} />}
        {/* ===== 上方：消息流（思考过程 + 回复） ===== */}
        <div className="flex-1 overflow-y-auto px-6 py-5 space-y-5">
          {msgs.map(m => <MessageBlock key={m.id} msg={m} convId={convId}
                                       onDecided={load} />)}
          {loading && (
            <div className="flex items-center gap-2 text-sm text-slate2">
              <Loader2 size={15} className="animate-spin text-navy" /> 智能体思考中…
            </div>
          )}
        </div>
        {/* ===== 下方：输入区 ===== */}
        <Composer convId={convId} conv={conv} models={models}
                  onPatch={patchConv}
                  onSent={() => { load(); bumpConv() }}
                  setLoading={setLoading} />
      </div>
      {/* ===== 右侧：工作区文件栏 ===== */}
      {filesOpen && conv && (
        <div className="w-64 shrink-0 bg-card border-l border-border overflow-y-auto p-3">
          <p className="text-xs font-semibold text-ink mb-2 flex items-center gap-1.5">
            <Files size={13} className="text-navy" /> 工作区 · {conv.workspace}
          </p>
          {files.length === 0 && <p className="text-xs text-slate2">暂无产物文件</p>}
          {files.map(f => (
            <a key={f.path} href={`/api/v1/conversation/${convId}/files/${f.path}`}
               className="block text-xs text-navy hover:underline truncate py-1 border-b border-border/60"
               title={f.path}>
              <FileText size={11} className="inline mr-1" />{f.path}
              <span className="text-slate2 ml-1">({Math.ceil(f.size / 1024)}KB)</span>
            </a>
          ))}
        </div>
      )}
    </div>
  )
}

/* ============ 空态（未选会话） ============ */
function EmptyState() {
  return (
    <div className="h-full flex flex-col items-center justify-center gap-4 tech-grid">
      <span className="w-16 h-16 rounded-2xl bg-gradient-brand shadow-glow flex items-center justify-center">
        <Bot size={32} className="text-white" />
      </span>
      <h1 className="font-display text-xl font-bold text-ink">开启一个新的智能体会话</h1>
      <p className="text-sm text-slate2 max-w-md text-center leading-relaxed">
        在下方输入框旁选择<b>智能体模式</b>、<b>工作区</b>与<b>模型</b>，
        直接发送第一条消息即可开始；支持挂载附件、引用知识库/资料库/专家/Skill/MCP 等能力，
        会话标题将由 AI 自动生成。
      </p>
    </div>
  )
}

/* ============ 顶部信息栏：模式/模型切换 ============ */
function ChatHeader({ conv, setConv, models, filesOpen, setFilesOpen, fileCount }: any) {
  const patch = async (body: object) => {
    const r = await api.patch(`/conversation/${conv.id}`, body)
    setConv((c: any) => ({ ...c, ...r }))
  }
  return (
    <div className="h-13 shrink-0 bg-card border-b border-border px-5 py-2.5 flex items-center gap-3">
      <div className="min-w-0">
        <p className="text-sm font-semibold text-ink truncate">会话 #{conv.id}</p>
        <p className="text-[11px] text-slate2 truncate">📁 工作区：{conv.workspace}</p>
      </div>
      <div className="ml-auto flex items-center gap-2">
        <Dropdown value={conv.agent_mode} width={138} up
                  onChange={(v: any) => patch({ agent_mode: v })}
                  options={MODES.map(m => ({ value: m.key, label: `${m.label}模式`, hint: m.desc }))}/>
        <Dropdown value={conv.model_id} width={150} up
                  onChange={(v: any) => patch({ model_id: Number(v) })}
                  options={[{ value: 0, label: '系统默认模型' },
                            ...models.map((m: any) => ({ value: m.id, label: m.name, hint: m.protocol }))]} />
        <button onClick={() => setFilesOpen(!filesOpen)}
                className={`text-xs rounded-lg px-2.5 py-1.5 border transition-colors ${filesOpen ? 'bg-navy text-white border-navy' : 'border-border text-slate2 hover:text-navy'}`}>
          工作区文件 ({fileCount})
        </button>
      </div>
    </div>
  )
}

/* ============ 消息块：思考时间线 + 内容 ============ */
function MessageBlock({ msg, convId, onDecided }: any) {
  const [open, setOpen] = useState(true)
  const isUser = msg.role === 'user'
  if (isUser) {
    return (
      <div className="flex justify-end gap-2.5">
        <div className="max-w-2xl bg-gradient-brand text-white rounded-2xl rounded-tr-sm px-4 py-2.5 text-sm shadow-soft">
          {msg.content}
          {msg.attachments?.length > 0 && (
            <p className="text-[11px] text-white/75 mt-1 flex items-center gap-1">
              <Paperclip size={11} /> {msg.attachments.map((a: any) => a.filename).join('、')}
            </p>
          )}
        </div>
        <span className="w-7 h-7 rounded-full bg-steel flex items-center justify-center shrink-0 mt-0.5">
          <User size={14} className="text-white" />
        </span>
      </div>
    )
  }
  return (
    <div className="flex gap-2.5">
      <span className="w-7 h-7 rounded-full bg-gradient-brand flex items-center justify-center shrink-0 mt-0.5">
        <Bot size={14} className="text-white" />
      </span>
      <div className="max-w-3xl min-w-0">
        {/* 思考/执行过程 */}
        {msg.thinking?.length > 0 && (
          <div className="mb-2 bg-card border border-border rounded-xl overflow-hidden">
            <button onClick={() => setOpen(!open)}
                    className="w-full flex items-center gap-1.5 px-3 py-2 text-xs font-medium text-ink hover:bg-canvas">
              <Wrench size={12} className="text-navy" /> 思考与执行过程
              <span className="text-slate2 font-normal">({msg.thinking.length} 步)</span>
              <ChevronDown size={13} className={`ml-auto text-slate2 transition-transform ${open ? '' : '-rotate-90'}`} />
            </button>
            {open && (
              <div className="px-3 pb-2.5 space-y-1.5 border-t border-border pt-2">
                {msg.thinking.map((s: Step, i: number) => {
                  const Icon = STEP_ICON[s.kind] || Sparkles
                  return (
                    <div key={i} className="flex items-start gap-2 text-xs">
                      <Icon size={13} className={`mt-0.5 shrink-0 ${STATUS_COLOR[s.status || 'done']}`} />
                      <span className="text-slate2 leading-relaxed">{s.detail}</span>
                      {s.status === 'pending' && (
                        <span className="ml-auto shrink-0 flex gap-1.5">
                          <ApproveBtn convId={convId} msgId={msg.id} idx={i}
                                      approve onDecided={onDecided} />
                          <ApproveBtn convId={convId} msgId={msg.id} idx={i}
                                      onDecided={onDecided} />
                        </span>
                      )}
                    </div>
                  )
                })}
              </div>
            )}
          </div>
        )}
        {msg.content && (
          <div className="bg-card border border-border rounded-2xl rounded-tl-sm px-4 py-3 text-sm whitespace-pre-wrap leading-relaxed shadow-soft">
            {msg.content}
          </div>
        )}
      </div>
    </div>
  )
}

function ApproveBtn({ convId, msgId, idx, approve, onDecided }: any) {
  const act = async () => {
    await api.post(`/conversation/${convId}/messages/${msgId}/ops/${idx}/decision`,
                   { approve })
    onDecided()
  }
  return (
    <button onClick={act}
            className={`px-2 py-0.5 rounded text-[11px] text-white ${approve ? 'bg-ok hover:opacity-90' : 'bg-slate2 hover:opacity-90'}`}>
      {approve ? '批准落盘' : '拒绝'}
    </button>
  )
}

/* ============ 输入区：附件/能力/模式内提示/发送 ============ */
function Composer({ convId, conv, models, onPatch, onSent, onCreated, setLoading }: any) {
  const patchMode = (v: any) => onPatch?.({ agent_mode: v })
  const patchModel = (v: any) => onPatch?.({ model_id: Number(v) })
  const [text, setText] = useState('')
  const [attachments, setAttachments] = useState<any[]>([])
  const [caps, setCaps] = useState<{ knowledge: number[]; records: number[]; market: number[]; avatar: string }>({
    knowledge: [], records: [], market: [], skill: [], mcp: [], plugin: [], avatar: '',
  })
  const [capsOpen, setCapsOpen] = useState(false)
  const [capTab, setCapTab] = useState<'knowledge' | 'records' | 'market' | 'skill' | 'mcp' | 'plugin' | 'avatar'>('knowledge')
  const [capItems, setCapItems] = useState<any[]>([])
  const [genFile, setGenFile] = useState(false)
  const [sending, setSending] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)
  const [pendingFiles, setPendingFiles] = useState<File[]>([])
  // 草稿态配置（会话框周围直接选择，首条消息时创建会话）
  const [draftWs, setDraftWs] = useState('')
  const [draftMode, setDraftMode] = useState('approve_risky')
  const [draftModelId, setDraftModelId] = useState(0)
  const isDraft = !convId

  const toggleCap = (tab: string, id: number) => {
    setCaps((c: any) => {
      const list: number[] = c[tab] || []
      return { ...c, [tab]: list.includes(id) ? list.filter(x => x !== id) : [...list, id] }
    })
  }
  const openCaps = (tab: any) => {
    setCapsOpen(true); setCapTab(tab)
    const url = tab === 'knowledge' ? '/knowledge'
      : tab === 'records' ? '/records'
      : tab === 'skill' ? '/market/skill'
      : tab === 'mcp' ? '/market/mcp'
      : tab === 'plugin' ? '/market/plugin'
      : '/market/expert'
    api.get(url).then(r => setCapItems(r.items || [])).catch(() => setCapItems([]))
  }

  const uploadFiles = async (list: FileList | null) => {
    if (!list) return
    if (isDraft) {
      // 草稿态先本地暂存，会话创建后再上传
      setPendingFiles(pf => [...pf, ...Array.from(list)])
      return
    }
    for (const f of Array.from(list)) {
      const fd = new FormData()
      fd.append('file', f)
      try {
        const r = await api.post(`/conversation/${convId}/upload`, fd,
          { headers: { 'Content-Type': 'multipart/form-data' } })
        setAttachments((a) => [...a, r])
      } catch { /* 单文件失败跳过 */ }
    }
  }

  const send = async () => {
    if (!text.trim() || sending) return
    setSending(true); setLoading(true)
    try {
      let targetId = convId
      let sentAttachments = attachments
      if (isDraft) {
        // 首条消息：以草稿配置懒创建会话，再补传暂存附件
        const conv = await api.post('/conversation', {
          title: '新对话',
          workspace: draftWs.trim() || `工作区-${Date.now().toString().slice(-6)}`,
          agent_mode: draftMode, model_id: draftModelId,
        })
        targetId = conv.id
        for (const f of pendingFiles) {
          const fd = new FormData()
          fd.append('file', f)
          try {
            const r = await api.post(`/conversation/${targetId}/upload`, fd,
              { headers: { 'Content-Type': 'multipart/form-data' } })
            sentAttachments = [...sentAttachments, r]
          } catch { /* 单文件失败跳过 */ }
        }
      }
      await api.post(`/conversation/${targetId}/chat`, {
        message: text, attachments: sentAttachments, capabilities: caps, generate_file: genFile,
      })
      setText(''); setAttachments([]); setPendingFiles([]); setGenFile(false)
      if (isDraft) onCreated(targetId)
      else onSent()
    } catch (e: any) {
      alert(e?.response?.data?.detail || '发送失败')
    } finally { setSending(false); setLoading(false) }
  }

  const capChip = (label: string, onRemove: () => void) => (
    <span key={label} className="inline-flex items-center gap-1 bg-blue-50 text-blue-600 border border-blue-100 rounded-full px-2 py-0.5 text-[11px]">
      {label}
      <button onClick={onRemove}><X size={10} /></button>
    </span>
  )

  return (
    <div className="shrink-0 px-4 pb-4 pt-1">
      {/* ===== Trae 式统一输入卡片 ===== */}
      <div className="relative bg-card border border-border rounded-2xl shadow-soft transition-shadow focus-within:shadow-lift">
        {/* 能力引用上浮菜单 */}
        {capsOpen && (
          <div className="absolute bottom-full left-0 right-0 mb-2 rounded-xl bg-card shadow-lift border border-border overflow-hidden z-30">
            <div className="flex items-center gap-1 p-2 border-b border-border/70 flex-wrap">
              {([['knowledge', '📖 知识库'], ['records', '🗂 资料库'], ['market', '👑 专家/专家团'],
                 ['skill', '⚡ Skill'], ['mcp', '🔌 MCP'], ['plugin', '🌐 插件'],
                 ['avatar', '🤖 数字分身']] as const).map(([k, label]) => (
                <button key={k} onClick={() => k !== 'avatar' && openCaps(k)}
                        className={`px-2.5 py-1 rounded-full text-[11px] transition-colors ${
                          capTab === k ? 'bg-gradient-pill text-white shadow-glow font-medium'
                                       : 'bg-canvas text-slate2 hover:text-navy'}`}>
                  {label}
                </button>
              ))}
              <button onClick={() => setCapsOpen(false)}
                      className="ml-auto w-6 h-6 rounded-full text-slate2 hover:bg-canvas hover:text-alert flex items-center justify-center">
                <X size={13} />
              </button>
            </div>
            <div className="max-h-56 overflow-y-auto p-1.5">
              {capTab === 'avatar' ? (
                <div className="flex gap-2 p-1">
                  <input value={caps.avatar} onChange={e => setCaps((c: any) => ({ ...c, avatar: e.target.value }))}
                         placeholder="输入数字分身名称，如：财务分身" autoFocus
                         className="flex-1 text-xs border border-border rounded-lg px-2.5 py-1.5 focus:border-steel-light"/>
                  <button onClick={() => setCapsOpen(false)}
                          className="text-xs bg-gradient-brand text-white rounded-lg px-3 shadow-glow">挂载</button>
                </div>
              ) : (
                <>
                  {capItems.length === 0 && (
                    <p className="text-xs text-slate2 p-3 text-center">暂无可选条目</p>
                  )}
                  {capItems.map((it: any) => {
                    const sel = (caps as any)[capTab]?.includes(it.id)
                    return (
                      <button key={it.id} onClick={() => toggleCap(capTab, it.id)}
                              className={`w-full flex items-center gap-2 text-left px-2.5 py-2 rounded-lg text-xs transition-colors ${
                                sel ? 'bg-blue-50/70 text-navy' : 'text-ink hover:bg-canvas'}`}>
                        <span className="flex-1 min-w-0">
                          <span className="block truncate font-medium">{it.title || it.name}</span>
                          {(it.department || it.scope || it.domain_label) && (
                            <span className="block text-[10px] text-slate2 truncate">
                              {it.domain_label || ''}{it.department || ''}{it.scope || it.status || ''}
                            </span>
                          )}
                        </span>
                        {sel && <Check size={13} className="shrink-0 text-navy" />}
                      </button>
                    )
                  })}
                </>
              )}
            </div>
          </div>
        )}

        {/* 工具栏：附件 / 能力 | 工作区(草稿) / 模式 / 模型 / 生成文件 */}
        <div className="flex items-center gap-1.5 px-3 pt-2.5 flex-wrap">
          <input ref={fileRef} type="file" multiple hidden
                 onChange={e => { uploadFiles(e.target.files); e.target.value = '' }} />
          <button title="添加附件（任意格式）" onClick={() => fileRef.current?.click()}
                  className="p-1.5 rounded-lg text-slate2 hover:text-navy hover:bg-canvas transition-colors">
            <Paperclip size={16} />
          </button>
          <button title="引用能力：知识库/资料库/专家/Skill/MCP/插件/数字分身"
                  onClick={() => capsOpen ? setCapsOpen(false) : openCaps('knowledge')}
                  className={`flex items-center gap-1 px-2 py-1.5 rounded-lg text-xs transition-colors ${
                    capsOpen ? 'bg-navy text-white' : 'text-slate2 hover:text-navy hover:bg-canvas'}`}>
            <Puzzle size={15} /> <span className="hidden sm:inline">能力</span>
          </button>
          {isDraft && (
            <span className="flex items-center gap-1 rounded-lg border border-dashed border-steel-light/60 bg-blue-50/40 px-2 py-1">
              <Files size={12} className="text-navy shrink-0" />
              <input value={draftWs} onChange={e => setDraftWs(e.target.value)}
                     placeholder="工作区名称（选填）" size={10}
                     className="bg-transparent text-xs text-ink placeholder:text-slate2/70 focus:outline-none w-28"/>
            </span>
          )}
          <span className="ml-auto flex items-center gap-1.5">
            <Dropdown value={conv?.agent_mode ?? draftMode} width={132}
                      onChange={(v: any) => conv ? patchMode(v) : setDraftMode(v)}
                      options={MODES.map(m => ({ value: m.key, label: `${m.label}模式`, hint: m.desc }))}/>
            <Dropdown value={conv?.model_id ?? draftModelId} width={148}
                      onChange={(v: any) => conv ? patchModel(v) : setDraftModelId(Number(v))}
                      options={[{ value: 0, label: '系统默认模型' },
                                ...models.map(m => ({ value: m.id, label: m.name, hint: m.protocol }))]} />
            <button title="AI 自动生成文件已启用；点击可强制本条回复保存为文件"
                    onClick={() => setGenFile(v => !v)}
                    className={`flex items-center gap-1 px-2 py-1.5 rounded-lg text-xs transition-colors ${
                      genFile ? 'bg-accent text-white' : 'text-slate2 hover:text-accent hover:bg-canvas'}`}>
              <Files size={14} /><span className="hidden md:inline">生成文件</span>
            </button>
          </span>
        </div>

        {/* 已选能力与附件 chips */}
        {((attachments.length + pendingFiles.length + caps.knowledge.length + caps.records.length + caps.market.length + (caps.skill || []).length + (caps.mcp || []).length + (caps.plugin || []).length) > 0 || !!caps.avatar) && (
          <div className="flex flex-wrap gap-1.5 px-3 pt-2">
            {attachments.map((a, i) => capChip(`📎 ${a.filename}`, () =>
              setAttachments(list => list.filter((_, j) => j !== i))))}
            {pendingFiles.map((f, i) => capChip(`📎 ${f.name}（待上传）`, () =>
              setPendingFiles(pf => pf.filter((_, j) => j !== i))))}
            {caps.knowledge.map(id => capChip(`📖 知识#${id}`, () => toggleCap('knowledge', id)))}
            {caps.records.map(id => capChip(`🗂 资料#${id}`, () => toggleCap('records', id)))}
            {caps.market.map(id => capChip(`👑 能力#${id}`, () => toggleCap('market', id)))}
            {(caps.skill || []).map(id => capChip(`⚡ Skill#${id}`, () => toggleCap('skill', id)))}
            {(caps.mcp || []).map(id => capChip(`🔌 MCP#${id}`, () => toggleCap('mcp', id)))}
            {(caps.plugin || []).map(id => capChip(`🌐 插件#${id}`, () => toggleCap('plugin', id)))}
            {caps.avatar && capChip(`🤖 分身:${caps.avatar}`, () => setCaps((c: any) => ({ ...c, avatar: '' })))}
          </div>
        )}

        {/* 文本域 */}
        <textarea value={text} onChange={e => setText(e.target.value)}
                  onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send() } }}
                  rows={2}
                  placeholder={`给智能体发送指令…（Enter 发送，Shift+Enter 换行；文件产物由 AI 自动判断格式）`}
                  className="w-full px-4 pt-3 pb-1 text-sm resize-none bg-transparent border-0 focus:outline-none placeholder:text-slate2/70"/>

        {/* 底行：模式提示 + 发送 */}
        <div className="flex items-center gap-3 px-4 pb-3">
          <p className="text-[10px] text-slate2 flex-1 min-w-0 truncate">
            <span className="inline-block w-1.5 h-1.5 rounded-full bg-steel-light mr-1 align-middle"/>
            {MODE_LABEL[conv?.agent_mode || draftMode] || '-'} ·
            {(conv?.agent_mode || draftMode) === 'full_auto' && ' 智能体自主执行，工作区可增删改查'}
            {(conv?.agent_mode || draftMode) === 'approve_risky' && ' 普通写入自动执行，高风险动作需审批'}
            {(conv?.agent_mode || draftMode) === 'sandbox' && ' 所有操作先沙箱执行，审批后落盘'}
          </p>
          <button onClick={send} disabled={sending || !text.trim()} title="发送"
                  className="w-9 h-9 rounded-full bg-gradient-brand text-white shadow-glow flex items-center justify-center hover:opacity-90 disabled:opacity-40 disabled:shadow-none transition-all shrink-0">
            <Send size={16} />
          </button>
        </div>
      </div>
    </div>
  )
}
