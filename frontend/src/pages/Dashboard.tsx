/** 看板页（FR-B1~B3：分管领导看板 + 董事长全域驾驶舱）。 */
import { useEffect, useState } from 'react'
import api from '../lib/api'
import { useAppStore } from '../store'

interface Stats { [key: string]: number }

export default function Dashboard() {
  const user = useAppStore(s => s.user)
  const [data, setData] = useState<Stats>({})
  const [error, setError] = useState('')

  useEffect(() => {
    const path = user?.role === 'chairman' ? '/dashboard/chairman' : '/dashboard/leader'
    api.get(path).then(r => setData(r.overview || r.stats || {})).catch(e => setError(e?.response?.data?.detail || '加载失败'))
  }, [user?.role])

  return (
    <div className="min-h-screen bg-canvas p-6">
      <h1 className="text-lg font-bold text-ink mb-4">
        {user?.role === 'chairman' ? '全域驾驶舱' : '分管看板'}
      </h1>
      {error && <p className="text-alert text-sm">{error}</p>}
      <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
        {Object.entries(data).map(([k, v]) => (
          <div key={k} className="bg-card border border-border rounded-lg p-4">
            <p className="text-xs text-slate2">{k}</p>
            <p className="text-2xl font-bold text-navy">{typeof v === 'number' ? v.toLocaleString() : String(v)}</p>
          </div>
        ))}
      </div>
    </div>
  )
}
