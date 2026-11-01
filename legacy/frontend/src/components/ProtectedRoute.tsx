import { type ReactNode } from 'react'
import { Navigate, useLocation } from 'react-router-dom'
import { useAuthStore } from '../stores/authStore'

interface ProtectedRouteProps {
  children: ReactNode
  /** 需要的资源权限，如 "admin" */
  resource?: string
  /** 需要的动作权限，如 "read" / "manage" */
  action?: string
}

/**
 * 路由守卫：未登录跳转登录页，权限不足显示 403 页面。
 */
export default function ProtectedRoute({
  children,
  resource,
  action,
}: ProtectedRouteProps) {
  const location = useLocation()
  const { isAuthenticated, hasPermission } = useAuthStore()

  // 未认证 → 跳转登录页，记住来源路径
  if (!isAuthenticated) {
    return (
      <Navigate
        to="/login"
        replace
        state={{ from: location.pathname }}
      />
    )
  }

  // 需要权限检查
  if (resource && action) {
    if (!hasPermission(resource, action)) {
      return (
        <div className="flex flex-col items-center justify-center min-h-[60vh] gap-4">
          <div className="w-16 h-16 rounded-full bg-status-failed/10 flex items-center justify-center">
            <svg
              className="w-8 h-8 text-status-failed"
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
              strokeWidth={2}
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                d="M18.364 18.364A9 9 0 005.636 5.636m12.728 12.728A9 9 0 015.636 5.636m12.728 12.728L5.636 5.636"
              />
            </svg>
          </div>
          <div className="text-center">
            <h2 className="text-lg font-semibold text-white">权限不足</h2>
            <p className="text-sm text-slate-500 mt-1">
              您没有访问此页面的权限（需要 {resource}:{action}）
            </p>
          </div>
        </div>
      )
    }
  }

  return <>{children}</>
}
