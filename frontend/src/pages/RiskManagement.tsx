import { useState } from 'react'
import {
  AlertTriangle,
  AlertCircle,
  ShieldCheck,
  ShieldAlert,
  Eye,
  Activity,
} from 'lucide-react'
import {
  PageHeader,
  Card,
  DataTable,
  StatusBadge,
  Modal,
  StatCard,
  type Column,
} from '../components/ui'
import { cn } from '../lib/utils'

/* ===================== 类型定义 ===================== */

type RiskLevel = 'high' | 'medium' | 'low'
type RiskCategory = 'finance' | 'operation' | 'compliance' | 'strategy' | 'market'
type RiskStatus = 'identified' | 'assessing' | 'mitigating' | 'monitoring' | 'closed'

interface Risk {
  id: string
  name: string
  category: RiskCategory
  impact: RiskLevel
  probability: RiskLevel
  score: number
  status: RiskStatus
  owner: string
  description: string
  mitigation: string
  contingency: string
}

/* ===================== 状态配置 ===================== */

const LEVEL_NUM: Record<RiskLevel, number> = { high: 3, medium: 2, low: 1 }
const LEVEL_LABEL: Record<RiskLevel, string> = { high: '高', medium: '中', low: '低' }
const LEVEL_VARIANT: Record<RiskLevel, 'danger' | 'warning' | 'success'> = {
  high: 'danger',
  medium: 'warning',
  low: 'success',
}

const CATEGORY_LABEL: Record<RiskCategory, string> = {
  finance: '财务',
  operation: '运营',
  compliance: '合规',
  strategy: '战略',
  market: '市场',
}

const STATUS_CONFIG: Record<
  RiskStatus,
  { variant: 'neutral' | 'info' | 'warning' | 'primary' | 'success'; label: string }
> = {
  identified: { variant: 'neutral', label: '识别' },
  assessing: { variant: 'info', label: '评估' },
  mitigating: { variant: 'warning', label: '缓解' },
  monitoring: { variant: 'primary', label: '监控' },
  closed: { variant: 'success', label: '关闭' },
}

const scoreLevel = (score: number): RiskLevel =>
  score >= 7 ? 'high' : score >= 4 ? 'medium' : 'low'

const MATRIX_CELL_STYLES: Record<RiskLevel, string> = {
  high: 'bg-status-failed/15 border-status-failed/40 text-status-failed',
  medium: 'bg-status-paused/15 border-status-paused/40 text-status-paused',
  low: 'bg-accent/15 border-accent/40 text-accent',
}

/* ===================== Mock 数据 ===================== */

const mockRisks: Risk[] = [
  {
    id: 'R-001',
    name: '核心客户流失风险',
    category: 'market',
    impact: 'high',
    probability: 'medium',
    score: 6,
    status: 'mitigating',
    owner: '王强',
    description: '前三大客户贡献营收占比超过 45%，若任一客户流失将对营收造成重大冲击。',
    mitigation: '建立大客户专属服务团队，制定客户成功计划，每月高层拜访，提升客户黏性。',
    contingency: '储备替代客户资源，建立 3 个月营收缓冲资金，确保现金流安全。',
  },
  {
    id: 'R-002',
    name: '数据安全合规风险',
    category: 'compliance',
    impact: 'high',
    probability: 'high',
    score: 9,
    status: 'assessing',
    owner: '李芳',
    description: '《数据安全法》《个人信息保护法》合规要求趋严，现有数据处理流程存在合规缺口。',
    mitigation: '启动数据合规专项审计，建立数据分类分级体系，部署数据脱敏与访问审计系统。',
    contingency: '制定数据泄露应急响应预案，配备 24 小时安全值守，与专业律所建立应急合作。',
  },
  {
    id: 'R-003',
    name: '供应链中断风险',
    category: 'operation',
    impact: 'high',
    probability: 'low',
    score: 3,
    status: 'monitoring',
    owner: '孙伟',
    description: '核心供应商集中，若供应商出现经营异常将影响产品交付。',
    mitigation: '开发备选供应商，关键物料保持双源供应，建立安全库存。',
    contingency: '启用备选供应商，调整交付计划，必要时启用现货采购。',
  },
  {
    id: 'R-004',
    name: '汇率波动风险',
    category: 'finance',
    impact: 'medium',
    probability: 'high',
    score: 6,
    status: 'mitigating',
    owner: '郑凯',
    description: '海外业务占比上升，汇率波动可能影响利润率。',
    mitigation: '开展远期结售汇对冲，约定汇率锁定条款，分散结算币种。',
    contingency: '动用汇率风险准备金，调整海外定价策略，转移成本压力。',
  },
  {
    id: 'R-005',
    name: '关键人才流失风险',
    category: 'operation',
    impact: 'medium',
    probability: 'medium',
    score: 4,
    status: 'mitigating',
    owner: '吴磊',
    description: '核心技术岗位人员流失可能影响项目交付与产品研发。',
    mitigation: '完善股权激励与晋升通道，建立知识库与文档体系，降低单点依赖。',
    contingency: '建立人才储备池，与猎头保持合作，关键岗位设置 AB 角。',
  },
  {
    id: 'R-006',
    name: '项目交付延期风险',
    category: 'operation',
    impact: 'medium',
    probability: 'high',
    score: 6,
    status: 'monitoring',
    owner: '王强',
    description: '大型定制项目易出现需求变更与交付延期，影响客户满意度。',
    mitigation: '推行敏捷交付与里程碑管理，加强需求评审与变更控制。',
    contingency: '调配应急资源，与客户协商调整范围，必要时启动外包补充。',
  },
  {
    id: 'R-007',
    name: '竞争对手价格战风险',
    category: 'market',
    impact: 'medium',
    probability: 'medium',
    score: 4,
    status: 'assessing',
    owner: '陈浩',
    description: '行业竞争加剧，竞品可能通过价格战抢占市场份额。',
    mitigation: '强化产品差异化与增值服务，提升客户切换成本，优化成本结构。',
    contingency: '制定阶梯定价与组合套餐，必要时启动阶段性促销应对。',
  },
  {
    id: 'R-008',
    name: '现金流断裂风险',
    category: 'finance',
    impact: 'high',
    probability: 'medium',
    score: 6,
    status: 'mitigating',
    owner: '郑凯',
    description: '应收账款周期拉长，若回款不及时可能影响现金流安全。',
    mitigation: '加强应收账款管理，推行账龄预警，优化客户信用评级。',
    contingency: '申请银行授信额度，建立 3 个月运营资金储备，启动保理融资。',
  },
  {
    id: 'R-009',
    name: '数据泄露风险',
    category: 'compliance',
    impact: 'high',
    probability: 'low',
    score: 3,
    status: 'monitoring',
    owner: '李芳',
    description: '客户敏感数据存在被攻击或内部泄露的潜在风险。',
    mitigation: '部署零信任安全架构，加强权限最小化管控，定期安全演练。',
    contingency: '启动应急响应预案，第一时间阻断访问，按规定上报监管部门。',
  },
  {
    id: 'R-010',
    name: '战略转型不及预期风险',
    category: 'strategy',
    impact: 'medium',
    probability: 'low',
    score: 2,
    status: 'identified',
    owner: '高翔',
    description: '向 AI 智能化方向转型，若技术路线或市场接受度不及预期，可能影响投入回报。',
    mitigation: '分阶段投入与验证，建立里程碑评估机制，保留战略调整空间。',
    contingency: '制定退出或转向方案，控制沉没成本，确保核心业务稳定。',
  },
  {
    id: 'R-011',
    name: '客户满意度下降风险',
    category: 'market',
    impact: 'low',
    probability: 'medium',
    score: 2,
    status: 'monitoring',
    owner: '黄丽',
    description: '服务响应速度下降可能导致客户满意度下滑。',
    mitigation: '建立客户满意度监测体系，定期回访，优化服务流程。',
    contingency: '启动专项回访计划，针对性解决客户痛点，恢复满意度。',
  },
  {
    id: 'R-012',
    name: '供应商违约风险',
    category: 'operation',
    impact: 'low',
    probability: 'low',
    score: 1,
    status: 'closed',
    owner: '孙伟',
    description: '次要供应商可能出现交付违约，已通过双源供应化解。',
    mitigation: '已完成备选供应商引入，签订违约赔偿条款。',
    contingency: '直接切换至备选供应商，影响可控。',
  },
]

/* ===================== 主组件 ===================== */

export default function RiskManagement() {
  const [selected, setSelected] = useState<Risk | null>(null)

  const impactRows: RiskLevel[] = ['high', 'medium', 'low']
  const probCols: RiskLevel[] = ['low', 'medium', 'high']

  const columns: Column<Risk>[] = [
    {
      key: 'id',
      label: '风险编号',
      sortable: true,
      render: (r) => <span className="font-mono text-xs text-primary">{r.id}</span>,
    },
    {
      key: 'name',
      label: '风险名称',
      sortable: true,
      render: (r) => <span className="font-medium text-white">{r.name}</span>,
    },
    {
      key: 'category',
      label: '类别',
      align: 'center',
      sortable: true,
      render: (r) => <StatusBadge variant="neutral">{CATEGORY_LABEL[r.category]}</StatusBadge>,
    },
    {
      key: 'impact',
      label: '影响等级',
      align: 'center',
      sortable: true,
      render: (r) => <StatusBadge variant={LEVEL_VARIANT[r.impact]}>{LEVEL_LABEL[r.impact]}</StatusBadge>,
    },
    {
      key: 'probability',
      label: '概率等级',
      align: 'center',
      sortable: true,
      render: (r) => <StatusBadge variant={LEVEL_VARIANT[r.probability]}>{LEVEL_LABEL[r.probability]}</StatusBadge>,
    },
    {
      key: 'score',
      label: '风险评分',
      align: 'center',
      sortable: true,
      render: (r) => (
        <span
          className={cn(
            'inline-flex items-center justify-center w-7 h-7 rounded-md text-xs font-bold tabular-nums',
            MATRIX_CELL_STYLES[scoreLevel(r.score)],
          )}
        >
          {r.score}
        </span>
      ),
    },
    {
      key: 'status',
      label: '状态',
      align: 'center',
      sortable: true,
      render: (r) => (
        <StatusBadge variant={STATUS_CONFIG[r.status].variant} dot>
          {STATUS_CONFIG[r.status].label}
        </StatusBadge>
      ),
    },
    { key: 'owner', label: '负责人', render: (r) => <span className="text-slate-300">{r.owner}</span> },
    {
      key: 'actions',
      label: '操作',
      align: 'center',
      render: (r) => (
        <button
          onClick={() => setSelected(r)}
          className="inline-flex items-center gap-1 px-2.5 py-1 rounded-md bg-primary/10 text-primary hover:bg-primary/20 text-xs font-medium transition-colors"
        >
          <Eye className="w-3.5 h-3.5" /> 查看详情
        </button>
      ),
    },
  ]

  return (
    <div className="space-y-6 animate-fade-in">
      <PageHeader
        title="风险管理"
        description="风险识别、评估、缓解与持续监控"
        icon={<AlertTriangle className="w-5 h-5" />}
      />

      {/* 统计卡片 */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard label="总风险数" value={28} icon={<ShieldAlert className="w-5 h-5" />} trend="全量台账" variant="primary" />
        <StatCard label="高风险" value={3} icon={<AlertCircle className="w-5 h-5" />} trend="需立即处置" variant="danger" />
        <StatCard label="中风险" value={9} icon={<AlertTriangle className="w-5 h-5" />} trend="持续关注" variant="warning" />
        <StatCard label="低风险" value={16} icon={<ShieldCheck className="w-5 h-5" />} trend="可控范围" trendUp variant="accent" />
      </div>

      {/* 主体：风险矩阵 + 风险列表 */}
      <div className="grid grid-cols-1 xl:grid-cols-3 gap-4">
        {/* 左侧：风险矩阵 */}
        <div className="xl:col-span-1">
          <Card title="风险矩阵" action={<Activity className="w-4 h-4 text-slate-500" />}>
            <div className="space-y-1.5">
              {/* 表头：概率轴 */}
              <div className="grid grid-cols-[60px_repeat(3,1fr)] gap-1.5">
                <div />
                {probCols.map((p) => (
                  <div key={p} className="text-center text-[11px] text-slate-500 pb-1">
                    概率{LEVEL_LABEL[p]}
                  </div>
                ))}
              </div>
              {/* 数据行：影响轴 */}
              {impactRows.map((imp) => (
                <div key={imp} className="grid grid-cols-[60px_repeat(3,1fr)] gap-1.5">
                  <div className="flex items-center justify-end text-[11px] text-slate-500 pr-1">
                    影响{LEVEL_LABEL[imp]}
                  </div>
                  {probCols.map((prob) => {
                    const count = mockRisks.filter(
                      (r) => r.impact === imp && r.probability === prob,
                    ).length
                    const score = LEVEL_NUM[imp] * LEVEL_NUM[prob]
                    const lvl = scoreLevel(score)
                    return (
                      <div
                        key={prob}
                        className={cn(
                          'aspect-square rounded-lg border flex flex-col items-center justify-center transition-all cursor-pointer hover:scale-105',
                          MATRIX_CELL_STYLES[lvl],
                        )}
                      >
                        <span className="text-xl font-bold tabular-nums">{count}</span>
                        <span className="text-[10px] opacity-80">评分 {score}</span>
                      </div>
                    )
                  })}
                </div>
              ))}
            </div>

            {/* 图例 */}
            <div className="mt-4 pt-3 border-t border-[#1e2536] space-y-1.5">
              <div className="flex items-center gap-2 text-xs">
                <span className="w-3 h-3 rounded bg-status-failed/30 border border-status-failed/40" />
                <span className="text-slate-400">高风险（评分 7-9）</span>
              </div>
              <div className="flex items-center gap-2 text-xs">
                <span className="w-3 h-3 rounded bg-status-paused/30 border border-status-paused/40" />
                <span className="text-slate-400">中风险（评分 4-6）</span>
              </div>
              <div className="flex items-center gap-2 text-xs">
                <span className="w-3 h-3 rounded bg-accent/30 border border-accent/40" />
                <span className="text-slate-400">低风险（评分 1-3）</span>
              </div>
            </div>
          </Card>
        </div>

        {/* 右侧：风险列表 */}
        <div className="xl:col-span-2">
          <Card title="风险列表">
            <DataTable
              columns={columns}
              data={mockRisks}
              searchKeys={['name']}
              searchPlaceholder="搜索风险名称..."
              pageSize={8}
            />
          </Card>
        </div>
      </div>

      {/* 风险详情弹窗 */}
      <Modal open={!!selected} onClose={() => setSelected(null)} title="风险详情" size="lg">
        {selected && <RiskDetail risk={selected} />}
      </Modal>
    </div>
  )
}

/* ===================== 风险详情 ===================== */

function RiskDetail({ risk }: { risk: Risk }) {
  const lvl = scoreLevel(risk.score)
  const rows: { label: string; value: string }[] = [
    { label: '风险编号', value: risk.id },
    { label: '风险名称', value: risk.name },
    { label: '风险类别', value: CATEGORY_LABEL[risk.category] },
    { label: '影响等级', value: LEVEL_LABEL[risk.impact] },
    { label: '概率等级', value: LEVEL_LABEL[risk.probability] },
    { label: '风险评分', value: `${risk.score} 分` },
    { label: '当前状态', value: STATUS_CONFIG[risk.status].label },
    { label: '负责人', value: risk.owner },
  ]

  return (
    <div className="space-y-5">
      {/* 风险等级与评分 */}
      <div className="flex items-center gap-3">
        <div
          className={cn(
            'w-12 h-12 rounded-xl border flex items-center justify-center text-lg font-bold tabular-nums',
            MATRIX_CELL_STYLES[lvl],
          )}
        >
          {risk.score}
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          <StatusBadge variant={LEVEL_VARIANT[lvl]} dot>
            {lvl === 'high' ? '高风险' : lvl === 'medium' ? '中风险' : '低风险'}
          </StatusBadge>
          <StatusBadge variant={STATUS_CONFIG[risk.status].variant} dot>
            {STATUS_CONFIG[risk.status].label}
          </StatusBadge>
          <StatusBadge variant="neutral">{CATEGORY_LABEL[risk.category]}</StatusBadge>
        </div>
      </div>

      {/* 基本信息 */}
      <div className="grid grid-cols-2 gap-x-6 gap-y-4">
        {rows.map((r) => (
          <div key={r.label} className="flex flex-col gap-1">
            <span className="text-xs text-slate-500">{r.label}</span>
            <span className="text-sm text-white font-medium">{r.value}</span>
          </div>
        ))}
      </div>

      {/* 风险描述 */}
      <div className="rounded-lg bg-bg-input border border-[#1e2536] p-4">
        <p className="text-xs text-slate-500 mb-1.5">风险描述</p>
        <p className="text-sm text-slate-300 leading-relaxed">{risk.description}</p>
      </div>

      {/* 缓解措施 */}
      <div className="rounded-lg bg-accent/5 border border-accent/20 p-4">
        <p className="text-xs text-accent mb-1.5 font-medium">缓解措施</p>
        <p className="text-sm text-slate-300 leading-relaxed">{risk.mitigation}</p>
      </div>

      {/* 应急预案 */}
      <div className="rounded-lg bg-status-paused/5 border border-status-paused/20 p-4">
        <p className="text-xs text-status-paused mb-1.5 font-medium">应急预案</p>
        <p className="text-sm text-slate-300 leading-relaxed">{risk.contingency}</p>
      </div>
    </div>
  )
}
