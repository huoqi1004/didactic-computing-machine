import { useState, useEffect, useCallback } from 'react'
import {
  FileText,
  FileSpreadsheet,
  Presentation,
  FileType,
  Download,
  Loader2,
  CheckCircle2,
  XCircle,
  RefreshCw,
  Sparkles,
  FileDown,
} from 'lucide-react'
import { apiService } from '../services/api'
import { PageHeader, Card, StatusBadge, FormField, inputClass } from '../components/ui'
import { cn } from '../lib/utils'

/* ===================== 类型定义 ===================== */

type DocFormat = 'docx' | 'xlsx' | 'pptx' | 'pdf'

interface AgentTemplate {
  template: string
  format: string
  name: string
}

interface AgentTemplates {
  agent_id: string
  templates: AgentTemplate[]
}

interface GenerateResult {
  success: boolean
  file_path: string
  filename: string
  format: string
  file_size: number
  generated_at: string
}

/* ===================== 格式配置 ===================== */

const FORMAT_LIST: { value: DocFormat; label: string; icon: typeof FileText }[] = [
  { value: 'xlsx', label: 'Excel', icon: FileSpreadsheet },
  { value: 'pdf', label: 'PDF', icon: FileType },
  { value: 'docx', label: 'Word', icon: FileText },
  { value: 'pptx', label: 'PPT', icon: Presentation },
]

/* ===================== 主组件 ===================== */

export default function DocumentGeneration() {
  // 模板列表
  const [templates, setTemplates] = useState<AgentTemplates[]>([])
  const [templatesLoading, setTemplatesLoading] = useState(true)

  // 表单状态
  const [agentId, setAgentId] = useState('')
  const [agentTemplates, setAgentTemplates] = useState<AgentTemplate[]>([])
  const [templateType, setTemplateType] = useState('')
  const [filename, setFilename] = useState('')
  const [title, setTitle] = useState('')
  const [jsonData, setJsonData] = useState('{\n  "title": "示例文档",\n  "items": []\n}')
  const [format, setFormat] = useState<DocFormat>('xlsx')

  // 生成状态
  const [generating, setGenerating] = useState(false)
  const [result, setResult] = useState<GenerateResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [downloading, setDownloading] = useState(false)

  /* ----- 加载模板列表 ----- */
  const fetchTemplates = useCallback(async () => {
    setTemplatesLoading(true)
    const data = await apiService.getDocumentTemplates()
    setTemplates(data)
    setTemplatesLoading(false)
  }, [])

  useEffect(() => {
    fetchTemplates()
  }, [fetchTemplates])

  /* ----- Agent 切换时加载对应模板 ----- */
  const handleAgentChange = async (id: string) => {
    setAgentId(id)
    setTemplateType('')
    setAgentTemplates([])
    if (!id) return
    const data = await apiService.getAgentTemplates(id)
    setAgentTemplates(data)
    if (data.length > 0) {
      setTemplateType(data[0].template)
      // 同步默认格式
      const matched = FORMAT_LIST.find((f) => f.value === data[0].format)
      if (matched) setFormat(matched.value)
    }
  }

  /* ----- 选择模板时同步格式 ----- */
  const handleTemplateChange = (tpl: string) => {
    setTemplateType(tpl)
    const matched = agentTemplates.find((t) => t.template === tpl)
    if (matched) {
      const fmt = FORMAT_LIST.find((f) => f.value === matched.format)
      if (fmt) setFormat(fmt.value)
    }
  }

  /* ----- 触发文件下载 ----- */
  const triggerDownload = (blob: Blob, name: string) => {
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = name
    a.style.display = 'none'
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  }

  /* ----- 生成文档 ----- */
  const handleGenerate = async () => {
    setError(null)
    setResult(null)

    // 校验
    if (!agentId) {
      setError('请选择 Agent')
      return
    }
    if (!templateType) {
      setError('请选择模板')
      return
    }
    if (!filename.trim()) {
      setError('请输入文件名')
      return
    }

    // 解析 JSON
    let parsedData: Record<string, unknown>
    try {
      parsedData = JSON.parse(jsonData)
    } catch (e) {
      setError('JSON 数据格式错误，请检查输入')
      return
    }

    setGenerating(true)
    try {
      const res = await apiService.generateDocument({
        agent_id: agentId,
        format,
        template_type: templateType,
        filename: filename.trim(),
        data: parsedData,
        title: title.trim() || undefined,
      })

      if (!res.success) {
        setError('文档生成失败，请稍后重试')
        return
      }
      setResult(res)
    } catch (e) {
      setError(e instanceof Error ? e.message : '文档生成失败')
    } finally {
      setGenerating(false)
    }
  }

  /* ----- 下载已生成的文档 ----- */
  const handleDownload = async () => {
    if (!result?.filename) return
    setDownloading(true)
    try {
      const blob = await apiService.downloadDocument(result.filename)
      triggerDownload(blob, result.filename)
    } catch (e) {
      setError('下载失败，请稍后重试')
    } finally {
      setDownloading(false)
    }
  }

  /* ----- 格式化文件大小 ----- */
  const formatFileSize = (bytes: number) => {
    if (!bytes) return '-'
    if (bytes < 1024) return `${bytes} B`
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
    return `${(bytes / 1024 / 1024).toFixed(2)} MB`
  }

  /* ----- 收集所有 Agent ID 用于下拉 ----- */
  const allAgents = templates.map((t) => t.agent_id)

  return (
    <div className="space-y-6 animate-fade-in">
      {/* 页面标题 */}
      <PageHeader
        title="文档中心"
        description="管理文档模板，按需生成并导出 Word / Excel / PDF / PPT 文档"
        icon={<FileText className="w-5 h-5" />}
        actions={
          <button
            onClick={fetchTemplates}
            className="inline-flex items-center gap-2 px-3 py-2 rounded-lg bg-bg-input border border-[#2a3142] text-sm text-slate-300 hover:text-white hover:border-primary/30 transition-colors"
          >
            <RefreshCw className={cn('w-4 h-4', templatesLoading && 'animate-spin')} />
            刷新模板
          </button>
        }
      />

      {/* 模板列表 */}
      <Card title="支持的文档模板" action={<StatusBadge variant="info">{templates.length} 个 Agent</StatusBadge>}>
        {templatesLoading ? (
          <div className="flex items-center justify-center py-12">
            <Loader2 className="w-6 h-6 text-primary animate-spin" />
            <span className="text-sm text-slate-500 ml-2">加载模板中...</span>
          </div>
        ) : templates.length === 0 ? (
          <div className="py-12 text-center">
            <FileText className="w-10 h-10 text-slate-700 mx-auto mb-3" />
            <p className="text-sm text-slate-500">暂无可用模板</p>
            <p className="text-xs text-slate-600 mt-1">后端服务未连接时将显示空列表</p>
          </div>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {templates.map((agentTpl) => (
              <div
                key={agentTpl.agent_id}
                className="rounded-lg border border-[#2a3142] bg-bg-input/50 p-4 hover:border-primary/30 transition-colors"
              >
                <div className="flex items-center gap-2 mb-3">
                  <div className="w-8 h-8 rounded-lg bg-primary/10 flex items-center justify-center">
                    <Sparkles className="w-4 h-4 text-primary" />
                  </div>
                  <span className="text-sm font-semibold text-white truncate">
                    {agentTpl.agent_id}
                  </span>
                </div>
                <div className="space-y-1.5">
                  {(agentTpl.templates ?? []).map((tpl) => {
                    const fmtIcon = FORMAT_LIST.find((f) => f.value === tpl.format)
                    const Icon = fmtIcon?.icon ?? FileText
                    return (
                      <div
                        key={`${tpl.template}-${tpl.format}`}
                        className="flex items-center gap-2 text-xs"
                      >
                        <Icon className="w-3.5 h-3.5 text-slate-500 shrink-0" />
                        <span className="text-slate-300 truncate flex-1">{tpl.name}</span>
                        <StatusBadge variant="neutral">{tpl.format}</StatusBadge>
                      </div>
                    )
                  })}
                </div>
              </div>
            ))}
          </div>
        )}
      </Card>

      {/* 手动生成表单 */}
      <Card title="手动生成文档">
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
          {/* 左列：表单字段 */}
          <div className="space-y-4">
            {/* Agent 选择 */}
            <FormField label="选择 Agent" required>
              <select
                value={agentId}
                onChange={(e) => handleAgentChange(e.target.value)}
                className={cn(inputClass, 'cursor-pointer')}
              >
                <option value="">请选择 Agent</option>
                {allAgents.map((id) => (
                  <option key={id} value={id}>
                    {id}
                  </option>
                ))}
                {/* 兜底：若模板列表为空，提供常用 Agent */}
                {allAgents.length === 0 && (
                  <>
                    <option value="accounting_agent">会计 Agent</option>
                    <option value="invoice_agent">发票 Agent</option>
                    <option value="hr_agent">人力资源 Agent</option>
                    <option value="inventory_agent">库存 Agent</option>
                    <option value="report_agent">报表 Agent</option>
                  </>
                )}
              </select>
            </FormField>

            {/* 模板选择 */}
            <FormField label="选择模板" required hint="模板列表根据所选 Agent 动态加载">
              <select
                value={templateType}
                onChange={(e) => handleTemplateChange(e.target.value)}
                disabled={!agentId}
                className={cn(inputClass, 'cursor-pointer', !agentId && 'opacity-50 cursor-not-allowed')}
              >
                <option value="">请选择模板</option>
                {agentTemplates.map((tpl) => (
                  <option key={tpl.template} value={tpl.template}>
                    {tpl.name}（{tpl.format}）
                  </option>
                ))}
                {/* 兜底模板 */}
                {agentId && agentTemplates.length === 0 && (
                  <>
                    <option value="standard_report">标准报表</option>
                    <option value="summary_report">汇总报告</option>
                    <option value="detail_list">明细清单</option>
                  </>
                )}
              </select>
            </FormField>

            {/* 文件名 */}
            <FormField label="文件名" required hint="无需包含扩展名，系统将自动添加">
              <input
                type="text"
                value={filename}
                onChange={(e) => setFilename(e.target.value)}
                placeholder="例如：2026年7月财务月报"
                className={inputClass}
              />
            </FormField>

            {/* 文档标题 */}
            <FormField label="文档标题" hint="可选，将显示在文档内容中">
              <input
                type="text"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                placeholder="例如：企业数字化大脑 - 财务月度报告"
                className={inputClass}
              />
            </FormField>

            {/* 格式选择 */}
            <FormField label="导出格式" required>
              <div className="grid grid-cols-4 gap-2">
                {FORMAT_LIST.map((opt) => {
                  const Icon = opt.icon
                  const active = format === opt.value
                  return (
                    <button
                      key={opt.value}
                      type="button"
                      onClick={() => setFormat(opt.value)}
                      className={cn(
                        'flex flex-col items-center gap-1.5 py-3 rounded-lg border transition-all',
                        active
                          ? 'border-primary bg-primary/10 text-primary'
                          : 'border-[#2a3142] bg-bg-input text-slate-400 hover:text-white hover:border-slate-500',
                      )}
                    >
                      <Icon className="w-5 h-5" />
                      <span className="text-xs font-medium">{opt.label}</span>
                    </button>
                  )
                })}
              </div>
            </FormField>
          </div>

          {/* 右列：JSON 数据 + 生成按钮 */}
          <div className="space-y-4">
            <FormField label="文档数据（JSON）" required hint="请输入合法的 JSON 格式数据">
              <textarea
                value={jsonData}
                onChange={(e) => setJsonData(e.target.value)}
                rows={14}
                spellCheck={false}
                className={cn(
                  inputClass,
                  'font-mono text-xs leading-relaxed resize-y',
                )}
                placeholder='{"title": "文档标题", "items": [...]}'
              />
            </FormField>

            {/* 生成按钮 */}
            <button
              type="button"
              onClick={handleGenerate}
              disabled={generating}
              className={cn(
                'w-full inline-flex items-center justify-center gap-2 px-4 py-2.5 rounded-lg font-medium transition-colors',
                'bg-blue-600 hover:bg-blue-700 text-white',
                'disabled:opacity-60 disabled:cursor-not-allowed',
              )}
            >
              {generating ? (
                <>
                  <Loader2 className="w-4 h-4 animate-spin" />
                  正在生成...
                </>
              ) : (
                <>
                  <Sparkles className="w-4 h-4" />
                  生成文档
                </>
              )}
            </button>
          </div>
        </div>

        {/* 错误提示 */}
        {error && (
          <div className="mt-4 flex items-center gap-2 px-4 py-3 rounded-lg bg-status-failed/10 border border-status-failed/20">
            <XCircle className="w-4 h-4 text-status-failed shrink-0" />
            <span className="text-sm text-status-failed">{error}</span>
          </div>
        )}

        {/* 生成结果 */}
        {result && (
          <div className="mt-4 rounded-lg border border-accent/20 bg-accent/5 p-5">
            <div className="flex items-center gap-2 mb-4">
              <CheckCircle2 className="w-5 h-5 text-accent" />
              <span className="text-sm font-semibold text-accent">文档生成成功</span>
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-3 mb-4">
              <div>
                <span className="text-xs text-slate-500">文件名</span>
                <p className="text-sm text-white font-medium mt-0.5 break-all">{result.filename}</p>
              </div>
              <div>
                <span className="text-xs text-slate-500">格式</span>
                <p className="text-sm text-white font-medium mt-0.5 uppercase">{result.format}</p>
              </div>
              <div>
                <span className="text-xs text-slate-500">文件大小</span>
                <p className="text-sm text-white font-medium mt-0.5">{formatFileSize(result.file_size)}</p>
              </div>
              <div>
                <span className="text-xs text-slate-500">生成时间</span>
                <p className="text-sm text-white font-medium mt-0.5">
                  {result.generated_at || '-'}
                </p>
              </div>
              {result.file_path && (
                <div className="sm:col-span-2">
                  <span className="text-xs text-slate-500">存储路径</span>
                  <p className="text-xs text-slate-400 font-mono mt-0.5 break-all">{result.file_path}</p>
                </div>
              )}
            </div>
            <button
              type="button"
              onClick={handleDownload}
              disabled={downloading}
              className={cn(
                'inline-flex items-center gap-2 px-4 py-2 rounded-lg font-medium transition-colors',
                'bg-accent/15 hover:bg-accent/25 text-accent border border-accent/30',
                'disabled:opacity-60 disabled:cursor-not-allowed',
              )}
            >
              {downloading ? (
                <>
                  <Loader2 className="w-4 h-4 animate-spin" />
                  下载中...
                </>
              ) : (
                <>
                  <Download className="w-4 h-4" />
                  下载文件
                </>
              )}
            </button>
          </div>
        )}
      </Card>

      {/* 使用说明 */}
      <Card title="使用说明">
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          <div className="flex items-start gap-3">
            <div className="w-8 h-8 rounded-lg bg-primary/10 flex items-center justify-center shrink-0">
              <FileText className="w-4 h-4 text-primary" />
            </div>
            <div>
              <h4 className="text-sm font-semibold text-white mb-1">选择模板</h4>
              <p className="text-xs text-slate-500 leading-relaxed">
                根据业务场景选择对应的 Agent 与文档模板，系统将按模板格式填充数据。
              </p>
            </div>
          </div>
          <div className="flex items-start gap-3">
            <div className="w-8 h-8 rounded-lg bg-primary/10 flex items-center justify-center shrink-0">
              <FileDown className="w-4 h-4 text-primary" />
            </div>
            <div>
              <h4 className="text-sm font-semibold text-white mb-1">填写数据</h4>
              <p className="text-xs text-slate-500 leading-relaxed">
                在 JSON 数据框中输入文档所需的结构化数据，支持嵌套对象与数组。
              </p>
            </div>
          </div>
          <div className="flex items-start gap-3">
            <div className="w-8 h-8 rounded-lg bg-primary/10 flex items-center justify-center shrink-0">
              <Download className="w-4 h-4 text-primary" />
            </div>
            <div>
              <h4 className="text-sm font-semibold text-white mb-1">生成下载</h4>
              <p className="text-xs text-slate-500 leading-relaxed">
                选择导出格式后点击生成，完成后可一键下载 Word、Excel、PDF 或 PPT 文件。
              </p>
            </div>
          </div>
        </div>
      </Card>
    </div>
  )
}
