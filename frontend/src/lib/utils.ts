import { clsx, type ClassValue } from 'clsx'
import { twMerge } from 'tailwind-merge'
import type { AgentStatus, EventLevel, InventoryStatus, VoucherStatus } from '../stores/systemStore'

/** 合并 Tailwind 类名，处理冲突 */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs))
}

/* ============ Agent 状态 ============ */
export const AGENT_STATUS_CONFIG: Record<
  AgentStatus,
  { label: string; color: string; bg: string; border: string; dot: string }
> = {
  running: {
    label: '运行中',
    color: 'text-status-running',
    bg: 'bg-status-running/10',
    border: 'border-status-running/30',
    dot: 'bg-status-running',
  },
  paused: {
    label: '已暂停',
    color: 'text-status-paused',
    bg: 'bg-status-paused/10',
    border: 'border-status-paused/30',
    dot: 'bg-status-paused',
  },
  circuit_open: {
    label: '已熔断',
    color: 'text-status-circuit',
    bg: 'bg-status-circuit/10',
    border: 'border-status-circuit/30',
    dot: 'bg-status-circuit',
  },
  idle: {
    label: '空闲',
    color: 'text-status-idle',
    bg: 'bg-status-idle/10',
    border: 'border-status-idle/30',
    dot: 'bg-status-idle',
  },
}

/* ============ 事件级别 ============ */
export const EVENT_LEVEL_CONFIG: Record<
  EventLevel,
  { label: string; color: string; bg: string; border: string }
> = {
  info: { label: '信息', color: 'text-blue-400', bg: 'bg-blue-500/10', border: 'border-blue-500/30' },
  success: { label: '成功', color: 'text-accent', bg: 'bg-accent/10', border: 'border-accent/30' },
  warning: { label: '警告', color: 'text-status-paused', bg: 'bg-status-paused/10', border: 'border-status-paused/30' },
  error: { label: '错误', color: 'text-status-circuit', bg: 'bg-status-circuit/10', border: 'border-status-circuit/30' },
}

/* ============ 库存状态 ============ */
export const INVENTORY_STATUS_CONFIG: Record<
  InventoryStatus,
  { label: string; color: string; bg: string; border: string }
> = {
  normal: { label: '正常', color: 'text-accent', bg: 'bg-accent/10', border: 'border-accent/30' },
  low: { label: '偏低', color: 'text-status-paused', bg: 'bg-status-paused/10', border: 'border-status-paused/30' },
  out: { label: '缺货', color: 'text-status-circuit', bg: 'bg-status-circuit/10', border: 'border-status-circuit/30' },
  overstock: { label: '积压', color: 'text-blue-400', bg: 'bg-blue-500/10', border: 'border-blue-500/30' },
}

/* ============ 凭证状态 ============ */
export const VOUCHER_STATUS_CONFIG: Record<
  VoucherStatus,
  { label: string; color: string; bg: string; border: string }
> = {
  pending: { label: '待审批', color: 'text-status-paused', bg: 'bg-status-paused/10', border: 'border-status-paused/30' },
  approved: { label: '已审批', color: 'text-accent', bg: 'bg-accent/10', border: 'border-accent/30' },
  rejected: { label: '已驳回', color: 'text-status-circuit', bg: 'bg-status-circuit/10', border: 'border-status-circuit/30' },
  posted: { label: '已过账', color: 'text-blue-400', bg: 'bg-blue-500/10', border: 'border-blue-500/30' },
}

/* ============ 审计结果 ============ */
export const AUDIT_RESULT_CONFIG: Record<
  string,
  { label: string; color: string; bg: string; border: string }
> = {
  success: { label: '成功', color: 'text-accent', bg: 'bg-accent/10', border: 'border-accent/30' },
  denied: { label: '拒绝', color: 'text-status-paused', bg: 'bg-status-paused/10', border: 'border-status-paused/30' },
  error: { label: '异常', color: 'text-status-circuit', bg: 'bg-status-circuit/10', border: 'border-status-circuit/30' },
}

/* ============ 风险等级 ============ */
export const RISK_LEVEL_CONFIG: Record<
  string,
  { label: string; color: string; bg: string; border: string }
> = {
  low: { label: '低风险', color: 'text-accent', bg: 'bg-accent/10', border: 'border-accent/30' },
  medium: { label: '中风险', color: 'text-status-paused', bg: 'bg-status-paused/10', border: 'border-status-paused/30' },
  high: { label: '高风险', color: 'text-status-circuit', bg: 'bg-status-circuit/10', border: 'border-status-circuit/30' },
}

/* ============ 格式化工具 ============ */
export function formatCurrency(amount: number): string {
  return `¥${amount.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}

export function formatNumber(num: number): string {
  return num.toLocaleString('zh-CN')
}

export function formatTime(iso: string): string {
  const d = new Date(iso)
  return d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false })
}

export function formatDateTime(iso: string): string {
  const d = new Date(iso)
  return d.toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  })
}

export function timeAgo(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime()
  const seconds = Math.floor(diff / 1000)
  if (seconds < 60) return `${seconds} 秒前`
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes} 分钟前`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours} 小时前`
  const days = Math.floor(hours / 24)
  return `${days} 天前`
}
