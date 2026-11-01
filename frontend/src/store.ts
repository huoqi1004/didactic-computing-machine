import { create } from 'zustand'

export interface User { username: string; display_name: string; role: string; department: string; role_label: string }

interface AppState {
  user: User | null
  setUser: (u: User) => void
  clearUser: () => void
  convVersion: number          // 会话列表刷新信号
  bumpConv: () => void
  quota: { used: number; limit: number; remaining: number; enabled: boolean } | null
  setQuota: (q: AppState['quota']) => void
}

const savedUser = sessionStorage.getItem('erp-user')

export const useAppStore = create<AppState>(set => ({
  user: savedUser ? JSON.parse(savedUser) : null,
  setUser: u => {
    sessionStorage.setItem('erp-user', JSON.stringify(u))
    set({ user: u })
  },
  clearUser: () => {
    sessionStorage.removeItem('erp-user')
    set({ user: null })
  },
  convVersion: 0,
  bumpConv: () => set(s => ({ convVersion: s.convVersion + 1 })),
  quota: null,
  setQuota: q => set({ quota: q }),
}))

export const ROLE_LABELS: Record<string, string> = {
  sys_admin: '系统管理员', chairman: '董事长', leader: '主管领导',
  dept_admin: '部门管理员', employee: '普通员工',
}
