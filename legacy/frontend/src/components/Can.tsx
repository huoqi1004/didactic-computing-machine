/**
 * 权限守卫组件
 * ============
 *
 * 条件渲染子组件：仅当当前用户拥有指定权限时才渲染。
 *
 * 用法：
 *
 *   <Can resource="invoice" action="create">
 *     <Button>开具发票</Button>
 *   </Can>
 *
 *   <Can resource="reimbursement" action="approve">
 *     <Button>审批</Button>
 *   </Can>
 *
 *   // 需要全部权限
 *   <Can permissions={["invoice:read", "invoice:create"]}>
 *     <Button>开具</Button>
 *   </Can>
 *
 *   // fallback：权限不足时显示替代内容
 *   <Can resource="system" action="manage" fallback={<span className="text-slate-600">—</span>}>
 *     <Button>管理</Button>
 *   </Can>
 */
import type { ReactNode } from 'react'
import { usePermission } from '../hooks/usePermission'

interface CanProps {
  /** 子元素：权限通过时渲染 */
  children: ReactNode
  /** 单项权限检查：资源 */
  resource?: string
  /** 单项权限检查：动作 */
  action?: string
  /** 多项权限检查（与 resource/action 互斥） */
  permissions?: string[]
  /** 多项权限检查模式：all（默认，需全部通过）或 any（任一通过即可） */
  mode?: 'all' | 'any'
  /** 权限不足时的替代渲染内容 */
  fallback?: ReactNode | null
}

export default function Can({
  children,
  resource,
  action,
  permissions,
  mode = 'all',
  fallback = null,
}: CanProps) {
  const { can, canAll, canAny } = usePermission()

  let allowed: boolean

  if (permissions && permissions.length > 0) {
    allowed = mode === 'any' ? canAny(permissions) : canAll(permissions)
  } else if (resource && action) {
    allowed = can(resource, action)
  } else {
    // 未指定权限要求，默认允许
    allowed = true
  }

  if (!allowed) return <>{fallback}</>
  return <>{children}</>
}
