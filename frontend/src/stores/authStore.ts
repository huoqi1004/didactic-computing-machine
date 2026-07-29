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

/* ===================== Store ===================== */

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
      // 只持久化 token 和 user
      partialize: (state) => ({
        token: state.token,
        refreshToken: state.refreshToken,
        user: state.user,
        isAuthenticated: state.isAuthenticated,
      }),
    },
  ),
)
