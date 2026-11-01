/**
 * 看板页（FR-B1~B3）：多图表实时数据。
 * 分管领导：运行指标卡 + 收支/盈亏折线 + 支出构成饼图 + 项目预算柱状 + 项目进度条
 * 董事长：全域指标 + 上述全部 + 部门预算执行 + 部门人数分布 + 会话活跃趋势
 */
import { useEffect, useState } from 'react'
import {
  Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, Legend, Line, LineChart,
  Pie, PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts'
import { Building2, Coins, FolderKanban, TrendingDown, TrendingUp, Users } from 'lucide-react'
import api from '../lib/api'
import { useAppStore } from '../store'

const CN: Record<string, string> = {
  users: '活跃用户', documents: '定稿文档', knowledge: '知识条目',
  todos_open: '未办待办', im_channels: 'IM 频道', conversations: '智能体会话',
  audit_recent: '本周审计事件', total_documents: '全部资料', audit_entries: '审计链总量',
  token_today: '今日 token 消耗',
}
const C1 = '#1d4ed8'; const C2 = '#0ea5e9'; const C3 = '#0891b2'
const PIE_COLORS = ['#1d4ed8', '#0ea5e9', '#0891b2', '#7dd3fc', '#2563eb', '#94a3b8']
const STATUS_CN: Record<string, string> = {
  planning: '规划中', running: '进行中', risk: '风险', done: '已完成', paused: '暂停',
}

function ChartCard({ title, children, className = '' }: any) {
  return (
    <div className={`bg-card rounded-2xl border border-border shadow-soft p-4 ${className}`}>
      <p className="text-sm font-semibold text-ink mb-3">{title}</p>
      {children}
    </div>
  )
}

const tooltipStyle = { borderRadius: 10, border: '1px solid #d9e4f5', fontSize: 12 }

export default function Dashboard() {
  const user = useAppStore(s => s.user)
  const isChairman = user?.role === 'chairman'
  const [data, setData] = useState<any>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    const path = isChairman ? '/dashboard/chairman' : '/dashboard/leader'
    api.get(path).then(setData)
      .catch(e => setError(e?.response?.data?.detail || '加载失败'))
  }, [isChairman])

  if (error) return <div className="p-6 text-alert text-sm">{error}</div>
  if (!data) return <div className="p-6 text-sm text-slate2">加载中…</div>

  const stats = data.overview || data.stats || {}
  const finance = data.finance || []
  const mix = data.expense_mix || []
  const projects = data.projects || []
  const deptFin = data.dept_finance || []

  return (
    <div className="h-full overflow-y-auto p-6 space-y-5">
      <h1 className="font-display text-lg font-bold text-ink">
        {isChairman ? '全域驾驶舱' : `分管看板 · ${data.my_department || ''}`}
      </h1>

      {/* 指标卡 */}
      <div className="grid grid-cols-2 md:grid-cols-4 xl:grid-cols-7 gap-3">
        {Object.entries(stats).map(([k, v]) => (
          <div key={k} className="card-lift bg-card rounded-xl border border-border shadow-soft p-3.5">
            <p className="text-[11px] text-slate2">{CN[k] || k}</p>
            <p className="font-display text-xl font-bold text-navy mt-0.5">
              {Number(v).toLocaleString()}
            </p>
          </div>
        ))}
      </div>

      {/* 收支与盈亏 + 支出构成 */}
      <div className="grid grid-cols-1 xl:grid-cols-3 gap-4">
        <ChartCard title="收支与盈亏趋势（近 6 个月 · 万元）" className="xl:col-span-2">
          <ResponsiveContainer width="100%" height={260}>
            <LineChart data={finance} margin={{ top: 4, right: 12, left: -12, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#e3ecf9" />
              <XAxis dataKey="month" tick={{ fontSize: 11, fill: '#5a6b85' }} />
              <YAxis tick={{ fontSize: 11, fill: '#5a6b85' }} />
              <Tooltip contentStyle={tooltipStyle} />
              <Legend wrapperStyle={{ fontSize: 12 }} />
              <Line type="monotone" dataKey="income" name="收入" stroke={C1} strokeWidth={2.5} dot={{ r: 3 }} />
              <Line type="monotone" dataKey="expense" name="支出" stroke={C3} strokeWidth={2.5} dot={{ r: 3 }} />
              <Line type="monotone" dataKey="profit" name="盈亏" stroke={C2} strokeWidth={2.5}
                    strokeDasharray="6 3" dot={{ r: 3 }} />
            </LineChart>
          </ResponsiveContainer>
        </ChartCard>
        <ChartCard title="支出构成（万元）">
          <ResponsiveContainer width="100%" height={260}>
            <PieChart>
              <Pie data={mix} dataKey="amount" nameKey="category" innerRadius={55}
                   outerRadius={92} paddingAngle={3}>
                {mix.map((_: any, i: number) =>
                  <Cell key={i} fill={PIE_COLORS[i % PIE_COLORS.length]} />)}
              </Pie>
              <Tooltip contentStyle={tooltipStyle} />
              <Legend wrapperStyle={{ fontSize: 12 }} />
            </PieChart>
          </ResponsiveContainer>
        </ChartCard>
      </div>

      {/* 项目预算 vs 实际 + 部门预算执行 */}
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
        <ChartCard title="项目预算执行（预算 vs 已花 · 万元）">
          <ResponsiveContainer width="100%" height={280}>
            <BarChart data={projects.slice(0, 8)} margin={{ top: 4, right: 12, left: -12, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#e3ecf9" />
              <XAxis dataKey="name" tick={{ fontSize: 10, fill: '#5a6b85' }} interval={0}
                     angle={-18} textAnchor="end" height={56} />
              <YAxis tick={{ fontSize: 11, fill: '#5a6b85' }} />
              <Tooltip contentStyle={tooltipStyle} />
              <Legend wrapperStyle={{ fontSize: 12 }} />
              <Bar dataKey="budget" name="预算" fill={C2} radius={[4, 4, 0, 0]} />
              <Bar dataKey="spent" name="已投入" fill={C1} radius={[4, 4, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </ChartCard>
        <ChartCard title="部门预算执行汇总（万元）">
          <ResponsiveContainer width="100%" height={280}>
            <BarChart data={deptFin} layout="vertical" margin={{ top: 4, right: 16, left: 8, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#e3ecf9" />
              <XAxis type="number" tick={{ fontSize: 11, fill: '#5a6b85' }} />
              <YAxis type="category" dataKey="department" width={78}
                     tick={{ fontSize: 11, fill: '#5a6b85' }} />
              <Tooltip contentStyle={tooltipStyle} />
              <Legend wrapperStyle={{ fontSize: 12 }} />
              <Bar dataKey="budget" name="预算" fill={C2} radius={[0, 4, 4, 0]} />
              <Bar dataKey="spent" name="已投入" fill={C1} radius={[0, 4, 4, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </ChartCard>
      </div>

      {/* 项目现状进度条 */}
      <ChartCard title="项目现状与进度">
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          {projects.map((p: any) => {
            const overBudget = p.spent > p.budget
            return (
              <div key={p.name} className="border border-border rounded-xl p-3.5">
                <div className="flex items-center justify-between mb-1.5">
                  <p className="text-sm font-medium text-ink flex items-center gap-1.5">
                    <FolderKanban size={14} className="text-navy" /> {p.name}
                  </p>
                  <span className={`text-[11px] rounded px-1.5 py-0.5 ${
                    p.status === 'risk' ? 'bg-red-50 text-alert'
                    : p.status === 'done' ? 'bg-emerald-50 text-ok'
                    : p.status === 'paused' ? 'bg-amber-50 text-warn'
                    : 'bg-blue-50 text-navy'}`}>
                    {STATUS_CN[p.status] || p.status}
                  </span>
                </div>
                <p className="text-[11px] text-slate2 mb-2">
                  {p.department} · 负责人 {p.owner || '-'} ·
                  预算 {p.budget} 万 / 已投入 {p.spent} 万
                  {overBudget && <span className="text-alert font-medium">（超支）</span>}
                </p>
                <div className="h-2 rounded-full bg-canvas overflow-hidden">
                  <div className={`h-full rounded-full ${overBudget ? 'bg-alert' : 'bg-gradient-brand'}`}
                       style={{ width: `${Math.min(p.progress, 100)}%` }} />
                </div>
                <p className="text-[10px] text-slate2 mt-1 text-right">{p.progress}%</p>
              </div>
            )
          })}
        </div>
      </ChartCard>

      {/* 董事长专属：部门人数 + 会话活跃 */}
      {isChairman && (
        <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
          <ChartCard title="部门人员分布">
            <ResponsiveContainer width="100%" height={240}>
              <PieChart>
                <Pie data={data.dept_users || []} dataKey="count" nameKey="department"
                     outerRadius={92} paddingAngle={3}>
                  {(data.dept_users || []).map((_: any, i: number) =>
                    <Cell key={i} fill={PIE_COLORS[i % PIE_COLORS.length]} />)}
                </Pie>
                <Tooltip contentStyle={tooltipStyle} />
                <Legend wrapperStyle={{ fontSize: 12 }} />
              </PieChart>
            </ResponsiveContainer>
          </ChartCard>
          <ChartCard title="智能体会话活跃（近 7 日）">
            <ResponsiveContainer width="100%" height={240}>
              <AreaChart data={data.conv_trend || []} margin={{ top: 4, right: 12, left: -18, bottom: 0 }}>
                <defs>
                  <linearGradient id="convGrad" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor={C2} stopOpacity={0.45} />
                    <stop offset="100%" stopColor={C2} stopOpacity={0.03} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="#e3ecf9" />
                <XAxis dataKey="day" tick={{ fontSize: 11, fill: '#5a6b85' }} />
                <YAxis allowDecimals={false} tick={{ fontSize: 11, fill: '#5a6b85' }} />
                <Tooltip contentStyle={tooltipStyle} />
                <Area type="monotone" dataKey="count" name="会话数" stroke={C2}
                      strokeWidth={2.5} fill="url(#convGrad)" />
              </AreaChart>
            </ResponsiveContainer>
          </ChartCard>
        </div>
      )}

      {/* 战略建议 */}
      {isChairman && data.strategy_suggestions && (
        <ChartCard title="战略建议">
          <div className="space-y-2">
            {data.strategy_suggestions.map((s: any) => (
              <p key={s.area} className="text-sm text-ink">
                <span className="inline-block bg-blue-50 text-navy rounded px-1.5 py-0.5 text-xs mr-2">{s.area}</span>
                {s.suggestion}
              </p>
            ))}
          </div>
        </ChartCard>
      )}
    </div>
  )
}
