/**
 * 登录页（商务办公风格：简洁/深蓝/白卡）
 */
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import api, { setToken } from '../lib/api'
import { useAppStore } from '../store'

export default function Login() {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const nav = useNavigate()
  const setUser = useAppStore(s => s.setUser)

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setLoading(true); setError('')
    try {
      const r = await api.post('/auth/login', { username, password })
      setToken(r.access_token)
      setUser(r.user)
      nav('/', { replace: true })
    } catch (err: any) {
      setError(err?.response?.data?.detail || '登录失败')
    } finally { setLoading(false) }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-canvas">
      <form onSubmit={submit} className="w-full max-w-sm bg-card rounded-lg shadow-md border border-border p-8 space-y-5">
        <div className="text-center space-y-1">
          <h1 className="text-xl font-bold text-navy">企业数字化大脑</h1>
          <p className="text-sm text-slate2">Agent-Native ERP · 新架构</p>
        </div>
        <input value={username} onChange={e => setUsername(e.target.value)}
               placeholder="用户名" className="w-full border border-border rounded px-3 py-2 text-sm" />
        <input type="password" value={password} onChange={e => setPassword(e.target.value)}
               placeholder="密码" className="w-full border border-border rounded px-3 py-2 text-sm" />
        {error && <p className="text-alert text-xs">{error}</p>}
        <button disabled={loading}
                className="w-full bg-navy text-white rounded py-2 text-sm font-medium hover:bg-navy-dark disabled:opacity-50">
          {loading ? '登录中…' : '登 录'}
        </button>
      </form>
    </div>
  )
}
