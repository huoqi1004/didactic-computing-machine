import { create } from 'zustand'
import { persist } from 'zustand/middleware'

/* ===================== 类型定义 ===================== */

export interface UserInfo {
  user_id: string
  username: string
  role_codes: string[]
  email?: string
  department?: string
  /** 后端返回的权限列表，格式为 ["resource:action", ...] */
  permissions?: string[]
}

interface AuthState {
  token: string | null
  refreshToken: string | null
  user: UserInfo | null
  isAuthenticated: boolean
  loading: boolean
  error: string | null

  // Actions
  setAuth: (data: {
    access_token: string
    refresh_token?: string
    user: UserInfo
  }) => void
  updateUser: (user: Partial<UserInfo>) => void
  logout: () => void
  setLoading: (loading: boolean) => void
  setError: (error: string | null) => void
  hasRole: (roleCode: string) => boolean
  hasPermission: (resource: string, action: string) => boolean
}

/* ===================== P1-6 凭证存储策略 =====================
 * access_token：仅内存（zustand state，不持久化）——页面刷新后由
 *               sessionStorage 中的 refresh_token 静默换新；
 * refresh_token：sessionStorage（标签页关闭即失效，XSS 窗口收窄）；
 * localStorage：只持久化 user/isAuthenticated（无凭证）。
 * 后端 /auth/refresh 已实现 refresh 轮换（每次刷新签发新 refresh_token）。
 * =========================================================== */
const REFRESH_STORAGE_KEY = 'erp-refresh-token'

export function saveRefreshToken(rt: string | null | undefined): void {
  if (rt) sessionStorage.setItem(REFRESH_STORAGE_KEY, rt)
  else sessionStorage.removeItem(REFRESH_STORAGE_KEY)
}

export function loadRefreshToken(): string | null {
  try {
    return sessionStorage.getItem(REFRESH_STORAGE_KEY)
  } catch {
    return null
  }
}

// 应用启动时恢复 refresh_token 到内存（access_token 由 api.ts 静默刷新重建）
const _bootRefresh = loadRefreshToken()
if (_bootRefresh) {
  // 延迟到 store 创建后：先建 store，再把 refresh_token 放入内存态
  queueMicrotask(() => useAuthStore.setState({ refreshToken: _bootRefresh }))
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set, get) => ({
      token: null,
      refreshToken: null,
      user: null,
      isAuthenticated: false,
      loading: false,
      error: null,

      setAuth: (data) => {
        saveRefreshToken(data.refresh_token)
        set({
          token: data.access_token,
          refreshToken: data.refresh_token || null,
          user: data.user,
          isAuthenticated: true,
          loading: false,
          error: null,
        })
      },

      updateUser: (user) => {
        const current = get().user
        if (current) {
          set({ user: { ...current, ...user } })
        }
      },

      logout: () => {
        saveRefreshToken(null)
        set({
          token: null,
          refreshToken: null,
          user: null,
          isAuthenticated: false,
          loading: false,
          error: null,
        })
      },

      setLoading: (loading) => set({ loading }),
      setError: (error) => set({ error }),

      hasRole: (roleCode) => {
        const user = get().user
        if (!user) return false
        return user.role_codes.includes(roleCode)
      },

      hasPermission: (resource, action) => {
        const user = get().user
        if (!user) return false

        // 优先使用后端返回的 permissions 列表
        const perms = user.permissions
        if (perms && perms.length > 0) {
          // 精确匹配
          if (perms.includes(`${resource}:${action}`)) return true
          // manage 通配：拥有 resource:manage 则隐含全部动作
          if (action !== 'manage' && perms.includes(`${resource}:manage`)) return true
          return false
        }

        // Fallback：CIO 和董事长拥有全部权限
        if (user.role_codes.includes('cio') || user.role_codes.includes('chairman')) {
          return true
        }

        return false
      },
    }),
    {
      name: 'erp-auth',
      // P1-6：只持久化用户信息——任何凭证不落 localStorage
      partialize: (state) => ({
        user: state.user,
        isAuthenticated: state.isAuthenticated,
      }),
    },
  ),
)
