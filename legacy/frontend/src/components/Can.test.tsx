/**
 * 权限系统测试
 *
 * 覆盖 authStore / usePermission / Can 组件
 */
import { describe, it, expect, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useAuthStore } from '../stores/authStore'
import { usePermission } from '../hooks/usePermission'
import Can from './Can'
import { renderHook, act } from '@testing-library/react'

/* ===================== authStore ===================== */

describe('authStore', () => {
  beforeEach(() => {
    // Reset store before each test
    useAuthStore.setState({
      token: null,
      refreshToken: null,
      user: null,
      isAuthenticated: false,
      loading: false,
      error: null,
    })
  })

  it('starts with default state', () => {
    const state = useAuthStore.getState()
    expect(state.token).toBeNull()
    expect(state.user).toBeNull()
    expect(state.isAuthenticated).toBe(false)
    expect(state.loading).toBe(false)
    expect(state.error).toBeNull()
  })

  it('setAuth sets token, user, and isAuthenticated', () => {
    act(() => {
      useAuthStore.getState().setAuth({
        access_token: 'test-token',
        refresh_token: 'refresh-token',
        user: {
          user_id: 'u1',
          username: 'testuser',
          role_codes: ['employee'],
          permissions: ['invoice:read'],
        },
      })
    })

    const state = useAuthStore.getState()
    expect(state.token).toBe('test-token')
    expect(state.refreshToken).toBe('refresh-token')
    expect(state.user?.username).toBe('testuser')
    expect(state.isAuthenticated).toBe(true)
    expect(state.loading).toBe(false)
    expect(state.error).toBeNull()
  })

  it('logout clears all auth state', () => {
    act(() => {
      useAuthStore.getState().setAuth({
        access_token: 'tok',
        user: { user_id: 'u1', username: 'u', role_codes: [] },
      })
      useAuthStore.getState().logout()
    })

    const state = useAuthStore.getState()
    expect(state.token).toBeNull()
    expect(state.user).toBeNull()
    expect(state.isAuthenticated).toBe(false)
  })

  it('updateUser merges partial user data', () => {
    act(() => {
      useAuthStore.getState().setAuth({
        access_token: 'tok',
        user: { user_id: 'u1', username: 'u', role_codes: [], email: 'old@test.com' },
      })
      useAuthStore.getState().updateUser({ email: 'new@test.com' })
    })

    expect(useAuthStore.getState().user?.email).toBe('new@test.com')
    expect(useAuthStore.getState().user?.username).toBe('u')
  })

  it('setLoading sets loading state', () => {
    act(() => useAuthStore.getState().setLoading(true))
    expect(useAuthStore.getState().loading).toBe(true)

    act(() => useAuthStore.getState().setLoading(false))
    expect(useAuthStore.getState().loading).toBe(false)
  })

  it('setError sets error state', () => {
    act(() => useAuthStore.getState().setError('登录失败'))
    expect(useAuthStore.getState().error).toBe('登录失败')

    act(() => useAuthStore.getState().setError(null))
    expect(useAuthStore.getState().error).toBeNull()
  })

  describe('hasRole', () => {
    it('returns true when user has the role', () => {
      act(() => {
        useAuthStore.getState().setAuth({
          access_token: 'tok',
          user: { user_id: 'u1', username: 'u', role_codes: ['admin', 'employee'] },
        })
      })
      expect(useAuthStore.getState().hasRole('admin')).toBe(true)
      expect(useAuthStore.getState().hasRole('employee')).toBe(true)
    })

    it('returns false when user does not have the role', () => {
      act(() => {
        useAuthStore.getState().setAuth({
          access_token: 'tok',
          user: { user_id: 'u1', username: 'u', role_codes: ['employee'] },
        })
      })
      expect(useAuthStore.getState().hasRole('admin')).toBe(false)
    })

    it('returns false when user is null', () => {
      expect(useAuthStore.getState().hasRole('admin')).toBe(false)
    })
  })

  describe('hasPermission', () => {
    it('returns true for exact permission match', () => {
      act(() => {
        useAuthStore.getState().setAuth({
          access_token: 'tok',
          user: {
            user_id: 'u1',
            username: 'u',
            role_codes: ['employee'],
            permissions: ['invoice:read', 'invoice:create'],
          },
        })
      })
      expect(useAuthStore.getState().hasPermission('invoice', 'read')).toBe(true)
      expect(useAuthStore.getState().hasPermission('invoice', 'create')).toBe(true)
    })

    it('returns false for missing permission', () => {
      act(() => {
        useAuthStore.getState().setAuth({
          access_token: 'tok',
          user: {
            user_id: 'u1',
            username: 'u',
            role_codes: ['employee'],
            permissions: ['invoice:read'],
          },
        })
      })
      expect(useAuthStore.getState().hasPermission('invoice', 'create')).toBe(false)
    })

    it('manage permission implies all actions', () => {
      act(() => {
        useAuthStore.getState().setAuth({
          access_token: 'tok',
          user: {
            user_id: 'u1',
            username: 'u',
            role_codes: ['employee'],
            permissions: ['system:manage'],
          },
        })
      })
      expect(useAuthStore.getState().hasPermission('system', 'read')).toBe(true)
      expect(useAuthStore.getState().hasPermission('system', 'create')).toBe(true)
      expect(useAuthStore.getState().hasPermission('system', 'delete')).toBe(true)
    })

    it('CIO role has all permissions (fallback)', () => {
      act(() => {
        useAuthStore.getState().setAuth({
          access_token: 'tok',
          user: {
            user_id: 'u1',
            username: 'cio',
            role_codes: ['cio'],
          },
        })
      })
      expect(useAuthStore.getState().hasPermission('anything', 'do')).toBe(true)
    })

    it('chairman role has all permissions (fallback)', () => {
      act(() => {
        useAuthStore.getState().setAuth({
          access_token: 'tok',
          user: {
            user_id: 'u1',
            username: 'boss',
            role_codes: ['chairman'],
          },
        })
      })
      expect(useAuthStore.getState().hasPermission('anything', 'do')).toBe(true)
    })

    it('returns false when user is null', () => {
      expect(useAuthStore.getState().hasPermission('invoice', 'read')).toBe(false)
    })

    it('returns false for empty permissions list without CIO/chairman role', () => {
      act(() => {
        useAuthStore.getState().setAuth({
          access_token: 'tok',
          user: {
            user_id: 'u1',
            username: 'u',
            role_codes: ['employee'],
          },
        })
      })
      expect(useAuthStore.getState().hasPermission('invoice', 'read')).toBe(false)
    })
  })
})

/* ===================== usePermission ===================== */

describe('usePermission', () => {
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

  it('returns can, canAll, canAny, hasRole, isAuthenticated', () => {
    const { result } = renderHook(() => usePermission())
    expect(result.current.can).toBeDefined()
    expect(result.current.canAll).toBeDefined()
    expect(result.current.canAny).toBeDefined()
    expect(result.current.hasRole).toBeDefined()
    expect(result.current.isAuthenticated).toBe(false)
  })

  describe('can', () => {
    it('returns true when user has permission', () => {
      act(() => {
        useAuthStore.getState().setAuth({
          access_token: 'tok',
          user: {
            user_id: 'u1',
            username: 'u',
            role_codes: ['employee'],
            permissions: ['invoice:read'],
          },
        })
      })
      const { result } = renderHook(() => usePermission())
      expect(result.current.can('invoice', 'read')).toBe(true)
    })

    it('returns false when user lacks permission', () => {
      act(() => {
        useAuthStore.getState().setAuth({
          access_token: 'tok',
          user: {
            user_id: 'u1',
            username: 'u',
            role_codes: ['employee'],
            permissions: ['invoice:read'],
          },
        })
      })
      const { result } = renderHook(() => usePermission())
      expect(result.current.can('invoice', 'delete')).toBe(false)
    })
  })

  describe('canAll', () => {
    it('returns true when user has all permissions', () => {
      act(() => {
        useAuthStore.getState().setAuth({
          access_token: 'tok',
          user: {
            user_id: 'u1',
            username: 'u',
            role_codes: ['employee'],
            permissions: ['invoice:read', 'invoice:create', 'invoice:export'],
          },
        })
      })
      const { result } = renderHook(() => usePermission())
      expect(result.current.canAll(['invoice:read', 'invoice:create'])).toBe(true)
    })

    it('returns false when user lacks one permission', () => {
      act(() => {
        useAuthStore.getState().setAuth({
          access_token: 'tok',
          user: {
            user_id: 'u1',
            username: 'u',
            role_codes: ['employee'],
            permissions: ['invoice:read'],
          },
        })
      })
      const { result } = renderHook(() => usePermission())
      expect(result.current.canAll(['invoice:read', 'invoice:create'])).toBe(false)
    })

    it('returns false for invalid permission format', () => {
      const { result } = renderHook(() => usePermission())
      expect(result.current.canAll(['invalidformat'])).toBe(false)
    })
  })

  describe('canAny', () => {
    it('returns true when user has at least one permission', () => {
      act(() => {
        useAuthStore.getState().setAuth({
          access_token: 'tok',
          user: {
            user_id: 'u1',
            username: 'u',
            role_codes: ['employee'],
            permissions: ['invoice:read'],
          },
        })
      })
      const { result } = renderHook(() => usePermission())
      expect(result.current.canAny(['invoice:read', 'invoice:create'])).toBe(true)
    })

    it('returns false when user has none of the permissions', () => {
      act(() => {
        useAuthStore.getState().setAuth({
          access_token: 'tok',
          user: {
            user_id: 'u1',
            username: 'u',
            role_codes: ['employee'],
            permissions: ['invoice:read'],
          },
        })
      })
      const { result } = renderHook(() => usePermission())
      expect(result.current.canAny(['invoice:create', 'invoice:delete'])).toBe(false)
    })
  })
})

/* ===================== Can 组件 ===================== */

describe('Can component', () => {
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

  it('renders children when permission is granted', () => {
    act(() => {
      useAuthStore.getState().setAuth({
        access_token: 'tok',
        user: {
          user_id: 'u1',
          username: 'u',
          role_codes: ['employee'],
          permissions: ['invoice:create'],
        },
      })
    })

    render(
      <Can resource="invoice" action="create">
        <button>开具发票</button>
      </Can>
    )
    expect(screen.getByText('开具发票')).toBeInTheDocument()
  })

  it('hides children when permission is denied', () => {
    act(() => {
      useAuthStore.getState().setAuth({
        access_token: 'tok',
        user: {
          user_id: 'u1',
          username: 'u',
          role_codes: ['employee'],
          permissions: ['invoice:read'],
        },
      })
    })

    render(
      <Can resource="invoice" action="create">
        <button>开具发票</button>
      </Can>
    )
    expect(screen.queryByText('开具发票')).not.toBeInTheDocument()
  })

  it('renders fallback when permission is denied', () => {
    act(() => {
      useAuthStore.getState().setAuth({
        access_token: 'tok',
        user: {
          user_id: 'u1',
          username: 'u',
          role_codes: ['employee'],
          permissions: ['invoice:read'],
        },
      })
    })

    render(
      <Can resource="invoice" action="create" fallback={<span>无权限</span>}>
        <button>开具发票</button>
      </Can>
    )
    expect(screen.getByText('无权限')).toBeInTheDocument()
    expect(screen.queryByText('开具发票')).not.toBeInTheDocument()
  })

  it('renders null fallback by default when permission denied', () => {
    act(() => {
      useAuthStore.getState().setAuth({
        access_token: 'tok',
        user: {
          user_id: 'u1',
          username: 'u',
          role_codes: ['employee'],
          permissions: ['invoice:read'],
        },
      })
    })

    const { container } = render(
      <Can resource="invoice" action="create">
        <button>开具发票</button>
      </Can>
    )
    expect(container.innerHTML).toBe('')
  })

  it('renders children when no permission specified (default allow)', () => {
    render(
      <Can>
        <span>总是显示</span>
      </Can>
    )
    expect(screen.getByText('总是显示')).toBeInTheDocument()
  })

  it('renders children when all permissions match (mode=all)', () => {
    act(() => {
      useAuthStore.getState().setAuth({
        access_token: 'tok',
        user: {
          user_id: 'u1',
          username: 'u',
          role_codes: ['employee'],
          permissions: ['invoice:read', 'invoice:create'],
        },
      })
    })

    render(
      <Can permissions={['invoice:read', 'invoice:create']} mode="all">
        <button>操作</button>
      </Can>
    )
    expect(screen.getByText('操作')).toBeInTheDocument()
  })

  it('hides children when not all permissions match (mode=all)', () => {
    act(() => {
      useAuthStore.getState().setAuth({
        access_token: 'tok',
        user: {
          user_id: 'u1',
          username: 'u',
          role_codes: ['employee'],
          permissions: ['invoice:read'],
        },
      })
    })

    render(
      <Can permissions={['invoice:read', 'invoice:create']} mode="all">
        <button>操作</button>
      </Can>
    )
    expect(screen.queryByText('操作')).not.toBeInTheDocument()
  })

  it('renders children when any permission matches (mode=any)', () => {
    act(() => {
      useAuthStore.getState().setAuth({
        access_token: 'tok',
        user: {
          user_id: 'u1',
          username: 'u',
          role_codes: ['employee'],
          permissions: ['invoice:read'],
        },
      })
    })

    render(
      <Can permissions={['invoice:read', 'invoice:create']} mode="any">
        <button>操作</button>
      </Can>
    )
    expect(screen.getByText('操作')).toBeInTheDocument()
  })

  it('hides children when no permission matches (mode=any)', () => {
    act(() => {
      useAuthStore.getState().setAuth({
        access_token: 'tok',
        user: {
          user_id: 'u1',
          username: 'u',
          role_codes: ['employee'],
          permissions: ['invoice:read'],
        },
      })
    })

    render(
      <Can permissions={['invoice:create', 'invoice:delete']} mode="any">
        <button>操作</button>
      </Can>
    )
    expect(screen.queryByText('操作')).not.toBeInTheDocument()
  })
})
