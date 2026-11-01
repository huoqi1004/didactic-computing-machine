import { create } from 'zustand'

export interface User { username: string; display_name: string; role: string; department: string; role_label: string }

interface AppState {
  user: User | null
  setUser: (u: User) => void
  clearUser: () => void
}

export const useAppStore = create<AppState>(set => ({
  user: null,
  setUser: u => set({ user: u }),
  clearUser: () => set({ user: null }),
}))

export const ROLE_LABELS: Record<string, string> = {
  sys_admin: '系统管理员', chairman: '董事长', leader: '主管领导',
  dept_admin: '部门管理员', employee: '普通员工',
}
