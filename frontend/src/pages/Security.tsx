import { useState } from 'react'
import {
  ShieldCheck,
  ShieldAlert,
  Fingerprint,
  KeyRound,
  ScrollText,
  Lock,
  Unlock,
  CheckCircle2,
  XCircle,
  AlertTriangle,
  Ban,
  Search,
  Clock,
} from 'lucide-react'
import { useSystemStore } from '../stores/systemStore'
import {
  AUDIT_RESULT_CONFIG,
  RISK_LEVEL_CONFIG,
  formatDateTime,
  timeAgo,
  cn,
} from '../lib/utils'

export default function Security() {
  const identities = useSystemStore((s) => s.agentIdentities)
  const auditLogs = useSystemStore((s) => s.auditLogs)
  const securityStats = useSystemStore((s) => s.securityStats)
  const [search, setSearch] = useState('')
  const [resultFilter, setResultFilter] = useState<string>('all')

  const filteredLogs = auditLogs.filter((log) => {
    const matchSearch =
      log.agent_name.includes(search) ||
      log.action.toLowerCase().includes(search.toLowerCase()) ||
      log.resource.toLowerCase().includes(search.toLowerCase()) ||
      log.details.includes(search)
    const matchResult = resultFilter === 'all' || log.result === resultFilter
    return matchSearch && matchResult
  })

  return (
    <div className="space-y-6 animate-fade-in">
      {/* 页面标题 */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-xl font-bold text-white">安全审计</h2>
          <p className="text-sm text-slate-500 mt-0.5">Agent 身份管理、权限审计与安全监控</p>
        </div>
        <div className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-bg-card border border-[#2a3142]">
          {securityStats && securityStats.last_breach === null ? (
            <>
              <ShieldCheck className="w-4 h-4 text-accent" />
              <span className="text-xs text-slate-300">系统安全 · 无入侵记录</span>
            </>
          ) : (
            <>
              <ShieldAlert className="w-4 h-4 text-status-circuit" />
              <span className="text-xs text-status-circuit">检测到安全事件</span>
            </>
          )}
        </div>
      </div>

      {/* 安全统计 */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <SecurityStat
          title="身份总数"
          value={securityStats?.total_identities ?? 0}
          icon={Fingerprint}
          color="primary"
        />
        <SecurityStat
          title="已验证身份"
          value={securityStats?.verified_identities ?? 0}
          icon={CheckCircle2}
          color="accent"
        />
        <SecurityStat
          title="今日拒绝操作"
          value={securityStats?.denied_actions_today ?? 0}
          icon={Ban}
          color="circuit"
        />
        <SecurityStat
          title="高风险 Agent"
          value={securityStats?.high_risk_agents ?? 0}
          icon={AlertTriangle}
          color="paused"
        />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        {/* Agent 身份列表 */}
        <div className="card lg:col-span-1">
          <div className="flex items-center gap-2 mb-4">
            <div className="w-8 h-8 rounded-lg bg-primary/10 flex items-center justify-center">
              <KeyRound className="w-4 h-4 text-primary" />
            </div>
            <div>
              <h3 className="text-sm font-semibold text-white">Agent 身份</h3>
              <p className="text-xs text-slate-500">令牌与权限管理</p>
            </div>
          </div>
          <div className="space-y-2.5 max-h-[600px] overflow-y-auto pr-1">
            {identities.map((identity) => {
              const riskCfg = RISK_LEVEL_CONFIG[identity.risk_level] ?? RISK_LEVEL_CONFIG.low
              const isVerified = identity.auth_status === 'verified'
              const isExpired = identity.auth_status === 'expired'
              return (
                <div
                  key={identity.agent_id}
                  className="p-3 rounded-lg bg-bg-input border border-[#1e2536] hover:border-primary/30 transition-colors"
                >
                  <div className="flex items-center justify-between gap-2 mb-2">
                    <div className="flex items-center gap-2 min-w-0">
                      <div
                        className={cn(
                          'w-7 h-7 rounded-md flex items-center justify-center shrink-0',
                          isVerified ? 'bg-accent/10' : 'bg-status-paused/10',
                        )}
                      >
                        {isVerified ? (
                          <Lock className="w-3.5 h-3.5 text-accent" />
                        ) : (
                          <Unlock className="w-3.5 h-3.5 text-status-paused" />
                        )}
                      </div>
                      <span className="text-sm font-medium text-white truncate">{identity.agent_name}</span>
                    </div>
                    <span className={cn('badge py-0 text-[10px]', riskCfg.bg, riskCfg.color, riskCfg.border)}>
                      {riskCfg.label}
                    </span>
                  </div>

                  {/* 令牌 */}
                  <div className="flex items-center gap-1.5 mb-2 text-[11px] text-slate-500 font-mono">
                    <Fingerprint className="w-3 h-3" />
                    <span className="truncate">{identity.identity_token}</span>
                  </div>

                  {/* 权限标签 */}
                  <div className="flex flex-wrap gap-1 mb-2">
                    {identity.permissions.slice(0, 3).map((perm) => (
                      <span
                        key={perm}
                        className="px-1.5 py-0.5 rounded text-[10px] bg-bg-deep text-slate-400 border border-[#2a3142]"
                      >
                        {perm}
                      </span>
                    ))}
                    {identity.permissions.length > 3 && (
                      <span className="px-1.5 py-0.5 rounded text-[10px] bg-bg-deep text-slate-500">
                        +{identity.permissions.length - 3}
                      </span>
                    )}
                  </div>

                  {/* 认证状态 + 时间 */}
                  <div className="flex items-center justify-between text-[11px]">
                    <span
                      className={cn(
                        'flex items-center gap-1',
                        isVerified ? 'text-accent' : isExpired ? 'text-status-paused' : 'text-status-circuit',
                      )}
                    >
                      {isVerified ? <CheckCircle2 className="w-3 h-3" /> : <XCircle className="w-3 h-3" />}
                      {isVerified ? '已验证' : isExpired ? '已过期' : '已撤销'}
                    </span>
                    <span className="flex items-center gap-1 text-slate-500">
                      <Clock className="w-3 h-3" />
                      {timeAgo(identity.last_auth)}
                    </span>
                  </div>
                </div>
              )
            })}
          </div>
        </div>

        {/* 审计日志 */}
        <div className="card lg:col-span-2">
          <div className="flex flex-wrap items-center justify-between gap-3 mb-4">
            <div className="flex items-center gap-2">
              <div className="w-8 h-8 rounded-lg bg-status-circuit/10 flex items-center justify-center">
                <ScrollText className="w-4 h-4 text-status-circuit" />
              </div>
              <div>
                <h3 className="text-sm font-semibold text-white">审计日志</h3>
                <p className="text-xs text-slate-500">Agent 操作留痕与异常溯源</p>
              </div>
            </div>
            <div className="flex items-center gap-2 flex-wrap">
              {/* 结果筛选 */}
              <div className="flex items-center gap-1">
                {(['all', 'success', 'denied', 'error'] as const).map((r) => (
                  <button
                    key={r}
                    onClick={() => setResultFilter(r)}
                    className={cn(
                      'px-2.5 py-1 rounded-md text-xs font-medium transition-all',
                      resultFilter === r
                        ? 'bg-primary text-white'
                        : 'bg-bg-input text-slate-400 hover:text-white border border-[#2a3142]',
                    )}
                  >
                    {r === 'all' ? '全部' : AUDIT_RESULT_CONFIG[r].label}
                  </button>
                ))}
              </div>
              {/* 搜索 */}
              <div className="relative">
                <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-slate-500" />
                <input
                  type="text"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  placeholder="搜索操作/资源..."
                  className="input !py-1.5 !pl-8 w-44 text-xs"
                />
              </div>
            </div>
          </div>

          {/* 日志时间线 */}
          <div className="space-y-2 max-h-[600px] overflow-y-auto pr-1">
            {filteredLogs.length === 0 ? (
              <div className="text-center py-12 text-sm text-slate-500">未找到匹配的审计日志</div>
            ) : (
              filteredLogs.map((log) => {
                const cfg = AUDIT_RESULT_CONFIG[log.result] ?? AUDIT_RESULT_CONFIG.success
                return (
                  <div
                    key={log.id}
                    className="flex items-start gap-3 p-3 rounded-lg bg-bg-input border border-[#1e2536] hover:border-[#2a3142] transition-colors"
                  >
                    {/* 结果图标 */}
                    <div
                      className={cn(
                        'w-8 h-8 rounded-lg flex items-center justify-center shrink-0 border',
                        cfg.bg,
                        cfg.border,
                      )}
                    >
                      {log.result === 'success' ? (
                        <CheckCircle2 className={cn('w-4 h-4', cfg.color)} />
                      ) : log.result === 'denied' ? (
                        <Ban className={cn('w-4 h-4', cfg.color)} />
                      ) : (
                        <XCircle className={cn('w-4 h-4', cfg.color)} />
                      )}
                    </div>

                    {/* 内容 */}
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className="text-sm font-medium text-white">{log.agent_name}</span>
                        <span className={cn('badge py-0 text-[10px]', cfg.bg, cfg.color, cfg.border)}>
                          {cfg.label}
                        </span>
                        <span className="text-[11px] text-slate-500 font-mono">{log.action}</span>
                      </div>
                      <p className="text-xs text-slate-400 mt-1">{log.details}</p>
                      <div className="flex items-center gap-3 mt-1.5 text-[11px] text-slate-500">
                        <span className="flex items-center gap-1">
                          <ScrollText className="w-3 h-3" />
                          {log.resource}
                        </span>
                        <span>·</span>
                        <span className="font-mono">IP {log.ip_address}</span>
                        <span>·</span>
                        <span>{formatDateTime(log.timestamp)}</span>
                      </div>
                    </div>
                  </div>
                )
              })
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

function SecurityStat({
  title,
  value,
  icon: Icon,
  color,
}: {
  title: string
  value: number
  icon: typeof ShieldCheck
  color: 'primary' | 'accent' | 'paused' | 'circuit'
}) {
  const colorMap = {
    primary: 'text-primary bg-primary/10',
    accent: 'text-accent bg-accent/10',
    paused: 'text-status-paused bg-status-paused/10',
    circuit: 'text-status-circuit bg-status-circuit/10',
  }
  return (
    <div className="card card-hover">
      <div className="flex items-center gap-3">
        <div className={cn('w-10 h-10 rounded-lg flex items-center justify-center shrink-0', colorMap[color])}>
          <Icon className="w-5 h-5" />
        </div>
        <div>
          <p className="text-xs text-slate-500">{title}</p>
          <p className="text-2xl font-bold text-white tabular-nums">{value}</p>
        </div>
      </div>
    </div>
  )
}
