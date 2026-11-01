import { useState, useEffect } from 'react'
import { useNavigate, useLocation } from 'react-router-dom'
import { Cpu, Eye, EyeOff, AlertCircle, Loader2 } from 'lucide-react'
import { useAuthStore } from '../stores/authStore'
import { authService } from '../services/authService'

export default function Login() {
  const navigate = useNavigate()
  const location = useLocation()
  const { setAuth, setLoading, setError, loading, error, isAuthenticated } = useAuthStore()

  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [showPassword, setShowPassword] = useState(false)

  const from = (location.state as { from?: string })?.from || '/'

  useEffect(() => {
    if (isAuthenticated) {
      navigate(from, { replace: true })
    }
  }, [isAuthenticated, navigate, from])

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!username || !password) return

    setLoading(true)
    setError(null)

    try {
      const data = await authService.login({ username, password })
      setAuth({
        access_token: data.access_token,
        refresh_token: data.refresh_token,
        user: data.user,
      })
      navigate(from, { replace: true })
    } catch (err: any) {
      const msg =
        err?.response?.data?.detail ||
        err?.response?.data?.message ||
        '登录失败，请检查用户名和密码'
      setError(msg)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-bg grid-bg p-4">
      {/* 背景装饰 */}
      <div className="absolute inset-0 overflow-hidden pointer-events-none">
        <div className="absolute top-1/4 left-1/4 w-96 h-96 bg-primary/10 rounded-full blur-[120px]" />
        <div className="absolute bottom-1/4 right-1/4 w-96 h-96 bg-accent/10 rounded-full blur-[120px]" />
      </div>

      <div className="relative w-full max-w-md">
        {/* Logo */}
        <div className="flex flex-col items-center mb-8">
          <div className="w-16 h-16 rounded-2xl bg-gradient-primary flex items-center justify-center shadow-glow mb-4">
            <Cpu className="w-9 h-9 text-white" />
          </div>
          <h1 className="text-2xl font-bold text-white">企业数字化大脑</h1>
          <p className="text-sm text-slate-500 mt-1">Agent-Native ERP System</p>
        </div>

        {/* 登录卡片 */}
        <div className="bg-bg-card/80 backdrop-blur-xl rounded-2xl border border-[#1e2536] p-8 shadow-2xl">
          <h2 className="text-lg font-semibold text-white mb-6">登录</h2>

          {/* 错误提示 */}
          {error && (
            <div className="mb-4 flex items-start gap-2 px-4 py-3 rounded-lg bg-status-failed/10 border border-status-failed/30">
              <AlertCircle className="w-4 h-4 text-status-failed shrink-0 mt-0.5" />
              <span className="text-sm text-status-failed">{error}</span>
            </div>
          )}

          <form onSubmit={handleSubmit} className="space-y-5">
            {/* 用户名 */}
            <div>
              <label className="block text-xs font-medium text-slate-400 mb-1.5">
                用户名
              </label>
              <input
                type="text"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                autoFocus
                autoComplete="username"
                placeholder="请输入用户名"
                className="w-full px-4 py-2.5 rounded-lg bg-bg-input border border-[#2a3142] text-white text-sm placeholder:text-slate-600 focus:outline-none focus:border-primary/50 focus:ring-1 focus:ring-primary/30 transition-colors"
              />
            </div>

            {/* 密码 */}
            <div>
              <label className="block text-xs font-medium text-slate-400 mb-1.5">
                密码
              </label>
              <div className="relative">
                <input
                  type={showPassword ? 'text' : 'password'}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  autoComplete="current-password"
                  placeholder="请输入密码"
                  className="w-full px-4 py-2.5 pr-11 rounded-lg bg-bg-input border border-[#2a3142] text-white text-sm placeholder:text-slate-600 focus:outline-none focus:border-primary/50 focus:ring-1 focus:ring-primary/30 transition-colors"
                />
                <button
                  type="button"
                  onClick={() => setShowPassword(!showPassword)}
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-500 hover:text-slate-300"
                >
                  {showPassword ? (
                    <EyeOff className="w-4 h-4" />
                  ) : (
                    <Eye className="w-4 h-4" />
                  )}
                </button>
              </div>
            </div>

            {/* 登录按钮 */}
            <button
              type="submit"
              disabled={loading || !username || !password}
              className="w-full py-2.5 rounded-lg bg-gradient-primary text-white text-sm font-semibold shadow-glow hover:opacity-90 disabled:opacity-50 disabled:cursor-not-allowed transition-opacity flex items-center justify-center gap-2"
            >
              {loading ? (
                <>
                  <Loader2 className="w-4 h-4 animate-spin" />
                  登录中...
                </>
              ) : (
                '登录'
              )}
            </button>
          </form>

          {/* 提示信息 */}
          <div className="mt-6 pt-5 border-t border-[#1e2536]">
            <p className="text-xs text-slate-600 text-center">
              默认管理员: admin / admin123
            </p>
          </div>
        </div>

        <p className="text-center text-xs text-slate-700 mt-6">
          (c) 2026 企业数字化大脑 - All Rights Reserved
        </p>
      </div>
    </div>
  )
}
