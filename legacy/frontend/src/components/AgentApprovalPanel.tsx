/**
 * Agent 工具审批区块（Phase B / B4）
 * ====================================
 * 数据源：GET  /api/v1/agent-ops/approvals?status=pending
 * 动作：  POST /api/v1/agent-ops/approvals/{thread_id}/decide
 *
 * 展示 write_high 工具人审任务（含 dry-run 效果预览），
 * 批准/驳回后由后端从 Checkpointer 断点恢复执行，结果广播回原聊天频道。
 */
import { useCallback, useEffect, useState } from 'react'
import {
  Bot,
  CheckCircle2,
  XCircle,
  RefreshCw,
  ShieldAlert,
} from 'lucide-react'
import { apiService } from '../services/api'
import { cn, formatDateTime } from '../lib/utils'

interface AgentApproval {
  thread_id: string
  channel_id: string | null
  agent_key: string
  domain: string
  tool: string
  args: Record<string, unknown> | null
  preview: string | null
  risk: string
  status: string
  requested_by: string | null
  decided_by: string | null
  decided_at: string | null
  result_reply: string | null
  created_at: string | null
}

export function AgentApprovalPanel({ onChanged }: { onChanged?: () => void }) {
  const [items, setItems] = useState<AgentApproval[]>([])
  const [loading, setLoading] = useState(false)
  const [acting, setActing] = useState<string | null>(null)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const res = await apiService.getAgentApprovals('pending')
      setItems(Array.isArray(res?.approvals) ? res.approvals : [])
    } catch {
      setError('审批列表加载失败')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const decide = async (threadId: string, approved: boolean) => {
    setActing(threadId)
    setError('')
    // 乐观更新：立即从列表移除（后端恢复执行+LLM综合答复需十几秒，
    // 执行结果会广播回原聊天频道，审批列表不必阻塞等待）
    setItems((prev) => prev.filter((i) => i.thread_id !== threadId))
    try {
      await apiService.decideAgentApproval(threadId, approved, approved ? '批准' : '驳回')
      onChanged?.()
    } catch {
      setError('审批操作失败，请刷新重试')
      load()
    } finally {
      setActing(null)
    }
  }

  return (
    <div className="card p-4">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <ShieldAlert className="w-4 h-4 text-accent" />
          <h3 className="text-sm font-semibold text-white">
            Agent 工具审批
            {items.length > 0 && (
              <span className="ml-2 px-1.5 py-0.5 rounded-full bg-accent/15 text-accent text-[11px]">
                {items.length} 待处理
              </span>
            )}
          </h3>
        </div>
        <button
          onClick={load}
          disabled={loading}
          className="text-slate-400 hover:text-white text-xs flex items-center gap-1"
        >
          <RefreshCw className={cn('w-3 h-3', loading && 'animate-spin')} />
          刷新
        </button>
      </div>

      {error && (
        <div className="mb-3 p-2 rounded-lg bg-status-circuit/10 border border-status-circuit/30 text-xs text-status-circuit">
          {error}
        </div>
      )}

      {items.length === 0 && !loading && (
        <p className="text-xs text-slate-500 py-4 text-center">
          暂无待审批的 Agent 高危操作
        </p>
      )}

      <div className="space-y-3">
        {items.map((item) => (
          <div
            key={item.thread_id}
            className="rounded-xl border border-accent/20 bg-accent/5 p-3 space-y-2"
          >
            <div className="flex items-center justify-between text-xs">
              <span className="flex items-center gap-1.5 text-slate-300">
                <Bot className="w-3.5 h-3.5 text-primary" />
                {item.agent_key} · <code className="text-primary">{item.tool}</code>
              </span>
              <span className="px-1.5 py-0.5 rounded bg-status-circuit/15 text-status-circuit text-[10px] font-medium">
                {item.risk}
              </span>
            </div>

            {item.preview && (
              <p className="text-xs text-slate-400 leading-relaxed">
                {item.preview}
              </p>
            )}

            {item.args && Object.keys(item.args).length > 0 && (
              <pre className="text-[10px] text-slate-500 bg-bg-input/60 rounded p-2 overflow-x-auto">
                {JSON.stringify(item.args, null, 2)}
              </pre>
            )}

            <div className="flex items-center justify-between text-[11px] text-slate-500">
              <span>发起人：{item.requested_by || '—'} · {item.created_at ? formatDateTime(item.created_at) : '—'}</span>
              <div className="flex gap-2">
                <button
                  onClick={() => decide(item.thread_id, false)}
                  disabled={acting === item.thread_id}
                  className="px-2.5 py-1 rounded-lg border border-status-circuit/40 text-status-circuit hover:bg-status-circuit/10 flex items-center gap-1 disabled:opacity-50"
                >
                  <XCircle className="w-3 h-3" />
                  驳回
                </button>
                <button
                  onClick={() => decide(item.thread_id, true)}
                  disabled={acting === item.thread_id}
                  className="px-2.5 py-1 rounded-lg bg-accent/20 border border-accent/40 text-accent hover:bg-accent/30 flex items-center gap-1 disabled:opacity-50"
                >
                  <CheckCircle2 className={cn('w-3 h-3', acting === item.thread_id && 'animate-pulse')} />
                  批准执行
                </button>
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
