/**
 * 通用自定义下拉（Trae 风格）：触发按钮 + 上/下浮菜单 + 选中勾。
 * 替代原生 <select>，观感与现代智能体 IDE 一致。
 */
import { useEffect, useRef, useState } from 'react'
import { Check, ChevronDown } from 'lucide-react'

export interface DropdownOption {
  value: string | number
  label: string
  hint?: string
}

export default function Dropdown({ value, options, onChange,
                                   placeholder = '请选择', width = 180,
                                   align = 'right', up = false }: {
  value: string | number
  options: DropdownOption[]
  onChange: (v: string | number) => void
  placeholder?: string
  width?: number
  align?: 'left' | 'right'
  up?: boolean
}) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  const current = options.find(o => o.value === value)

  useEffect(() => {
    const close = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', close)
    return () => document.removeEventListener('mousedown', close)
  }, [])

  return (
    <div ref={ref} className="relative" style={{ width }}>
      <button type="button" onClick={() => setOpen(v => !v)}
              className={`w-full flex items-center gap-2 rounded-lg border px-2.5 py-1.5 text-xs transition-colors ${
                open ? 'border-steel-light bg-blue-50/50' : 'border-border bg-card hover:border-steel-light'}`}>
        <span className="flex-1 min-w-0 text-left truncate text-ink">
          {current ? current.label : placeholder}
        </span>
        <ChevronDown size={13}
                     className={`shrink-0 text-slate2 transition-transform ${open ? 'rotate-180' : ''}`} />
      </button>
      {open && (
        <div className={`absolute z-30 ${up ? 'bottom-full mb-1.5' : 'top-full mt-1.5'} ${
          align === 'right' ? 'right-0' : 'left-0'} w-full min-w-max max-w-xs rounded-xl bg-card shadow-lift border border-border overflow-hidden`}>
          {options.map(o => (
            <button key={o.value} type="button"
                    onClick={() => { onChange(o.value); setOpen(false) }}
                    className={`w-full flex items-center gap-2 px-3 py-2 text-xs text-left transition-colors ${
                      o.value === value ? 'bg-blue-50/60 text-navy' : 'text-ink hover:bg-canvas'}`}>
              <span className="flex-1 min-w-0">
                <span className="block truncate font-medium">{o.label}</span>
                {o.hint && <span className="block text-[10px] text-slate2 truncate">{o.hint}</span>}
              </span>
              {o.value === value && <Check size={13} className="shrink-0 text-navy" />}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
