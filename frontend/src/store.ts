import { create } from 'zustand'

export interface User { username: string; display_name: string; role: string; department: string; role_label: string }

interface AppState {
  user: User | null
  setUser: (u: User) => void
  clearUser: () => void
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
}))

export const ROLE_LABELS: Record<string, string> = {
  sys_admin: '系统管理员', chairman: '董事长', leader: '主管领导',
  dept_admin: '部门管理员', employee: '普通员工',
}
