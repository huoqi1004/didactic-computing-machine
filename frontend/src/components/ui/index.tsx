import { type ReactNode, useState, useEffect } from 'react'
import { X, ChevronLeft, ChevronRight, Search, Loader2 } from 'lucide-react'
import { cn } from '../../lib/utils'

/* ===================== PageHeader ===================== */

interface PageHeaderProps {
  title: string
  description?: string
  icon?: ReactNode
  actions?: ReactNode
}

export function PageHeader({ title, description, icon, actions }: PageHeaderProps) {
  return (
    <div className="flex items-start justify-between gap-4 mb-6">
      <div className="flex items-start gap-3">
        {icon && (
          <div className="w-10 h-10 rounded-xl bg-primary/10 flex items-center justify-center text-primary shrink-0">
            {icon}
          </div>
        )}
        <div>
          <h1 className="text-xl font-bold text-white">{title}</h1>
          {description && <p className="text-sm text-slate-500 mt-0.5">{description}</p>}
        </div>
      </div>
      {actions && <div className="flex items-center gap-2 shrink-0">{actions}</div>}
    </div>
  )
}

/* ===================== Card ===================== */

interface CardProps {
  children: ReactNode
  className?: string
  title?: string
  action?: ReactNode
}

export function Card({ children, className, title, action }: CardProps) {
  return (
    <div className={cn('bg-bg-card/80 backdrop-blur-sm rounded-xl border border-[#1e2536] p-5', className)}>
      {(title || action) && (
        <div className="flex items-center justify-between mb-4">
          {title && <h3 className="text-sm font-semibold text-white">{title}</h3>}
          {action}
        </div>
      )}
      {children}
    </div>
  )
}

/* ===================== StatusBadge ===================== */

type BadgeVariant = 'success' | 'warning' | 'danger' | 'info' | 'neutral' | 'primary' | 'accent'

interface StatusBadgeProps {
  variant?: BadgeVariant
  children: ReactNode
  dot?: boolean
}

const badgeStyles: Record<BadgeVariant, string> = {
  success: 'bg-accent/10 text-accent border-accent/20',
  warning: 'bg-status-paused/10 text-status-paused border-status-paused/20',
  danger: 'bg-status-failed/10 text-status-failed border-status-failed/20',
  info: 'bg-primary/10 text-primary border-primary/20',
  neutral: 'bg-slate-500/10 text-slate-400 border-slate-500/20',
  primary: 'bg-primary/15 text-primary border-primary/30',
  accent: 'bg-accent/15 text-accent border-accent/30',
}

const dotStyles: Record<BadgeVariant, string> = {
  success: 'bg-accent',
  warning: 'bg-status-paused',
  danger: 'bg-status-failed',
  info: 'bg-primary',
  neutral: 'bg-slate-500',
  primary: 'bg-primary',
  accent: 'bg-accent',
}

export function StatusBadge({ variant = 'neutral', children, dot }: StatusBadgeProps) {
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-medium border',
        badgeStyles[variant],
      )}
    >
      {dot && <span className={cn('w-1.5 h-1.5 rounded-full', dotStyles[variant])} />}
      {children}
    </span>
  )
}

/* ===================== DataTable ===================== */

export interface Column<T> {
  key: string
  label: string
  render?: (row: T) => ReactNode
  sortable?: boolean
  width?: string
  align?: 'left' | 'center' | 'right'
}

interface DataTableProps<T> {
  columns: Column<T>[]
  data: T[]
  loading?: boolean
  emptyText?: string
  pageSize?: number
  searchable?: boolean
  searchPlaceholder?: string
  searchKeys?: (keyof T)[]
  onRowClick?: (row: T) => void
}

export function DataTable<T extends Record<string, any>>({
  columns,
  data,
  loading,
  emptyText = '暂无数据',
  pageSize = 10,
  searchable = true,
  searchPlaceholder = '搜索...',
  searchKeys,
  onRowClick,
}: DataTableProps<T>) {
  const [page, setPage] = useState(1)
  const [query, setQuery] = useState('')
  const [sortKey, setSortKey] = useState<string | null>(null)
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('asc')

  // 搜索过滤
  const filtered = query && searchKeys
    ? data.filter((row) =>
        searchKeys.some((key) =>
          String(row[key] || '').toLowerCase().includes(query.toLowerCase()),
        ),
      )
    : data

  // 排序
  const sorted = sortKey
    ? [...filtered].sort((a, b) => {
        const av = a[sortKey]
        const bv = b[sortKey]
        if (av == null) return 1
        if (bv == null) return -1
        const cmp = String(av).localeCompare(String(bv), 'zh-CN', { numeric: true })
        return sortDir === 'asc' ? cmp : -cmp
      })
    : filtered

  // 分页
  const totalPages = Math.max(1, Math.ceil(sorted.length / pageSize))
  const currentPage = Math.min(page, totalPages)
  const pageData = sorted.slice((currentPage - 1) * pageSize, currentPage * pageSize)

  useEffect(() => setPage(1), [query])

  const handleSort = (key: string) => {
    if (sortKey === key) {
      setSortDir(sortDir === 'asc' ? 'desc' : 'asc')
    } else {
      setSortKey(key)
      setSortDir('asc')
    }
  }

  return (
    <div>
      {/* 搜索栏 */}
      {searchable && (
        <div className="flex items-center gap-3 mb-4">
          <div className="relative flex-1 max-w-xs">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-600" />
            <input
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder={searchPlaceholder}
              className="w-full pl-9 pr-3 py-2 rounded-lg bg-bg-input border border-[#2a3142] text-sm text-white placeholder:text-slate-600 focus:outline-none focus:border-primary/40 transition-colors"
            />
          </div>
          <span className="text-xs text-slate-500">{filtered.length} 条记录</span>
        </div>
      )}

      {/* 表格 */}
      <div className="overflow-x-auto">
        <table className="w-full">
          <thead>
            <tr className="border-b border-[#1e2536]">
              {columns.map((col) => (
                <th
                  key={col.key}
                  onClick={() => col.sortable && handleSort(col.key)}
                  className={cn(
                    'px-4 py-3 text-xs font-medium text-slate-500 uppercase tracking-wider whitespace-nowrap',
                    col.align === 'right' && 'text-right',
                    col.align === 'center' && 'text-center',
                    !col.align && 'text-left',
                    col.sortable && 'cursor-pointer hover:text-slate-300 select-none',
                  )}
                  style={{ width: col.width }}
                >
                  <span className="inline-flex items-center gap-1">
                    {col.label}
                    {col.sortable && sortKey === col.key && (
                      <span className="text-primary">{sortDir === 'asc' ? '↑' : '↓'}</span>
                    )}
                  </span>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr>
                <td colSpan={columns.length} className="py-12 text-center">
                  <Loader2 className="w-6 h-6 text-primary animate-spin mx-auto" />
                  <p className="text-sm text-slate-500 mt-2">加载中...</p>
                </td>
              </tr>
            ) : pageData.length === 0 ? (
              <tr>
                <td colSpan={columns.length} className="py-12 text-center text-sm text-slate-600">
                  {emptyText}
                </td>
              </tr>
            ) : (
              pageData.map((row, idx) => (
                <tr
                  key={idx}
                  onClick={() => onRowClick?.(row)}
                  className={cn(
                    'border-b border-[#1e2536]/50 transition-colors',
                    onRowClick && 'cursor-pointer hover:bg-bg-hover',
                  )}
                >
                  {columns.map((col) => (
                    <td
                      key={col.key}
                      className={cn(
                        'px-4 py-3 text-sm text-slate-300 whitespace-nowrap',
                        col.align === 'right' && 'text-right',
                        col.align === 'center' && 'text-center',
                      )}
                    >
                      {col.render ? col.render(row) : String(row[col.key] ?? '—')}
                    </td>
                  ))}
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {/* 分页 */}
      {totalPages > 1 && (
        <div className="flex items-center justify-between mt-4">
          <span className="text-xs text-slate-500">
            第 {currentPage}/{totalPages} 页，共 {sorted.length} 条
          </span>
          <div className="flex items-center gap-2">
            <button
              onClick={() => setPage((p) => Math.max(1, p - 1))}
              disabled={currentPage === 1}
              className="p-1.5 rounded-lg bg-bg-input border border-[#2a3142] text-slate-400 hover:text-white disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
            >
              <ChevronLeft className="w-4 h-4" />
            </button>
            <span className="text-xs text-slate-400 tabular-nums px-2">{currentPage}</span>
            <button
              onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
              disabled={currentPage === totalPages}
              className="p-1.5 rounded-lg bg-bg-input border border-[#2a3142] text-slate-400 hover:text-white disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
            >
              <ChevronRight className="w-4 h-4" />
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

/* ===================== Modal ===================== */

interface ModalProps {
  open: boolean
  onClose: () => void
  title?: string
  children: ReactNode
  size?: 'sm' | 'md' | 'lg' | 'xl'
}

const modalSizes = {
  sm: 'max-w-md',
  md: 'max-w-lg',
  lg: 'max-w-2xl',
  xl: 'max-w-4xl',
}

export function Modal({ open, onClose, title, children, size = 'md' }: ModalProps) {
  if (!open) return null
  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center p-4">
      {/* 遮罩 */}
      <div
        className="absolute inset-0 bg-black/60 backdrop-blur-sm"
        onClick={onClose}
      />
      {/* 内容 */}
      <div className={cn('relative w-full bg-bg-card rounded-2xl border border-[#2a3142] shadow-2xl max-h-[85vh] overflow-hidden flex flex-col', modalSizes[size])}>
        {title && (
          <div className="flex items-center justify-between px-6 py-4 border-b border-[#1e2536]">
            <h2 className="text-base font-semibold text-white">{title}</h2>
            <button
              onClick={onClose}
              className="text-slate-500 hover:text-white transition-colors"
            >
              <X className="w-5 h-5" />
            </button>
          </div>
        )}
        <div className="overflow-y-auto p-6">{children}</div>
      </div>
    </div>
  )
}

/* ===================== StatCard ===================== */

interface StatCardProps {
  label: string
  value: string | number
  icon?: ReactNode
  trend?: string
  trendUp?: boolean
  variant?: 'primary' | 'accent' | 'warning' | 'danger'
}

export function StatCard({ label, value, icon, trend, trendUp, variant = 'primary' }: StatCardProps) {
  const variantStyles = {
    primary: 'text-primary',
    accent: 'text-accent',
    warning: 'text-status-paused',
    danger: 'text-status-failed',
  }
  return (
    <div className="bg-bg-card/80 backdrop-blur-sm rounded-xl border border-[#1e2536] p-4">
      <div className="flex items-center justify-between mb-2">
        <span className="text-xs text-slate-500 font-medium">{label}</span>
        {icon && <div className={variantStyles[variant]}>{icon}</div>}
      </div>
      <div className="text-2xl font-bold text-white tabular-nums">{value}</div>
      {trend && (
        <div className={cn('text-xs mt-1', trendUp ? 'text-accent' : 'text-status-paused')}>
          {trendUp ? '↑' : '↓'} {trend}
        </div>
      )}
    </div>
  )
}

/* ===================== FormField ===================== */

interface FormFieldProps {
  label: string
  children: ReactNode
  required?: boolean
  hint?: string
}

export function FormField({ label, children, required, hint }: FormFieldProps) {
  return (
    <div>
      <label className="block text-xs font-medium text-slate-400 mb-1.5">
        {label}
        {required && <span className="text-status-failed ml-0.5">*</span>}
      </label>
      {children}
      {hint && <p className="text-[11px] text-slate-600 mt-1">{hint}</p>}
    </div>
  )
}

export const inputClass =
  'w-full px-3 py-2 rounded-lg bg-bg-input border border-[#2a3142] text-white text-sm placeholder:text-slate-600 focus:outline-none focus:border-primary/40 transition-colors'
