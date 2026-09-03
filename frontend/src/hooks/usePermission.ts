/**
 * 按钮级权限控制 Hook
 * ==================
 *
 * 封装 authStore 的 hasPermission，提供便捷的按钮级权限检查。
 *
 * 用法：
 *
 *   // 方式1：直接检查
 *   const { can } = usePermission()
 *   {can('invoice', 'create') && <Button>开具发票</Button>}
 *
 *   // 方式2：批量检查
 *   const { canAll, canAny } = usePermission()
 *   {canAll(['invoice:read', 'invoice:create']) && <Button>开具</Button>}
 *
 *   // 方式3：与 Can 组件配合
 *   <Can resource="invoice" action="create">
 *     <Button>开具发票</Button>
 *   </Can>
 */
import { useAuthStore } from '../stores/authStore'

export interface UsePermissionReturn {
  /** 检查是否拥有 resource:action 权限 */
  can: (resource: string, action: string) => boolean
  /** 检查是否拥有全部权限 */
  canAll: (permissions: string[]) => boolean
  /** 检查是否拥有任意一项权限 */
  canAny: (permissions: string[]) => boolean
  /** 检查是否拥有指定角色 */
  hasRole: (roleCode: string) => boolean
  /** 当前用户是否已认证 */
  isAuthenticated: boolean
}

/**
 * 解析 "resource:action" 字符串
 */
function parsePermission(perm: string): [string, string] | null {
  const parts = perm.split(':')
  if (parts.length !== 2) return null
  return [parts[0], parts[1]]
}

export function usePermission(): UsePermissionReturn {
  const hasPermission = useAuthStore((s) => s.hasPermission)
  const hasRole = useAuthStore((s) => s.hasRole)
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated)

  const can = (resource: string, action: string): boolean => {
    return hasPermission(resource, action)
  }

  const canAll = (permissions: string[]): boolean => {
    return permissions.every((perm) => {
      const parsed = parsePermission(perm)
      if (!parsed) return false
      return hasPermission(parsed[0], parsed[1])
    })
  }

  const canAny = (permissions: string[]): boolean => {
    return permissions.some((perm) => {
      const parsed = parsePermission(perm)
      if (!parsed) return false
      return hasPermission(parsed[0], parsed[1])
    })
  }

  return {
    can,
    canAll,
    canAny,
    hasRole,
    isAuthenticated,
  }
}
