import { apiClient } from './api'
import type { UserInfo } from '../stores/authStore'

export interface LoginRequest {
  username: string
  password: string
}

export interface LoginResponse {
  access_token: string
  refresh_token?: string
  token_type: string
  expires_in: number
  user: UserInfo
}

export interface RefreshResponse {
  access_token: string
  token_type: string
  expires_in: number
}

export const authService = {
  async login(data: LoginRequest): Promise<LoginResponse> {
    const resp = await apiClient.post<LoginResponse>('/auth/login', data)
    return resp as unknown as LoginResponse
  },

  async logout(): Promise<void> {
    try {
      await apiClient.post('/auth/logout')
    } catch {
      // 忽略错误，前端仍然清除状态
    }
  },

  async refresh(): Promise<RefreshResponse> {
    const resp = await apiClient.post<RefreshResponse>('/auth/refresh')
    return resp as unknown as RefreshResponse
  },

  async getProfile(): Promise<UserInfo> {
    const resp = await apiClient.get<UserInfo>('/auth/me')
    return resp as unknown as UserInfo
  },
}
