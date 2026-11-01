import { useState } from 'react'
import { AgentCustomizationCenter } from '../components/AgentCustomizationCenter'
import { WorkflowDesigner } from '../components/WorkflowDesigner'
import { McpGovernance } from '../components/McpGovernance'
import { SkillStudio } from '../components/SkillStudio'
import { CustomizationAudit } from '../components/CustomizationAudit'
import { EvalTrendBoard } from '../components/EvalTrendBoard'
import { OperationLogBoard } from '../components/OperationLogBoard'
import {
  Settings,
  Activity,
  Bot,
  ShieldCheck,
  Database,
  Plug,
  HardDrive,
  Cpu,
  MemoryStick,
  Network,
  Server,
  Lock,
  KeyRound,
  Fingerprint,
  ScrollText,
  FileSearch,
  Pause,
  Play,
  RotateCcw,
  AlertTriangle,
  AlertCircle,
  Info,
  Clock,
  Archive,
  Trash2,
  RefreshCw,
  CheckCircle2,
  XCircle,
  Globe,
  Languages,
  Layers,
  Zap,
  Eye,
  Save,
  ToggleLeft,
  ToggleRight,
  CloudUpload,
  FileCheck2,
  SlidersHorizontal,
  Workflow,
  Sparkles,
  TrendingUp,
} from 'lucide-react'
import {
  PageHeader,
  Card,
  DataTable,
  StatusBadge,
  Modal,
  StatCard,
  FormField,
  inputClass,
  type Column,
} from '../components/ui'
import { cn } from '../lib/utils'

/* ================================================================
 *  类型定义
 * ================================================================ */

type ServiceStatus = 'running' | 'degraded' | 'stopped'
type AlertLevel = 'warning' | 'critical' | 'info'
type AgentState = 'running' | 'paused' | 'error' | 'idle'
type AgentMode = 'autonomous' | 'supervised' | 'manual'
type ConfigType = 'text' | 'number' | 'boolean' | 'enum'
type BackupType = 'full' | 'incremental'
type BackupStatus = 'success' | 'running' | 'failed'

interface SystemService {
  id: string
  name: string
  status: ServiceStatus
  responseTime: number
  availability: number
}

interface AlertRecord {
  id: string
  time: string
  level: AlertLevel
  source: string
  message: string
  status: string
}

interface AgentRecord {
  id: string
  name: string
  type: string
  status: AgentState
  mode: AgentMode
  cpu: number
  memory: number
  successRate: number
  capabilities: string[]
  lastTask: string
  heartbeat: string
}

interface ConfigItem {
  id: string
  name: string
  value: string
  type: ConfigType
  updatedAt: string
  options?: string[]
}

interface SecurityPolicy {
  id: string
  name: string
  description: string
  icon: typeof Lock
  enabled: boolean
}

interface SecurityEvent {
  id: string
  time: string
  type: string
  ip: string
  user: string
  riskLevel: 'low' | 'medium' | 'high'
  status: string
}

interface DataCategory {
  name: string
  percentage: number
  color: string
}

interface BackupRecord {
  id: string
  type: BackupType
  startTime: string
  duration: string
  size: string
  status: BackupStatus
}

interface IntegrationApp {
  id: string
  name: string
  type: string
  status: 'connected' | 'disconnected' | 'error'
  lastSync: string
}

/* ================================================================
 *  Mock 数据
 * ================================================================ */

// --- Tab 1: 系统监控 ---
const SYSTEM_SERVICES: SystemService[] = [
  { id: 'svc-1', name: 'API 网关', status: 'running', responseTime: 12, availability: 99.98 },
  { id: 'svc-2', name: '认证服务', status: 'running', responseTime: 8, availability: 99.99 },
  { id: 'svc-3', name: '事件总线', status: 'running', responseTime: 3, availability: 99.95 },
  { id: 'svc-4', name: '知识库引擎', status: 'running', responseTime: 45, availability: 99.9 },
  { id: 'svc-5', name: '向量数据库', status: 'degraded', responseTime: 128, availability: 98.2 },
  { id: 'svc-6', name: 'Agent 运行时', status: 'running', responseTime: 22, availability: 99.97 },
  { id: 'svc-7', name: '任务调度器', status: 'running', responseTime: 5, availability: 99.99 },
  { id: 'svc-8', name: '日志服务', status: 'running', responseTime: 15, availability: 99.96 },
]

const ALERT_RECORDS: AlertRecord[] = [
  { id: 'a-1', time: '2026-07-28 14:32:18', level: 'warning', source: '向量数据库', message: '查询响应时间超过阈值 120ms', status: '已处理' },
  { id: 'a-2', time: '2026-07-28 13:05:42', level: 'info', source: '任务调度器', message: '每日全量索引任务已完成', status: '已处理' },
  { id: 'a-3', time: '2026-07-28 11:48:03', level: 'critical', source: 'Agent 运行时', message: 'Agent finance-bot-03 内存使用率超过 90%', status: '已恢复' },
  { id: 'a-4', time: '2026-07-28 09:15:27', level: 'warning', source: 'API 网关', message: '请求错误率短暂上升至 1.2%', status: '已处理' },
  { id: 'a-5', time: '2026-07-28 02:00:11', level: 'info', source: '备份服务', message: '每日增量备份已自动完成', status: '已完成' },
]

// --- Tab 2: Agent 生命周期 ---
const AGENT_RECORDS: AgentRecord[] = [
  { id: 'ag-1', name: '财务核算 Agent', type: '财务', status: 'running', mode: 'autonomous', cpu: 18.5, memory: 412, successRate: 98.6, capabilities: ['凭证生成', '对账核对', '报表分析'], lastTask: '生成 7 月损益表凭证', heartbeat: '2026-07-28 14:35:22' },
  { id: 'ag-2', name: '发票核验 Agent', type: '税务', status: 'running', mode: 'supervised', cpu: 12.3, memory: 256, successRate: 99.2, capabilities: ['发票识别', '真伪查验', '抵扣计算'], lastTask: '核验 INV-2026-07892', heartbeat: '2026-07-28 14:34:58' },
  { id: 'ag-3', name: '合同审查 Agent', type: '法务', status: 'running', mode: 'supervised', cpu: 8.7, memory: 188, successRate: 97.4, capabilities: ['条款提取', '风险识别', '合规检查'], lastTask: '审查采购合同 CT-2026-0341', heartbeat: '2026-07-28 14:35:10' },
  { id: 'ag-4', name: '库存监控 Agent', type: '运营', status: 'running', mode: 'autonomous', cpu: 6.2, memory: 142, successRate: 99.8, capabilities: ['库存预警', '补货建议', '周转分析'], lastTask: '检查 3 种低库存物料', heartbeat: '2026-07-28 14:35:30' },
  { id: 'ag-5', name: '风险预警 Agent', type: '风控', status: 'paused', mode: 'manual', cpu: 0, memory: 0, successRate: 96.1, capabilities: ['风险扫描', '预警推送', '趋势分析'], lastTask: '暂停前完成季度风险扫描', heartbeat: '2026-07-28 10:22:15' },
  { id: 'ag-6', name: '客户服务 Agent', type: '客服', status: 'running', mode: 'autonomous', cpu: 24.8, memory: 528, successRate: 95.3, capabilities: ['意图识别', '工单分配', 'FAQ 回复'], lastTask: '处理客户咨询 #CS-8821', heartbeat: '2026-07-28 14:35:28' },
  { id: 'ag-7', name: '报销审批 Agent', type: '财务', status: 'running', mode: 'supervised', cpu: 5.1, memory: 98, successRate: 99.5, capabilities: ['单据核验', '额度校验', '自动审批'], lastTask: '审批报销单 RB-2026-1125', heartbeat: '2026-07-28 14:34:45' },
  { id: 'ag-8', name: '数据同步 Agent', type: '运维', status: 'running', mode: 'autonomous', cpu: 15.6, memory: 320, successRate: 98.9, capabilities: ['ETL 同步', '数据校验', '异常告警'], lastTask: '同步 ERP -> 数据中台', heartbeat: '2026-07-28 14:35:15' },
  { id: 'ag-9', name: '报表生成 Agent', type: '运营', status: 'idle', mode: 'supervised', cpu: 0.8, memory: 56, successRate: 99.0, capabilities: ['BI 报表', '数据聚合', '趋势分析'], lastTask: '等待下次调度任务', heartbeat: '2026-07-28 14:20:00' },
  { id: 'ag-10', name: '招聘筛选 Agent', type: '人力资源', status: 'paused', mode: 'manual', cpu: 0, memory: 0, successRate: 94.7, capabilities: ['简历解析', '匹配评分', '初筛推荐'], lastTask: '暂停前筛选 12 份简历', heartbeat: '2026-07-27 18:30:42' },
]

// --- Tab 3: 配置管理 ---
const CONFIG_GROUPS: { group: string; icon: typeof Settings; items: ConfigItem[] }[] = [
  {
    group: '系统配置',
    icon: Settings,
    items: [
      { id: 'cfg-1', name: '时区', value: 'Asia/Shanghai (UTC+8)', type: 'enum', updatedAt: '2026-06-15 10:30', options: ['Asia/Shanghai (UTC+8)', 'UTC', 'America/New_York'] },
      { id: 'cfg-2', name: '语言', value: '简体中文', type: 'enum', updatedAt: '2026-06-15 10:30', options: ['简体中文', 'English', '日本語'] },
      { id: 'cfg-3', name: '会话超时', value: '30 分钟', type: 'number', updatedAt: '2026-07-10 14:22' },
      { id: 'cfg-4', name: '最大并发', value: '50', type: 'number', updatedAt: '2026-07-20 09:15' },
    ],
  },
  {
    group: '安全配置',
    icon: ShieldCheck,
    items: [
      { id: 'cfg-5', name: '密码策略', value: '强密码（12位+特殊字符）', type: 'enum', updatedAt: '2026-05-20 16:00', options: ['弱密码（8位）', '中密码（10位）', '强密码（12位+特殊字符）'] },
      { id: 'cfg-6', name: 'JWT 过期时间', value: '7200 秒', type: 'number', updatedAt: '2026-06-01 11:00' },
      { id: 'cfg-7', name: '登录失败锁定', value: '5 次失败后锁定 30 分钟', type: 'boolean', updatedAt: '2026-05-20 16:00' },
      { id: 'cfg-8', name: 'IP 白名单', value: '192.168.1.0/24, 10.0.0.0/8', type: 'text', updatedAt: '2026-07-25 14:30' },
    ],
  },
  {
    group: '业务配置',
    icon: Layers,
    items: [
      { id: 'cfg-9', name: '审批超时', value: '48 小时', type: 'number', updatedAt: '2026-06-10 09:00' },
      { id: 'cfg-10', name: '自动备份频率', value: '每日 02:00', type: 'enum', updatedAt: '2026-05-01 08:00', options: ['每日 02:00', '每 6 小时', '每小时'] },
      { id: 'cfg-11', name: '知识库索引策略', value: '增量索引（实时）', type: 'enum', updatedAt: '2026-07-15 13:45', options: ['增量索引（实时）', '全量索引（每日）', '混合模式'] },
    ],
  },
]

// --- Tab 4: 安全控制 ---
const SECURITY_POLICIES: SecurityPolicy[] = [
  { id: 'sp-1', name: '密码策略', description: '强密码要求：12 位以上，含大小写字母、数字和特殊字符', icon: Lock, enabled: true },
  { id: 'sp-2', name: '双因素认证', description: '所有管理员账号强制启用 TOTP 双因素认证', icon: Fingerprint, enabled: true },
  { id: 'sp-3', name: 'IP 限制', description: '仅允许白名单 IP 段访问管理后台', icon: Globe, enabled: true },
  { id: 'sp-4', name: 'API 密钥管理', description: 'API 密钥自动轮换，有效期 90 天', icon: KeyRound, enabled: true },
  { id: 'sp-5', name: '数据加密', description: '传输层 TLS 1.3，存储层 AES-256 加密', icon: ShieldCheck, enabled: true },
  { id: 'sp-6', name: '审计日志', description: '记录全部操作行为，保留 180 天', icon: ScrollText, enabled: false },
]

const SECURITY_EVENTS: SecurityEvent[] = [
  { id: 'se-1', time: '2026-07-28 14:02:33', type: '异常登录', ip: '203.45.12.88', user: 'unknown', riskLevel: 'high', status: '已拦截' },
  { id: 'se-2', time: '2026-07-28 11:30:15', type: '密码错误超限', ip: '192.168.1.55', user: 'zhang_min', riskLevel: 'medium', status: '已锁定' },
  { id: 'se-3', time: '2026-07-28 08:45:22', type: '权限变更', ip: '192.168.1.10', user: 'admin', riskLevel: 'low', status: '已确认' },
  { id: 'se-4', time: '2026-07-27 22:18:07', type: 'API 密钥使用', ip: '10.0.0.22', user: 'system_bot', riskLevel: 'low', status: '正常' },
  { id: 'se-5', time: '2026-07-27 16:55:40', type: '敏感数据访问', ip: '192.168.1.33', user: 'li_hua', riskLevel: 'medium', status: '已审计' },
]

// --- Tab 5: 数据治理 ---
const DATA_CATEGORIES: DataCategory[] = [
  { name: '用户数据', percentage: 22, color: 'bg-primary' },
  { name: '业务数据', percentage: 35, color: 'bg-accent' },
  { name: '财务数据', percentage: 18, color: 'bg-status-paused' },
  { name: '文档数据', percentage: 15, color: 'bg-blue-400' },
  { name: '日志数据', percentage: 10, color: 'bg-slate-500' },
]

const DATA_QUALITY = [
  { label: '完整性', value: 98.6, icon: CheckCircle2, color: 'text-accent' },
  { label: '一致性', value: 96.2, icon: Layers, color: 'text-primary' },
  { label: '准确性', value: 94.8, icon: CheckCircle2, color: 'text-accent' },
  { label: '时效性', value: 92.1, icon: Clock, color: 'text-status-paused' },
]

const DATA_LIFECYCLE = [
  { id: 'dl-1', name: '保留策略', description: '业务数据保留 7 年，日志数据保留 180 天', icon: Archive, color: 'text-primary' },
  { id: 'dl-2', name: '归档策略', description: '超过 1 年的已完成工单自动归档至冷存储', icon: HardDrive, color: 'text-accent' },
  { id: 'dl-3', name: '删除策略', description: '用户注销后数据脱敏保留 90 天后永久删除', icon: Trash2, color: 'text-status-failed' },
]

// --- Tab 6: 集成平台 ---
const INTEGRATION_APPS: IntegrationApp[] = [
  { id: 'ia-1', name: 'ERP 系统', type: '企业资源计划', status: 'connected', lastSync: '2 分钟前' },
  { id: 'ia-2', name: 'CRM 系统', type: '客户关系管理', status: 'connected', lastSync: '5 分钟前' },
  { id: 'ia-3', name: '银行接口', type: '资金结算', status: 'connected', lastSync: '1 分钟前' },
  { id: 'ia-4', name: '税务平台', type: '税务申报', status: 'connected', lastSync: '12 分钟前' },
  { id: 'ia-5', name: '电子发票', type: '发票管理', status: 'connected', lastSync: '3 分钟前' },
  { id: 'ia-6', name: 'OA 系统', type: '办公协同', status: 'connected', lastSync: '8 分钟前' },
  { id: 'ia-7', name: '邮件系统', type: '通信服务', status: 'error', lastSync: '2 小时前' },
  { id: 'ia-8', name: '云存储', type: '文件存储', status: 'connected', lastSync: '实时' },
]

const API_CALL_TREND = [
  { day: '周一', calls: 7800 },
  { day: '周二', calls: 8200 },
  { day: '周三', calls: 9100 },
  { day: '周四', calls: 8500 },
  { day: '周五', calls: 9800 },
  { day: '周六', calls: 4200 },
  { day: '周日', calls: 3900 },
]

// --- Tab 7: 备份恢复 ---
const BACKUP_RECORDS: BackupRecord[] = [
  { id: 'BK-20260728-0200', type: 'incremental', startTime: '2026-07-28 02:00:00', duration: '18 分钟', size: '4.2 GB', status: 'success' },
  { id: 'BK-20260727-0200', type: 'incremental', startTime: '2026-07-27 02:00:00', duration: '22 分钟', size: '4.8 GB', status: 'success' },
  { id: 'BK-20260726-0200', type: 'full', startTime: '2026-07-26 02:00:00', duration: '1 小时 15 分', size: '128 GB', status: 'success' },
  { id: 'BK-20260725-0200', type: 'incremental', startTime: '2026-07-25 02:00:00', duration: '16 分钟', size: '3.9 GB', status: 'success' },
  { id: 'BK-20260724-0200', type: 'incremental', startTime: '2026-07-24 02:00:00', duration: '20 分钟', size: '4.1 GB', status: 'failed' },
  { id: 'BK-20260723-0200', type: 'incremental', startTime: '2026-07-23 02:00:00', duration: '19 分钟', size: '4.0 GB', status: 'success' },
  { id: 'BK-20260722-0200', type: 'incremental', startTime: '2026-07-22 02:00:00', duration: '21 分钟', size: '4.3 GB', status: 'success' },
  { id: 'BK-20260721-0200', type: 'incremental', startTime: '2026-07-21 02:00:00', duration: '25 分钟', size: '4.5 GB', status: 'success' },
  { id: 'BK-20260721-1400', type: 'full', startTime: '2026-07-21 14:00:00', duration: '进行中', size: '—', status: 'running' },
]

const RECOVERY_TESTS = [
  { id: 'rt-1', date: '2026-07-21 15:30', target: '测试环境', result: '成功', duration: '8 分钟', note: '数据完整性校验通过' },
  { id: 'rt-2', date: '2026-07-14 15:30', target: '灾备环境', result: '成功', duration: '12 分钟', note: '全量恢复验证通过' },
  { id: 'rt-3', date: '2026-07-07 15:30', target: '测试环境', result: '成功', duration: '9 分钟', note: '增量恢复验证通过' },
]

/* ================================================================
 *  Tab 定义
 * ================================================================ */

interface TabItem {
  key: string
  label: string
  icon: typeof Activity
}

const TABS: TabItem[] = [
  { key: 'monitor', label: '系统监控', icon: Activity },
  { key: 'customization', label: 'Agent 定制中心', icon: SlidersHorizontal },
  { key: 'workflow', label: '工作流设计', icon: Workflow },
  { key: 'mcp', label: 'MCP 治理', icon: Server },
  { key: 'skills', label: '技能工坊', icon: Sparkles },
  { key: 'audit', label: '定制审计', icon: ScrollText },
  { key: 'oplogs', label: '操作日志', icon: FileSearch },
  { key: 'evals', label: '评测趋势', icon: TrendingUp },
  { key: 'agent', label: 'Agent 生命周期', icon: Bot },
  { key: 'config', label: '配置管理', icon: Settings },
  { key: 'security', label: '安全控制', icon: ShieldCheck },
  { key: 'governance', label: '数据治理', icon: Database },
  { key: 'integration', label: '集成平台', icon: Plug },
  { key: 'backup', label: '备份恢复', icon: HardDrive },
]

/* ================================================================
 *  通用辅助组件
 * ================================================================ */

function ToggleSwitch({ enabled, onClick }: { enabled: boolean; onClick: () => void }) {
  return (
    <button onClick={onClick} className="flex items-center gap-1 transition-colors">
      {enabled ? (
        <ToggleRight className="w-7 h-7 text-accent" />
      ) : (
        <ToggleLeft className="w-7 h-7 text-slate-600" />
      )}
    </button>
  )
}

function ProgressBar({ value, max, color }: { value: number; max: number; color: string }) {
  const pct = Math.min(100, (value / max) * 100)
  return (
    <div className="h-1.5 rounded-full bg-bg-input overflow-hidden">
      <div className={cn('h-full rounded-full transition-all', color)} style={{ width: `${pct}%` }} />
    </div>
  )
}

/* ================================================================
 *  Tab 1: 系统监控
 * ================================================================ */

const serviceStatusMap: Record<ServiceStatus, { label: string; variant: 'success' | 'warning' | 'danger' }> = {
  running: { label: '运行中', variant: 'success' },
  degraded: { label: '降级', variant: 'warning' },
  stopped: { label: '停止', variant: 'danger' },
}

const alertLevelMap: Record<AlertLevel, { label: string; variant: 'warning' | 'danger' | 'info' }> = {
  warning: { label: '警告', variant: 'warning' },
  critical: { label: '严重', variant: 'danger' },
  info: { label: '信息', variant: 'info' },
}

function SystemMonitorTab() {
  const alertColumns: Column<AlertRecord>[] = [
    { key: 'time', label: '时间', sortable: true, render: (r) => <span className="font-mono text-xs text-slate-400">{r.time}</span> },
    {
      key: 'level',
      label: '级别',
      render: (r) => {
        const cfg = alertLevelMap[r.level] ?? Object.values(alertLevelMap)[0]
        const icon =
          r.level === 'warning' ? <AlertTriangle className="w-3 h-3" /> :
          r.level === 'critical' ? <AlertCircle className="w-3 h-3" /> :
          <Info className="w-3 h-3" />
        return (
          <StatusBadge variant={cfg.variant} dot>
            {icon}
            {cfg.label}
          </StatusBadge>
        )
      },
    },
    { key: 'source', label: '来源', render: (r) => <span className="text-slate-300">{r.source}</span> },
    { key: 'message', label: '消息', render: (r) => <span className="text-slate-400">{r.message}</span> },
    { key: 'status', label: '状态', render: (r) => <StatusBadge variant="neutral">{r.status}</StatusBadge> },
  ]

  return (
    <div className="space-y-6">
      {/* 指标卡片 */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard label="CPU 使用率" value="34.2%" icon={<Cpu className="w-5 h-5" />} variant="primary" trend="2.1%" trendUp={false} />
        <StatCard label="内存使用" value="6.8 / 16 GB" icon={<MemoryStick className="w-5 h-5" />} variant="accent" trend="0.3 GB" trendUp={false} />
        <StatCard label="磁盘使用" value="456 / 1024 GB" icon={<HardDrive className="w-5 h-5" />} variant="warning" trend="5.2 GB" trendUp />
        <StatCard label="网络流量" value="128 Mbps" icon={<Network className="w-5 h-5" />} variant="primary" trend="12 Mbps" trendUp />
      </div>

      {/* 服务状态 */}
      <Card title="核心服务状态">
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
          {SYSTEM_SERVICES.map((svc) => {
            const cfg = serviceStatusMap[svc.status] ?? Object.values(serviceStatusMap)[0]
            const isOk = svc.status === 'running'
            return (
              <div
                key={svc.id}
                className="p-4 rounded-lg bg-bg-input border border-[#1e2536] hover:border-[#2a3142] transition-colors"
              >
                <div className="flex items-center justify-between mb-3">
                  <div className="flex items-center gap-2">
                    <div className={cn(
                      'w-8 h-8 rounded-lg flex items-center justify-center',
                      isOk ? 'bg-accent/10' : svc.status === 'degraded' ? 'bg-status-paused/10' : 'bg-status-failed/10',
                    )}>
                      <Server className={cn('w-4 h-4', isOk ? 'text-accent' : svc.status === 'degraded' ? 'text-status-paused' : 'text-status-failed')} />
                    </div>
                    <span className="text-sm font-medium text-white">{svc.name}</span>
                  </div>
                </div>
                <StatusBadge variant={cfg.variant} dot>
                  {cfg.label}
                </StatusBadge>
                <div className="mt-3 space-y-1.5">
                  <div className="flex items-center justify-between text-xs">
                    <span className="text-slate-500">响应时间</span>
                    <span className={cn('font-mono', svc.responseTime > 100 ? 'text-status-paused' : 'text-slate-300')}>
                      {svc.responseTime} ms
                    </span>
                  </div>
                  <div className="flex items-center justify-between text-xs">
                    <span className="text-slate-500">可用率</span>
                    <span className={cn('font-mono', svc.availability < 99 ? 'text-status-paused' : 'text-accent')}>
                      {svc.availability}%
                    </span>
                  </div>
                </div>
              </div>
            )
          })}
        </div>
      </Card>

      {/* 最近告警 */}
      <Card title="最近告警">
        <DataTable
          columns={alertColumns}
          data={ALERT_RECORDS}
          searchable={false}
          pageSize={5}
        />
      </Card>
    </div>
  )
}

/* ================================================================
 *  Tab 2: Agent 生命周期
 * ================================================================ */

const agentStateMap: Record<AgentState, { label: string; variant: 'success' | 'warning' | 'danger' | 'neutral' }> = {
  running: { label: '运行中', variant: 'success' },
  paused: { label: '暂停', variant: 'warning' },
  error: { label: '异常', variant: 'danger' },
  idle: { label: '空闲', variant: 'neutral' },
}

const agentModeMap: Record<AgentMode, string> = {
  autonomous: '自主',
  supervised: '监督',
  manual: '手动',
}

function AgentLifecycleTab() {
  const [detailAgent, setDetailAgent] = useState<AgentRecord | null>(null)

  const columns: Column<AgentRecord>[] = [
    {
      key: 'name',
      label: 'Agent 名称',
      sortable: true,
      render: (r) => (
        <div className="flex items-center gap-2">
          <div className="w-7 h-7 rounded-lg bg-primary/10 flex items-center justify-center shrink-0">
            <Bot className="w-3.5 h-3.5 text-primary" />
          </div>
          <div>
            <div className="text-sm font-medium text-white">{r.name}</div>
            <div className="text-[11px] text-slate-500 font-mono">{r.id}</div>
          </div>
        </div>
      ),
    },
    { key: 'type', label: '类型', sortable: true, render: (r) => <span className="text-slate-300">{r.type}</span> },
    {
      key: 'status',
      label: '状态',
      sortable: true,
      render: (r) => {
        const cfg = agentStateMap[r.status] ?? Object.values(agentStateMap)[0]
        return <StatusBadge variant={cfg.variant} dot>{cfg.label}</StatusBadge>
      },
    },
    { key: 'mode', label: '模式', render: (r) => <span className="text-slate-300">{agentModeMap[r.mode]}</span> },
    { key: 'cpu', label: 'CPU', sortable: true, align: 'right', render: (r) => <span className="font-mono text-slate-300">{r.cpu}%</span> },
    { key: 'memory', label: '内存', sortable: true, align: 'right', render: (r) => <span className="font-mono text-slate-300">{r.memory} MB</span> },
    { key: 'successRate', label: '成功率', sortable: true, align: 'right', render: (r) => <span className={cn('font-mono', r.successRate >= 98 ? 'text-accent' : r.successRate >= 95 ? 'text-status-paused' : 'text-status-failed')}>{r.successRate}%</span> },
    {
      key: 'actions',
      label: '操作',
      align: 'center',
      render: (r) => (
        <div className="flex items-center justify-center gap-1.5">
          {r.status === 'running' ? (
            <button className="inline-flex items-center gap-1 px-2 py-1 rounded-md text-xs bg-status-paused/10 text-status-paused hover:bg-status-paused/20 transition-colors">
              <Pause className="w-3 h-3" />
              暂停
            </button>
          ) : (
            <button className="inline-flex items-center gap-1 px-2 py-1 rounded-md text-xs bg-accent/10 text-accent hover:bg-accent/20 transition-colors">
              <Play className="w-3 h-3" />
              恢复
            </button>
          )}
          <button className="inline-flex items-center gap-1 px-2 py-1 rounded-md text-xs bg-bg-input text-slate-400 hover:text-white border border-[#2a3142] transition-colors">
            <RotateCcw className="w-3 h-3" />
            重启
          </button>
        </div>
      ),
    },
  ]

  return (
    <div className="space-y-6">
      {/* 指标卡片 */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard label="总 Agent 数" value={33} icon={<Bot className="w-5 h-5" />} variant="primary" />
        <StatCard label="运行中" value={31} icon={<Activity className="w-5 h-5" />} variant="accent" />
        <StatCard label="暂停" value={2} icon={<Pause className="w-5 h-5" />} variant="warning" />
        <StatCard label="异常" value={0} icon={<AlertCircle className="w-5 h-5" />} variant="danger" />
      </div>

      {/* Agent 列表 */}
      <Card title="Agent 列表">
        <DataTable
          columns={columns}
          data={AGENT_RECORDS}
          searchKeys={['name', 'id', 'type']}
          searchPlaceholder="搜索 Agent 名称 / ID / 类型..."
          pageSize={8}
          onRowClick={(r) => setDetailAgent(r)}
        />
      </Card>

      {/* Agent 详情弹窗 */}
      <Modal
        open={!!detailAgent}
        onClose={() => setDetailAgent(null)}
        title={detailAgent ? `${detailAgent.name} · Agent 详情` : ''}
        size="lg"
      >
        {detailAgent && <AgentDetail agent={detailAgent} />}
      </Modal>
    </div>
  )
}

function AgentDetail({ agent }: { agent: AgentRecord }) {
  const cfg = agentStateMap[agent.status] ?? Object.values(agentStateMap)[0]
  return (
    <div className="space-y-5">
      {/* 基本信息 */}
      <div className="flex flex-wrap items-center gap-3 p-3 rounded-lg bg-bg-input border border-[#2a3142]">
        <div className="flex items-center gap-2">
          <span className="text-xs text-slate-500">ID</span>
          <span className="font-mono text-xs text-primary">{agent.id}</span>
        </div>
        <div className="w-px h-3 bg-[#2a3142]" />
        <div className="flex items-center gap-2">
          <span className="text-xs text-slate-500">类型</span>
          <span className="text-xs text-slate-300">{agent.type}</span>
        </div>
        <div className="w-px h-3 bg-[#2a3142]" />
        <div className="flex items-center gap-2">
          <span className="text-xs text-slate-500">模式</span>
          <span className="text-xs text-slate-300">{agentModeMap[agent.mode]}</span>
        </div>
        <div className="w-px h-3 bg-[#2a3142]" />
        <StatusBadge variant={cfg.variant} dot>{cfg.label}</StatusBadge>
      </div>

      {/* 运行指标 */}
      <div className="grid grid-cols-3 gap-3">
        <div className="p-3 rounded-lg bg-bg-input border border-[#1e2536]">
          <div className="flex items-center gap-1.5 mb-1">
            <Cpu className="w-3.5 h-3.5 text-primary" />
            <span className="text-xs text-slate-500">CPU 使用率</span>
          </div>
          <span className="text-lg font-bold text-white tabular-nums">{agent.cpu}%</span>
          <div className="mt-2"><ProgressBar value={agent.cpu} max={100} color="bg-primary" /></div>
        </div>
        <div className="p-3 rounded-lg bg-bg-input border border-[#1e2536]">
          <div className="flex items-center gap-1.5 mb-1">
            <MemoryStick className="w-3.5 h-3.5 text-accent" />
            <span className="text-xs text-slate-500">内存占用</span>
          </div>
          <span className="text-lg font-bold text-white tabular-nums">{agent.memory} MB</span>
          <div className="mt-2"><ProgressBar value={agent.memory} max={1024} color="bg-accent" /></div>
        </div>
        <div className="p-3 rounded-lg bg-bg-input border border-[#1e2536]">
          <div className="flex items-center gap-1.5 mb-1">
            <Activity className="w-3.5 h-3.5 text-accent" />
            <span className="text-xs text-slate-500">任务成功率</span>
          </div>
          <span className="text-lg font-bold text-white tabular-nums">{agent.successRate}%</span>
          <div className="mt-2"><ProgressBar value={agent.successRate} max={100} color="bg-accent" /></div>
        </div>
      </div>

      {/* 能力描述 */}
      <div>
        <h4 className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-2">能力描述</h4>
        <div className="flex flex-wrap gap-2">
          {agent.capabilities.map((cap) => (
            <span key={cap} className="inline-flex items-center px-2.5 py-1 rounded-md text-xs bg-primary/10 text-primary border border-primary/20">
              {cap}
            </span>
          ))}
        </div>
      </div>

      {/* 最近任务 */}
      <div>
        <h4 className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-2">最近任务</h4>
        <div className="flex items-center gap-2 p-3 rounded-lg bg-bg-input border border-[#1e2536]">
          <CheckCircle2 className="w-4 h-4 text-accent shrink-0" />
          <span className="text-sm text-slate-300">{agent.lastTask}</span>
        </div>
      </div>

      {/* 心跳信息 */}
      <div>
        <h4 className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-2">心跳信息</h4>
        <div className="flex items-center gap-2 p-3 rounded-lg bg-bg-input border border-[#1e2536]">
          <div className={cn('w-2 h-2 rounded-full', agent.status === 'running' ? 'bg-accent pulse-dot' : 'bg-slate-600')} />
          <span className="text-sm text-slate-300">最后心跳：</span>
          <span className="font-mono text-sm text-slate-400">{agent.heartbeat}</span>
        </div>
      </div>
    </div>
  )
}

/* ================================================================
 *  Tab 3: 配置管理
 * ================================================================ */

const configTypeMap: Record<ConfigType, { label: string; variant: 'info' | 'accent' | 'warning' | 'neutral' }> = {
  text: { label: '文本', variant: 'neutral' },
  number: { label: '数字', variant: 'info' },
  boolean: { label: '布尔', variant: 'warning' },
  enum: { label: '枚举', variant: 'accent' },
}

function ConfigManagementTab() {
  const [editConfig, setEditConfig] = useState<ConfigItem | null>(null)

  return (
    <div className="space-y-6">
      {CONFIG_GROUPS.map((group) => (
        <Card
          key={group.group}
          title={group.group}
          action={
            <div className="flex items-center gap-1.5 text-xs text-slate-500">
              <group.icon className="w-3.5 h-3.5" />
              {group.items.length} 项
            </div>
          }
        >
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            {group.items.map((item) => {
              const cfg = configTypeMap[item.type] ?? Object.values(configTypeMap)[0]
              return (
                <button
                  key={item.id}
                  onClick={() => setEditConfig(item)}
                  className="text-left p-4 rounded-lg bg-bg-input border border-[#1e2536] hover:border-primary/30 transition-colors group"
                >
                  <div className="flex items-center justify-between mb-2">
                    <span className="text-sm font-medium text-white">{item.name}</span>
                    <StatusBadge variant={cfg.variant}>{cfg.label}</StatusBadge>
                  </div>
                  <div className="flex items-center justify-between">
                    <span className="text-sm text-slate-300 truncate font-mono">{item.value}</span>
                    <Eye className="w-4 h-4 text-slate-600 group-hover:text-primary transition-colors shrink-0 ml-2" />
                  </div>
                  <div className="flex items-center gap-1 mt-2 text-[11px] text-slate-500">
                    <Clock className="w-3 h-3" />
                    最后修改：{item.updatedAt}
                  </div>
                </button>
              )
            })}
          </div>
        </Card>
      ))}

      {/* 编辑弹窗 */}
      <Modal
        open={!!editConfig}
        onClose={() => setEditConfig(null)}
        title={editConfig ? `编辑配置：${editConfig.name}` : ''}
        size="md"
      >
        {editConfig && <ConfigEditForm config={editConfig} onClose={() => setEditConfig(null)} />}
      </Modal>
    </div>
  )
}

function ConfigEditForm({ config, onClose }: { config: ConfigItem; onClose: () => void }) {
  const [value, setValue] = useState(config.value)
  const cfg = configTypeMap[config.type] ?? Object.values(configTypeMap)[0]

  return (
    <div className="space-y-4">
      <FormField label="配置名称">
        <div className="flex items-center gap-2">
          <span className="text-sm text-white">{config.name}</span>
          <StatusBadge variant={cfg.variant}>{cfg.label}</StatusBadge>
        </div>
      </FormField>

      <FormField label="当前值">
        <span className="text-sm text-slate-400 font-mono">{config.value}</span>
      </FormField>

      <FormField label="新值" required>
        {config.type === 'boolean' ? (
          <select value={value} onChange={(e) => setValue(e.target.value)} className={inputClass}>
            <option value="启用">启用</option>
            <option value="禁用">禁用</option>
          </select>
        ) : config.type === 'enum' && config.options ? (
          <select value={value} onChange={(e) => setValue(e.target.value)} className={inputClass}>
            {config.options.map((opt) => (
              <option key={opt} value={opt}>{opt}</option>
            ))}
          </select>
        ) : (
          <input
            type={config.type === 'number' ? 'number' : 'text'}
            value={value}
            onChange={(e) => setValue(e.target.value)}
            className={inputClass}
          />
        )}
      </FormField>

      <div className="p-3 rounded-lg bg-primary/5 border border-primary/20 text-xs text-slate-400">
        最后修改时间：{config.updatedAt}。修改后将立即生效并记录审计日志。
      </div>

      <div className="flex gap-3 pt-1">
        <button onClick={onClose} className="btn-secondary flex-1">
          取消
        </button>
        <button onClick={onClose} className="btn-primary flex-1">
          <Save className="w-4 h-4" />
          保存配置
        </button>
      </div>
    </div>
  )
}

/* ================================================================
 *  Tab 4: 安全控制
 * ================================================================ */

const riskLevelMap: Record<string, { label: string; variant: 'success' | 'warning' | 'danger' }> = {
  low: { label: '低风险', variant: 'success' },
  medium: { label: '中风险', variant: 'warning' },
  high: { label: '高风险', variant: 'danger' },
}

function SecurityControlTab() {
  const [policies, setPolicies] = useState<SecurityPolicy[]>(SECURITY_POLICIES)

  const togglePolicy = (id: string) => {
    setPolicies((prev) => prev.map((p) => (p.id === id ? { ...p, enabled: !p.enabled } : p)))
  }

  const eventColumns: Column<SecurityEvent>[] = [
    { key: 'time', label: '时间', sortable: true, render: (r) => <span className="font-mono text-xs text-slate-400">{r.time}</span> },
    { key: 'type', label: '事件类型', sortable: true, render: (r) => <span className="text-slate-300">{r.type}</span> },
    { key: 'ip', label: '来源 IP', render: (r) => <span className="font-mono text-xs text-slate-400">{r.ip}</span> },
    { key: 'user', label: '用户', render: (r) => <span className="text-slate-300">{r.user}</span> },
    {
      key: 'riskLevel',
      label: '风险级别',
      sortable: true,
      render: (r) => {
        const cfg = riskLevelMap[r.riskLevel] ?? Object.values(riskLevelMap)[0]
        return <StatusBadge variant={cfg.variant} dot>{cfg.label}</StatusBadge>
      },
    },
    { key: 'status', label: '状态', render: (r) => <StatusBadge variant="neutral">{r.status}</StatusBadge> },
  ]

  return (
    <div className="space-y-6">
      {/* 指标卡片 */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard label="活跃会话" value={28} icon={<Activity className="w-5 h-5" />} variant="primary" />
        <StatCard label="今日登录" value={56} icon={<CheckCircle2 className="w-5 h-5" />} variant="accent" trend="12%" trendUp />
        <StatCard label="失败登录" value={3} icon={<XCircle className="w-5 h-5" />} variant="warning" />
        <StatCard label="安全事件" value={1} icon={<AlertTriangle className="w-5 h-5" />} variant="danger" />
      </div>

      {/* 安全策略 */}
      <Card title="安全策略">
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
          {policies.map((policy) => (
            <div
              key={policy.id}
              className="p-4 rounded-lg bg-bg-input border border-[#1e2536] hover:border-[#2a3142] transition-colors"
            >
              <div className="flex items-start justify-between mb-2">
                <div className="flex items-center gap-2">
                  <div className={cn(
                    'w-8 h-8 rounded-lg flex items-center justify-center',
                    policy.enabled ? 'bg-accent/10' : 'bg-slate-500/10',
                  )}>
                    <policy.icon className={cn('w-4 h-4', policy.enabled ? 'text-accent' : 'text-slate-500')} />
                  </div>
                  <span className="text-sm font-medium text-white">{policy.name}</span>
                </div>
                <ToggleSwitch enabled={policy.enabled} onClick={() => togglePolicy(policy.id)} />
              </div>
              <p className="text-xs text-slate-500 leading-relaxed">{policy.description}</p>
              <div className="mt-2">
                <StatusBadge variant={policy.enabled ? 'success' : 'neutral'} dot>
                  {policy.enabled ? '启用' : '禁用'}
                </StatusBadge>
              </div>
            </div>
          ))}
        </div>
      </Card>

      {/* 安全事件 */}
      <Card title="最近安全事件">
        <DataTable
          columns={eventColumns}
          data={SECURITY_EVENTS}
          searchable={false}
          pageSize={5}
        />
      </Card>
    </div>
  )
}

/* ================================================================
 *  Tab 5: 数据治理
 * ================================================================ */

function DataGovernanceTab() {
  return (
    <div className="space-y-6">
      {/* 指标卡片 */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard label="数据总量" value="286 GB" icon={<Database className="w-5 h-5" />} variant="primary" trend="8.2 GB" trendUp />
        <StatCard label="数据表数" value={128} icon={<Layers className="w-5 h-5" />} variant="accent" />
        <StatCard label="今日新增" value="1.2 GB" icon={<CloudUpload className="w-5 h-5" />} variant="primary" trend="0.3 GB" trendUp />
        <StatCard label="数据质量分" value="94.2%" icon={<CheckCircle2 className="w-5 h-5" />} variant="accent" trend="1.1%" trendUp />
      </div>

      {/* 数据分类 + 数据质量 */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* 数据分类统计 */}
        <Card title="数据分类统计">
          <div className="space-y-3">
            {DATA_CATEGORIES.map((cat) => (
              <div key={cat.name}>
                <div className="flex items-center justify-between mb-1">
                  <span className="text-sm text-slate-300">{cat.name}</span>
                  <span className="text-sm font-mono text-slate-400">{cat.percentage}%</span>
                </div>
                <div className="h-2.5 rounded-full bg-bg-input overflow-hidden">
                  <div
                    className={cn('h-full rounded-full transition-all', cat.color)}
                    style={{ width: `${cat.percentage * 2.8}%` }}
                  />
                </div>
              </div>
            ))}
          </div>
        </Card>

        {/* 数据质量指标 */}
        <Card title="数据质量指标">
          <div className="grid grid-cols-2 gap-3">
            {DATA_QUALITY.map((q) => (
              <div key={q.label} className="p-4 rounded-lg bg-bg-input border border-[#1e2536]">
                <div className="flex items-center justify-between mb-2">
                  <span className="text-xs text-slate-500">{q.label}</span>
                  <q.icon className={cn('w-4 h-4', q.color)} />
                </div>
                <div className="text-2xl font-bold text-white tabular-nums">{q.value}%</div>
                <div className="mt-2"><ProgressBar value={q.value} max={100} color="bg-accent" /></div>
              </div>
            ))}
          </div>
        </Card>
      </div>

      {/* 数据生命周期策略 */}
      <Card title="数据生命周期策略">
        <div className="space-y-3">
          {DATA_LIFECYCLE.map((item) => (
            <div
              key={item.id}
              className="flex items-start gap-3 p-4 rounded-lg bg-bg-input border border-[#1e2536]"
            >
              <div className={cn(
                'w-9 h-9 rounded-lg flex items-center justify-center shrink-0 bg-bg-card border border-[#2a3142]',
              )}>
                <item.icon className={cn('w-4 h-4', item.color)} />
              </div>
              <div className="flex-1">
                <div className="flex items-center gap-2 mb-1">
                  <span className="text-sm font-medium text-white">{item.name}</span>
                </div>
                <p className="text-xs text-slate-400">{item.description}</p>
              </div>
            </div>
          ))}
        </div>
      </Card>
    </div>
  )
}

/* ================================================================
 *  Tab 6: 集成平台
 * ================================================================ */

const integrationStatusMap: Record<string, { label: string; variant: 'success' | 'neutral' | 'danger' }> = {
  connected: { label: '已连接', variant: 'success' },
  disconnected: { label: '未连接', variant: 'neutral' },
  error: { label: '异常', variant: 'danger' },
}

function IntegrationPlatformTab() {
  const maxCalls = Math.max(...API_CALL_TREND.map((d) => d.calls))

  return (
    <div className="space-y-6">
      {/* 指标卡片 */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard label="集成应用" value={12} icon={<Plug className="w-5 h-5" />} variant="primary" />
        <StatCard label="活跃连接" value={10} icon={<CheckCircle2 className="w-5 h-5" />} variant="accent" />
        <StatCard label="今日调用" value="8,562" icon={<Activity className="w-5 h-5" />} variant="primary" trend="1,240" trendUp />
        <StatCard label="错误率" value="0.3%" icon={<AlertTriangle className="w-5 h-5" />} variant="warning" trend="0.1%" trendUp={false} />
      </div>

      {/* 集成应用列表 */}
      <Card title="集成应用">
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
          {INTEGRATION_APPS.map((app) => {
            const cfg = integrationStatusMap[app.status] ?? Object.values(integrationStatusMap)[0]
            const isError = app.status === 'error'
            return (
              <div
                key={app.id}
                className="p-4 rounded-lg bg-bg-input border border-[#1e2536] hover:border-[#2a3142] transition-colors"
              >
                <div className="flex items-center justify-between mb-3">
                  <div className={cn(
                    'w-9 h-9 rounded-lg flex items-center justify-center',
                    isError ? 'bg-status-failed/10' : 'bg-primary/10',
                  )}>
                    <Plug className={cn('w-4 h-4', isError ? 'text-status-failed' : 'text-primary')} />
                  </div>
                  <StatusBadge variant={cfg.variant} dot>{cfg.label}</StatusBadge>
                </div>
                <div className="text-sm font-medium text-white mb-0.5">{app.name}</div>
                <div className="text-xs text-slate-500 mb-2">{app.type}</div>
                <div className="flex items-center gap-1 text-[11px] text-slate-500">
                  <RefreshCw className="w-3 h-3" />
                  最后同步：{app.lastSync}
                </div>
              </div>
            )
          })}
        </div>
      </Card>

      {/* API 调用统计 */}
      <Card title="API 调用趋势（最近 7 天）">
        <div className="space-y-3">
          {API_CALL_TREND.map((d) => (
            <div key={d.day} className="flex items-center gap-3">
              <span className="text-xs text-slate-400 w-10 shrink-0">{d.day}</span>
              <div className="flex-1 h-7 rounded-lg bg-bg-input overflow-hidden relative">
                <div
                  className="h-full rounded-lg bg-gradient-to-r from-primary/60 to-primary/30 transition-all flex items-center justify-end pr-2"
                  style={{ width: `${(d.calls / maxCalls) * 100}%` }}
                >
                  <span className="text-[11px] font-mono text-white">{d.calls.toLocaleString()}</span>
                </div>
              </div>
            </div>
          ))}
        </div>
      </Card>
    </div>
  )
}

/* ================================================================
 *  Tab 7: 备份恢复
 * ================================================================ */

const backupStatusMap: Record<BackupStatus, { label: string; variant: 'success' | 'warning' | 'danger' }> = {
  success: { label: '成功', variant: 'success' },
  running: { label: '进行中', variant: 'warning' },
  failed: { label: '失败', variant: 'danger' },
}

const backupTypeMap: Record<BackupType, { label: string; variant: 'primary' | 'accent' }> = {
  full: { label: '全量', variant: 'primary' },
  incremental: { label: '增量', variant: 'accent' },
}

function BackupRecoveryTab() {
  const [restoreTarget, setRestoreTarget] = useState<BackupRecord | null>(null)

  const columns: Column<BackupRecord>[] = [
    { key: 'id', label: '备份 ID', sortable: true, render: (r) => <span className="font-mono text-xs text-primary">{r.id}</span> },
    {
      key: 'type',
      label: '类型',
      sortable: true,
      render: (r) => {
        const cfg = backupTypeMap[r.type] ?? Object.values(backupTypeMap)[0]
        return <StatusBadge variant={cfg.variant}>{cfg.label}</StatusBadge>
      },
    },
    { key: 'startTime', label: '开始时间', sortable: true, render: (r) => <span className="font-mono text-xs text-slate-400">{r.startTime}</span> },
    { key: 'duration', label: '耗时', render: (r) => <span className="text-slate-300">{r.duration}</span> },
    { key: 'size', label: '大小', sortable: true, align: 'right', render: (r) => <span className="font-mono text-slate-300">{r.size}</span> },
    {
      key: 'status',
      label: '状态',
      sortable: true,
      render: (r) => {
        const cfg = backupStatusMap[r.status] ?? Object.values(backupStatusMap)[0]
        return <StatusBadge variant={cfg.variant} dot>{cfg.label}</StatusBadge>
      },
    },
    {
      key: 'actions',
      label: '操作',
      align: 'center',
      render: (r) => (
        <button
          onClick={() => setRestoreTarget(r)}
          disabled={r.status !== 'success'}
          className="inline-flex items-center gap-1 px-2.5 py-1 rounded-md text-xs bg-accent/10 text-accent hover:bg-accent/20 transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
        >
          <RotateCcw className="w-3 h-3" />
          恢复
        </button>
      ),
    },
  ]

  return (
    <div className="space-y-6">
      {/* 指标卡片 */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard label="备份总数" value={86} icon={<HardDrive className="w-5 h-5" />} variant="primary" />
        <StatCard label="最近备份" value="2h 前" icon={<Clock className="w-5 h-5" />} variant="accent" />
        <StatCard label="备份大小" value="128 GB" icon={<Database className="w-5 h-5" />} variant="primary" />
        <StatCard label="恢复成功率" value="100%" icon={<CheckCircle2 className="w-5 h-5" />} variant="accent" />
      </div>

      {/* 备份策略 */}
      <Card title="备份策略">
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
          <div className="p-4 rounded-lg bg-bg-input border border-[#1e2536]">
            <div className="flex items-center gap-2 mb-2">
              <Clock className="w-4 h-4 text-primary" />
              <span className="text-xs text-slate-500">自动备份频率</span>
            </div>
            <span className="text-lg font-bold text-white">每日 02:00</span>
          </div>
          <div className="p-4 rounded-lg bg-bg-input border border-[#1e2536]">
            <div className="flex items-center gap-2 mb-2">
              <Archive className="w-4 h-4 text-accent" />
              <span className="text-xs text-slate-500">保留周期</span>
            </div>
            <span className="text-lg font-bold text-white">90 天</span>
          </div>
          <div className="p-4 rounded-lg bg-bg-input border border-[#1e2536]">
            <div className="flex items-center gap-2 mb-2">
              <Layers className="w-4 h-4 text-status-paused" />
              <span className="text-xs text-slate-500">备份类型</span>
            </div>
            <span className="text-lg font-bold text-white">全量 + 增量</span>
          </div>
        </div>
      </Card>

      {/* 备份记录 */}
      <Card title="备份记录">
        <DataTable
          columns={columns}
          data={BACKUP_RECORDS}
          searchKeys={['id']}
          searchPlaceholder="搜索备份 ID..."
          pageSize={8}
        />
      </Card>

      {/* 恢复测试记录 */}
      <Card title="恢复测试记录">
        <div className="space-y-3">
          {RECOVERY_TESTS.map((test) => (
            <div
              key={test.id}
              className="flex items-center gap-4 p-4 rounded-lg bg-bg-input border border-[#1e2536]"
            >
              <div className="w-9 h-9 rounded-lg bg-accent/10 flex items-center justify-center shrink-0">
                <FileCheck2 className="w-4 h-4 text-accent" />
              </div>
              <div className="flex-1 grid grid-cols-2 sm:grid-cols-5 gap-3">
                <div>
                  <div className="text-[11px] text-slate-500">测试时间</div>
                  <div className="text-xs text-slate-300 font-mono">{test.date}</div>
                </div>
                <div>
                  <div className="text-[11px] text-slate-500">目标环境</div>
                  <div className="text-xs text-slate-300">{test.target}</div>
                </div>
                <div>
                  <div className="text-[11px] text-slate-500">恢复耗时</div>
                  <div className="text-xs text-slate-300">{test.duration}</div>
                </div>
                <div>
                  <div className="text-[11px] text-slate-500">结果</div>
                  <StatusBadge variant="success" dot>{test.result}</StatusBadge>
                </div>
                <div className="col-span-2 sm:col-span-1">
                  <div className="text-[11px] text-slate-500">备注</div>
                  <div className="text-xs text-slate-400">{test.note}</div>
                </div>
              </div>
            </div>
          ))}
        </div>
      </Card>

      {/* 恢复确认弹窗 */}
      <Modal
        open={!!restoreTarget}
        onClose={() => setRestoreTarget(null)}
        title="确认恢复数据"
        size="sm"
      >
        {restoreTarget && (
          <div className="space-y-4">
            <div className="p-3 rounded-lg bg-status-paused/10 border border-status-paused/30 flex items-start gap-2">
              <AlertTriangle className="w-4 h-4 text-status-paused shrink-0 mt-0.5" />
              <p className="text-xs text-slate-300">
                恢复操作将覆盖当前数据，此操作不可撤销。请确认已做好相关备份。
              </p>
            </div>
            <div className="space-y-2">
              <div className="flex items-center justify-between text-sm">
                <span className="text-slate-500">备份 ID</span>
                <span className="font-mono text-primary">{restoreTarget.id}</span>
              </div>
              <div className="flex items-center justify-between text-sm">
                <span className="text-slate-500">备份类型</span>
                <StatusBadge variant={(backupTypeMap[restoreTarget.type] ?? Object.values(backupTypeMap)[0]).variant}>{(backupTypeMap[restoreTarget.type] ?? Object.values(backupTypeMap)[0]).label}</StatusBadge>
              </div>
              <div className="flex items-center justify-between text-sm">
                <span className="text-slate-500">备份时间</span>
                <span className="font-mono text-slate-300 text-xs">{restoreTarget.startTime}</span>
              </div>
              <div className="flex items-center justify-between text-sm">
                <span className="text-slate-500">数据大小</span>
                <span className="font-mono text-slate-300">{restoreTarget.size}</span>
              </div>
            </div>
            <div className="flex gap-3 pt-1">
              <button onClick={() => setRestoreTarget(null)} className="btn-secondary flex-1">
                取消
              </button>
              <button onClick={() => setRestoreTarget(null)} className="btn-accent flex-1">
                <RotateCcw className="w-4 h-4" />
                确认恢复
              </button>
            </div>
          </div>
        )}
      </Modal>
    </div>
  )
}

/* ================================================================
 *  主组件
 * ================================================================ */

export default function CIOAdmin() {
  const [activeTab, setActiveTab] = useState('monitor')

  const renderTabContent = () => {
    switch (activeTab) {
      case 'monitor':
        return <SystemMonitorTab />
      case 'customization':
        return <AgentCustomizationCenter />
      case 'workflow':
        return <WorkflowDesigner />
      case 'mcp':
        return <McpGovernance />
      case 'skills':
        return <SkillStudio />
      case 'audit':
        return <CustomizationAudit />
      case 'oplogs':
        return <OperationLogBoard />
      case 'evals':
        return <EvalTrendBoard />
      case 'agent':
        return <AgentLifecycleTab />
      case 'config':
        return <ConfigManagementTab />
      case 'security':
        return <SecurityControlTab />
      case 'governance':
        return <DataGovernanceTab />
      case 'integration':
        return <IntegrationPlatformTab />
      case 'backup':
        return <BackupRecoveryTab />
      default:
        return <SystemMonitorTab />
    }
  }

  return (
    <div className="space-y-6 animate-fade-in">
      <PageHeader
        title="CIO 管理后台"
        description="系统监控 · Agent 生命周期 · 配置 · 安全 · 数据治理 · 集成 · 备份"
        icon={<Settings className="w-5 h-5" />}
        actions={
          <StatusBadge variant="success" dot>
            系统健康
          </StatusBadge>
        }
      />

      {/* Tab 导航栏 */}
      <div className="flex items-center gap-1.5 overflow-x-auto pb-1 border-b border-[#1e2536]">
        {TABS.map((tab) => {
          const isActive = activeTab === tab.key
          return (
            <button
              key={tab.key}
              onClick={() => setActiveTab(tab.key)}
              className={cn(
                'inline-flex items-center gap-2 px-4 py-2.5 rounded-t-lg text-sm font-medium whitespace-nowrap transition-all border-b-2 -mb-px',
                isActive
                  ? 'text-primary border-primary bg-primary/5'
                  : 'text-slate-400 border-transparent hover:text-white hover:bg-bg-hover',
              )}
            >
              <tab.icon className="w-4 h-4" />
              {tab.label}
            </button>
          )
        })}
      </div>

      {/* Tab 内容区域 */}
      <div className="animate-fade-in">{renderTabContent()}</div>
    </div>
  )
}
