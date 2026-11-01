import { useState, useRef, useEffect } from 'react'
import {
  Download,
  FileText,
  FileSpreadsheet,
  Presentation,
  FileType,
  ChevronDown,
  Loader2,
  CheckCircle2,
  XCircle,
} from 'lucide-react'
import { apiService } from '../services/api'
import { cn } from '../lib/utils'

/* ===================== 类型定义 ===================== */

type DocFormat = 'docx' | 'xlsx' | 'pptx' | 'pdf'

interface ExportButtonProps {
  agentId: string // Agent标识
  templateType: string // 模板类型
  filename: string // 文件名
  data: Record<string, unknown> // 文档数据
  title?: string // 文档标题
  formats?: DocFormat[] // 支持的格式，默认全部
  label?: string // 按钮文字，默认"导出"
  size?: 'sm' | 'md' // 按钮大小
}

/* ===================== 格式配置 ===================== */

interface FormatOption {
  value: DocFormat
  label: string
  icon: typeof FileText
}

const FORMAT_OPTIONS: FormatOption[] = [
  { value: 'xlsx', label: '导出 Excel', icon: FileSpreadsheet },
  { value: 'pdf', label: '导出 PDF', icon: FileType },
  { value: 'docx', label: '导出 Word', icon: FileText },
  { value: 'pptx', label: '导出 PPT', icon: Presentation },
]

/* ===================== 主组件 ===================== */

export default function ExportButton({
  agentId,
  templateType,
  filename,
  data,
  title,
  formats = ['docx', 'xlsx', 'pptx', 'pdf'],
  label = '导出',
  size = 'md',
}: ExportButtonProps) {
  const [open, setOpen] = useState(false)
  const [loading, setLoading] = useState(false)
  const [loadingFormat, setLoadingFormat] = useState<DocFormat | null>(null)
  const [message, setMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null)
  const containerRef = useRef<HTMLDivElement>(null)

  const availableFormats = FORMAT_OPTIONS.filter((f) => formats.includes(f.value))

  // 点击外部关闭下拉菜单
  useEffect(() => {
    if (!open) return
    const handler = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [open])

  // 自动清除提示消息
  useEffect(() => {
    if (!message) return
    const timer = setTimeout(() => setMessage(null), 4000)
    return () => clearTimeout(timer)
  }, [message])

  /** 触发文件下载 */
  const triggerDownload = (blob: Blob, name: string) => {
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = name
    a.style.display = 'none'
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    // 释放对象 URL
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  }

  /** 处理导出 */
  const handleExport = async (format: DocFormat) => {
    setLoading(true)
    setLoadingFormat(format)
    setOpen(false)

    try {
      // 1. 生成文档
      const result = await apiService.generateDocument({
        agent_id: agentId,
        format,
        template_type: templateType,
        filename,
        data,
        title,
      })

      if (!result.success) {
        setMessage({ type: 'error', text: `文档生成失败：${result.filename || '未知错误'}` })
        return
      }

      // 2. 下载文档
      const blob = await apiService.downloadDocument(result.filename)
      const downloadName = result.filename || `${filename}.${format}`
      triggerDownload(blob, downloadName)

      const sizeText = result.file_size
        ? `（${(result.file_size / 1024).toFixed(1)} KB）`
        : ''
      setMessage({ type: 'success', text: `${downloadName} 导出成功${sizeText}` })
    } catch (err) {
      setMessage({
        type: 'error',
        text: `导出失败：${err instanceof Error ? err.message : '请稍后重试'}`,
      })
    } finally {
      setLoading(false)
      setLoadingFormat(null)
    }
  }

  const sizeClass = size === 'sm' ? 'px-3 py-1.5 text-xs' : 'px-4 py-2 text-sm'
  const iconSize = size === 'sm' ? 'w-3.5 h-3.5' : 'w-4 h-4'

  return (
    <div className="relative inline-block" ref={containerRef}>
      {/* 主按钮 + 下拉触发 */}
      <button
        type="button"
        disabled={loading}
        onClick={() => setOpen(!open)}
        className={cn(
          'inline-flex items-center gap-2 rounded-lg font-medium transition-colors',
          'bg-blue-600 hover:bg-blue-700 text-white',
          'disabled:opacity-60 disabled:cursor-not-allowed',
          sizeClass,
        )}
      >
        {loading ? (
          <Loader2 className={cn('animate-spin', iconSize)} />
        ) : (
          <Download className={iconSize} />
        )}
        <span>{loading ? '生成中...' : label}</span>
        <ChevronDown
          className={cn('transition-transform', iconSize, open && 'rotate-180')}
        />
      </button>

      {/* 下拉菜单 */}
      {open && !loading && (
        <div className="absolute right-0 top-full mt-1.5 w-44 bg-bg-card border border-[#2a3142] rounded-lg shadow-2xl overflow-hidden z-50">
          {availableFormats.map((opt) => {
            const Icon = opt.icon
            return (
              <button
                key={opt.value}
                type="button"
                onClick={() => handleExport(opt.value)}
                className="w-full flex items-center gap-2.5 px-3.5 py-2.5 text-sm text-slate-300 hover:text-white hover:bg-primary/10 transition-colors text-left"
              >
                <Icon className="w-4 h-4 text-primary shrink-0" />
                <span>{opt.label}</span>
              </button>
            )
          })}
          {availableFormats.length === 0 && (
            <div className="px-3.5 py-3 text-xs text-slate-600 text-center">无可导出格式</div>
          )}
        </div>
      )}

      {/* 状态提示 */}
      {message && (
        <div
          className={cn(
            'absolute right-0 top-full mt-1.5 flex items-center gap-2 px-3 py-2 rounded-lg text-xs font-medium border whitespace-nowrap z-50',
            message.type === 'success'
              ? 'bg-accent/10 text-accent border-accent/20'
              : 'bg-status-failed/10 text-status-failed border-status-failed/20',
          )}
        >
          {message.type === 'success' ? (
            <CheckCircle2 className="w-3.5 h-3.5 shrink-0" />
          ) : (
            <XCircle className="w-3.5 h-3.5 shrink-0" />
          )}
          <span>{message.text}</span>
        </div>
      )}
    </div>
  )
}
