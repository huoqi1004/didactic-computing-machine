/**
 * ProtectedRoute 组件测试
 *
 * 覆盖未认证跳转、权限不足 403、正常渲染
 */
import { describe, it, expect, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import ProtectedRoute from './ProtectedRoute'
import { useAuthStore } from '../stores/authStore'

function renderWithRouter(
  ui: React.ReactNode,
  initialEntry = '/protected'
) {
  return render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <Routes>
        <Route path="/login" element={<div>登录页</div>} />
        <Route path="/protected" element={ui} />
      </Routes>
    </MemoryRouter>
  )
}

describe('ProtectedRoute', () => {
  beforeEach(() => {
    useAuthStore.setState({
      token: null,
      refreshToken: null,
      user: null,
      isAuthenticated: false,
      loading: false,
      error: null,
    })
  })

  it('redirects to login when not authenticated', () => {
    renderWithRouter(
      <ProtectedRoute>
        <div>受保护内容</div>
      </ProtectedRoute>
    )
    expect(screen.getByText('登录页')).toBeInTheDocument()
    expect(screen.queryByText('受保护内容')).not.toBeInTheDocument()
  })

  it('renders children when authenticated and no permission required', () => {
    useAuthStore.setState({
      token: 'tok',
      isAuthenticated: true,
      user: { user_id: 'u1', username: 'u', role_codes: ['employee'] },
    })

    renderWithRouter(
      <ProtectedRoute>
        <div>受保护内容</div>
      </ProtectedRoute>
    )
    expect(screen.getByText('受保护内容')).toBeInTheDocument()
  })

  it('renders children when authenticated with required permission', () => {
    useAuthStore.setState({
      token: 'tok',
      isAuthenticated: true,
      user: {
        user_id: 'u1',
        username: 'u',
        role_codes: ['employee'],
        permissions: ['admin:read'],
      },
    })

    renderWithRouter(
      <ProtectedRoute resource="admin" action="read">
        <div>管理内容</div>
      </ProtectedRoute>
    )
    expect(screen.getByText('管理内容')).toBeInTheDocument()
  })

  it('shows 403 when authenticated but lacks permission', () => {
    useAuthStore.setState({
      token: 'tok',
      isAuthenticated: true,
      user: {
        user_id: 'u1',
        username: 'u',
        role_codes: ['employee'],
        permissions: ['invoice:read'],
      },
    })

    renderWithRouter(
      <ProtectedRoute resource="admin" action="manage">
        <div>管理内容</div>
      </ProtectedRoute>
    )
    expect(screen.getByText('权限不足')).toBeInTheDocument()
    expect(screen.queryByText('管理内容')).not.toBeInTheDocument()
  })

  it('shows required permission in 403 message', () => {
    useAuthStore.setState({
      token: 'tok',
      isAuthenticated: true,
      user: {
        user_id: 'u1',
        username: 'u',
        role_codes: ['employee'],
        permissions: [],
      },
    })

    renderWithRouter(
      <ProtectedRoute resource="system" action="manage">
        <div>系统管理</div>
      </ProtectedRoute>
    )
    expect(screen.getByText(/system:manage/)).toBeInTheDocument()
  })
})
