import { useState, useRef, useEffect } from 'react'
import {
  BookOpen,
  FileText,
  CheckCircle2,
  Clock,
  Search as SearchIcon,
  Upload,
  UploadCloud,
  FolderTree,
  MoreHorizontal,
  ShieldCheck,
  XCircle,
  File as FileIcon,
  FileType2,
  FileSpreadsheet,
  Presentation,
  Image as ImageIcon,
  Database,
  Share2,
  Network,
  ChevronDown,
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

/* ===================== 类型定义 ===================== */

type DocFormat = 'PDF' | 'Word' | 'Excel' | 'PPT' | '图片'
type DocStatus = 'pending' | 'published' | 'indexed' | 'rejected'

interface KnowledgeDoc {
  id: string
  title: string
  format: DocFormat
  category: string
  size: string
  uploadedAt: string
  status: DocStatus
  uploader: string
  description: string
}

interface CategoryItem {
  name: string
  count: number
}

interface RetrievalResult {
  docName: string
  category: string
  snippet: string
  highlight: string
  score: number
}

/* ===================== 状态与格式配置 ===================== */

const FORMAT_CONFIG: Record<
  DocFormat,
  { variant: 'danger' | 'primary' | 'accent' | 'warning' | 'neutral'; icon: typeof FileIcon }
> = {
  PDF: { variant: 'danger', icon: FileIcon },
  Word: { variant: 'primary', icon: FileType2 },
  Excel: { variant: 'accent', icon: FileSpreadsheet },
  PPT: { variant: 'warning', icon: Presentation },
  图片: { variant: 'neutral', icon: ImageIcon },
}

const STATUS_CONFIG: Record<
  DocStatus,
  { variant: 'warning' | 'success' | 'info' | 'danger'; label: string }
> = {
  pending: { variant: 'warning', label: '待审核' },
  published: { variant: 'success', label: '已发布' },
  indexed: { variant: 'info', label: '已索引' },
  rejected: { variant: 'danger', label: '已拒绝' },
}

const PERMISSION_LEVELS = ['公开', '部门', '管理层'] as const
const INDEX_STRATEGIES = ['自动分块', '手动分块'] as const

/* ===================== Mock 数据 ===================== */

const CATEGORIES: CategoryItem[] = [
  { name: '公司制度', count: 38 },
  { name: '技术文档', count: 52 },
  { name: '财务报告', count: 34 },
  { name: '法律合规', count: 22 },
  { name: '产品手册', count: 45 },
  { name: '培训资料', count: 28 },
  { name: '市场研究', count: 31 },
  { name: '项目档案', count: 36 },
]

const initialDocs: KnowledgeDoc[] = [
  { id: '1', title: '2026年度员工手册（修订版）', format: 'PDF', category: '公司制度', size: '4.2 MB', uploadedAt: '2026-07-25 09:30', status: 'published', uploader: '张明', description: '涵盖考勤、薪酬、绩效、福利等全员适用制度的最新修订版本。' },
  { id: '2', title: '微服务架构设计规范 V2', format: 'Word', category: '技术文档', size: '1.8 MB', uploadedAt: '2026-07-24 14:12', status: 'indexed', uploader: '李芳', description: '规定微服务拆分原则、服务间通信、数据一致性及部署策略。' },
  { id: '3', title: 'Q2 财务经营分析报告', format: 'Excel', category: '财务报告', size: '3.5 MB', uploadedAt: '2026-07-23 16:45', status: 'published', uploader: '王强', description: '包含利润表、资产负债表及现金流量表的季度分析数据。' },
  { id: '4', title: '数据安全与隐私合规白皮书', format: 'PDF', category: '法律合规', size: '5.6 MB', uploadedAt: '2026-07-22 11:20', status: 'pending', uploader: '陈浩', description: '围绕《数据安全法》《个人信息保护法》的合规要点梳理。' },
  { id: '5', title: 'ERP 产品操作手册 v3.0', format: 'PDF', category: '产品手册', size: '8.1 MB', uploadedAt: '2026-07-21 10:05', status: 'published', uploader: '孙伟', description: '面向终端用户的 ERP 全模块操作指引，含截图与流程说明。' },
  { id: '6', title: '新员工入职培训课件', format: 'PPT', category: '培训资料', size: '12.4 MB', uploadedAt: '2026-07-20 13:50', status: 'published', uploader: '周婷', description: '企业文化、组织架构、系统操作等入职培训演示文稿。' },
  { id: '7', title: '行业数字化转型市场趋势分析', format: 'PDF', category: '市场研究', size: '2.3 MB', uploadedAt: '2026-07-19 15:30', status: 'indexed', uploader: '吴磊', description: '2026 年企业数字化转型的行业趋势与竞争格局分析。' },
  { id: '8', title: '智慧供应链项目结项档案', format: 'Word', category: '项目档案', size: '1.2 MB', uploadedAt: '2026-07-18 09:15', status: 'published', uploader: '郑凯', description: '智慧供应链项目的立项、过程、交付与验收全过程记录。' },
  { id: '9', title: 'API 接口开发与对接文档', format: 'Word', category: '技术文档', size: '920 KB', uploadedAt: '2026-07-17 17:40', status: 'published', uploader: '高翔', description: '统一 API 设计规范、鉴权方式、错误码及对接示例。' },
  { id: '10', title: '差旅费用报销管理制度', format: 'PDF', category: '公司制度', size: '680 KB', uploadedAt: '2026-07-16 11:00', status: 'pending', uploader: '黄丽', description: '差旅标准、审批流程及报销凭证要求的最新规定。' },
  { id: '11', title: '年度审计报告关键图表', format: '图片', category: '财务报告', size: '4.7 MB', uploadedAt: '2026-07-15 14:25', status: 'rejected', uploader: '林峰', description: '审计报告中的关键财务图表，因清晰度不足被驳回。' },
  { id: '12', title: '客户案例研究合集（2025）', format: 'PDF', category: '市场研究', size: '6.8 MB', uploadedAt: '2026-07-14 10:30', status: 'indexed', uploader: '张明', description: '15 个标杆客户实施案例的完整复盘与成效数据。' },
  { id: '13', title: '敏捷开发流程与最佳实践指南', format: 'PPT', category: '培训资料', size: '5.2 MB', uploadedAt: '2026-07-13 16:00', status: 'published', uploader: '李芳', description: 'Scrum 与 Kanban 流程详解及团队落地最佳实践。' },
  { id: '14', title: '商业秘密保护协议模板', format: 'Word', category: '法律合规', size: '450 KB', uploadedAt: '2026-07-12 09:45', status: 'pending', uploader: '陈浩', description: '适用于核心岗位的商业秘密保护协议标准模板。' },
]

const RETRIEVAL_RESULTS: RetrievalResult[] = [
  {
    docName: '微服务架构设计规范 V2',
    category: '技术文档',
    snippet: '采用微服务架构，将单体应用拆分为独立部署的服务，每个服务负责单一业务能力，通过 API 网关统一对外提供服务，并使用服务注册中心实现动态发现与负载均衡。',
    highlight: '微服务',
    score: 94,
  },
  {
    docName: '2026年度员工手册（修订版）',
    category: '公司制度',
    snippet: '公司实行弹性工作制，员工可在规定时间范围内灵活安排工作时间，但需保证每日核心工时（10:00-16:00）在岗，并通过系统完成每日打卡记录。',
    highlight: '工作时间',
    score: 89,
  },
  {
    docName: '客户案例研究合集（2025）',
    category: '市场研究',
    snippet: '该客户通过部署数字化大脑系统，实现了采购、库存、财务全链路流程优化，订单处理效率提升 62%，库存周转率提高 1.8 倍，年节约运营成本超 800 万元。',
    highlight: '流程优化',
    score: 82,
  },
]

/* ===================== 辅助函数 ===================== */

/** 高亮关键词片段 */
function HighlightText({ text, keyword }: { text: string; keyword: string }) {
  if (!keyword) return <>{text}</>
  const parts = text.split(new RegExp(`(${keyword})`, 'gi'))
  return (
    <>
      {parts.map((part, i) =>
        part.toLowerCase() === keyword.toLowerCase() ? (
          <mark key={i} className="bg-primary/20 text-primary rounded px-0.5">
            {part}
          </mark>
        ) : (
          <span key={i}>{part}</span>
        ),
      )}
    </>
  )
}

/* ===================== 主组件 ===================== */

export default function KnowledgeBase() {
  const [docs, setDocs] = useState<KnowledgeDoc[]>(initialDocs)
  const [selectedCategory, setSelectedCategory] = useState<string | null>(null)

  const [searchModalOpen, setSearchModalOpen] = useState(false)
  const [uploadModalOpen, setUploadModalOpen] = useState(false)
  const [reviewDoc, setReviewDoc] = useState<KnowledgeDoc | null>(null)

  const [retrievalQuery, setRetrievalQuery] = useState('微服务架构如何拆分')
  const [retrievalResults, setRetrievalResults] = useState<RetrievalResult[]>(RETRIEVAL_RESULTS)
  const [retrievalLoading, setRetrievalLoading] = useState(false)

  // 上传表单状态
  const [uploadForm, setUploadForm] = useState({
    title: '',
    category: CATEGORIES[0].name,
    permission: PERMISSION_LEVELS[0],
    description: '',
  })

  // 审核表单状态
  const [indexStrategy, setIndexStrategy] = useState<(typeof INDEX_STRATEGIES)[number]>('自动分块')

  // 行操作下拉菜单
  const [openMenuId, setOpenMenuId] = useState<string | null>(null)
  const menuRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const handleClick = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setOpenMenuId(null)
      }
    }
    document.addEventListener('mousedown', handleClick)
    return () => document.removeEventListener('mousedown', handleClick)
  }, [])

  // 按分类过滤
  const filteredDocs = selectedCategory
    ? docs.filter((d) => d.category === selectedCategory)
    : docs

  const totalDocs = CATEGORIES.reduce((sum, c) => sum + c.count, 0)

  const handleSearch = () => {
    setRetrievalLoading(true)
    setTimeout(() => {
      setRetrievalResults(RETRIEVAL_RESULTS)
      setRetrievalLoading(false)
    }, 600)
  }

  const handleApprove = () => {
    if (!reviewDoc) return
    setDocs((prev) =>
      prev.map((d) => (d.id === reviewDoc.id ? { ...d, status: 'published' } : d)),
    )
    setReviewDoc(null)
    setIndexStrategy('自动分块')
  }

  const handleReject = () => {
    if (!reviewDoc) return
    setDocs((prev) =>
      prev.map((d) => (d.id === reviewDoc.id ? { ...d, status: 'rejected' } : d)),
    )
    setReviewDoc(null)
  }

  const handleUpload = () => {
    setUploadModalOpen(false)
    setUploadForm({
      title: '',
      category: CATEGORIES[0].name,
      permission: PERMISSION_LEVELS[0],
      description: '',
    })
  }

  const columns: Column<KnowledgeDoc>[] = [
    {
      key: 'title',
      label: '文档名称',
      sortable: true,
      render: (r) => (
        <div className="flex items-center gap-2.5">
          <span className="text-slate-500">
            {(() => {
              const Icon = (FORMAT_CONFIG[r.format] ?? Object.values(FORMAT_CONFIG)[0]).icon
              return <Icon className="w-4 h-4" />
            })()}
          </span>
          <span className="font-medium text-white">{r.title}</span>
        </div>
      ),
    },
    {
      key: 'format',
      label: '格式',
      align: 'center',
      sortable: true,
      render: (r) => (
        <StatusBadge variant={(FORMAT_CONFIG[r.format] ?? Object.values(FORMAT_CONFIG)[0]).variant}>{r.format}</StatusBadge>
      ),
    },
    {
      key: 'category',
      label: '分类',
      sortable: true,
      render: (r) => <span className="text-slate-300 text-xs">{r.category}</span>,
    },
    {
      key: 'size',
      label: '大小',
      align: 'right',
      sortable: true,
      render: (r) => <span className="text-slate-400 text-xs tabular-nums">{r.size}</span>,
    },
    {
      key: 'uploadedAt',
      label: '上传时间',
      sortable: true,
      render: (r) => <span className="text-slate-400 text-xs">{r.uploadedAt}</span>,
    },
    {
      key: 'status',
      label: '状态',
      align: 'center',
      sortable: true,
      render: (r) => (
        <StatusBadge variant={(STATUS_CONFIG[r.status] ?? Object.values(STATUS_CONFIG)[0]).variant} dot>
          {(STATUS_CONFIG[r.status] ?? Object.values(STATUS_CONFIG)[0]).label}
        </StatusBadge>
      ),
    },
    {
      key: 'actions',
      label: '操作',
      align: 'center',
      render: (r) => (
        <div className="flex items-center justify-center gap-1.5">
          <button
            onClick={() => setReviewDoc(r)}
            className="inline-flex items-center gap-1 px-2.5 py-1 rounded-md bg-primary/10 text-primary hover:bg-primary/20 text-xs font-medium transition-colors"
          >
            <SearchIcon className="w-3.5 h-3.5" /> 检索预览
          </button>
          <div className="relative" ref={openMenuId === r.id ? menuRef : undefined}>
            <button
              onClick={() => setOpenMenuId((prev) => (prev === r.id ? null : r.id))}
              className="inline-flex items-center gap-1 px-2 py-1 rounded-md bg-bg-input border border-[#2a3142] text-slate-400 hover:text-white text-xs font-medium transition-colors"
            >
              <MoreHorizontal className="w-3.5 h-3.5" />
            </button>
            {openMenuId === r.id && (
              <div className="absolute right-0 top-full mt-1 z-20 w-32 rounded-lg bg-bg-card border border-[#2a3142] shadow-xl py-1">
                <button
                  onClick={() => {
                    setReviewDoc(r)
                    setOpenMenuId(null)
                  }}
                  className="w-full flex items-center gap-2 px-3 py-1.5 text-xs text-slate-300 hover:bg-bg-hover hover:text-white transition-colors"
                >
                  <ShieldCheck className="w-3.5 h-3.5" /> 审核
                </button>
                <button
                  onClick={() => setOpenMenuId(null)}
                  className="w-full flex items-center gap-2 px-3 py-1.5 text-xs text-slate-300 hover:bg-bg-hover hover:text-white transition-colors"
                >
                  <Database className="w-3.5 h-3.5" /> 重新索引
                </button>
                <button
                  onClick={() => setOpenMenuId(null)}
                  className="w-full flex items-center gap-2 px-3 py-1.5 text-xs text-status-failed hover:bg-status-failed/10 transition-colors"
                >
                  <XCircle className="w-3.5 h-3.5" /> 删除
                </button>
              </div>
            )}
          </div>
        </div>
      ),
    },
  ]

  return (
    <div className="space-y-6 animate-fade-in">
      <PageHeader
        title="RAG 知识库"
        description="企业知识管理 · 多格式文档 · 混合检索"
        icon={<BookOpen className="w-5 h-5" />}
        actions={
          <>
            <button onClick={() => setUploadModalOpen(true)} className="btn-secondary">
              <Upload className="w-4 h-4" /> 上传文档
            </button>
            <button onClick={() => setSearchModalOpen(true)} className="btn-primary">
              <SearchIcon className="w-4 h-4" /> 知识检索
            </button>
          </>
        }
      />

      {/* 统计卡片 */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard
          label="文档总数"
          value={286}
          icon={<FileText className="w-5 h-5" />}
          trend="本周新增 12"
          trendUp
          variant="primary"
        />
        <StatCard
          label="已索引"
          value={258}
          icon={<Database className="w-5 h-5" />}
          trend="索引率 90.2%"
          trendUp
          variant="accent"
        />
        <StatCard
          label="待审核"
          value={5}
          icon={<Clock className="w-5 h-5" />}
          trend="需及时处理"
          variant="warning"
        />
        <StatCard
          label="检索次数今日"
          value="1,256"
          icon={<SearchIcon className="w-5 h-5" />}
          trend="较昨日 +18.4%"
          trendUp
          variant="primary"
        />
      </div>

      {/* 左侧分类树 + 右侧文档列表 */}
      <div className="grid grid-cols-1 lg:grid-cols-4 gap-4">
        {/* 分类树 */}
        <Card title="知识分类" className="lg:col-span-1 h-fit">
          <div className="space-y-1">
            <button
              onClick={() => setSelectedCategory(null)}
              className={cn(
                'w-full flex items-center justify-between px-3 py-2 rounded-lg text-sm transition-colors',
                selectedCategory === null
                  ? 'bg-primary/10 text-primary font-medium'
                  : 'text-slate-300 hover:bg-bg-hover',
              )}
            >
              <span className="flex items-center gap-2">
                <FolderTree className="w-4 h-4" /> 全部分类
              </span>
              <span className="text-xs text-slate-500 tabular-nums">{totalDocs}</span>
            </button>
            {CATEGORIES.map((cat) => (
              <button
                key={cat.name}
                onClick={() => setSelectedCategory(cat.name)}
                className={cn(
                  'w-full flex items-center justify-between px-3 py-2 rounded-lg text-sm transition-colors',
                  selectedCategory === cat.name
                    ? 'bg-primary/10 text-primary font-medium'
                    : 'text-slate-300 hover:bg-bg-hover',
                )}
              >
                <span className="flex items-center gap-2">
                  <FileText className="w-4 h-4 opacity-60" /> {cat.name}
                </span>
                <span className="text-xs text-slate-500 tabular-nums">{cat.count}</span>
              </button>
            ))}
          </div>
        </Card>

        {/* 文档列表 */}
        <Card
          className="lg:col-span-3"
          title={`文档列表${selectedCategory ? ` · ${selectedCategory}` : ''}`}
          action={
            <span className="text-xs text-slate-500">
              共 {filteredDocs.length} 篇文档
            </span>
          }
        >
          <DataTable
            columns={columns}
            data={filteredDocs}
            searchKeys={['title']}
            searchPlaceholder="搜索文档名称..."
            pageSize={10}
          />
        </Card>
      </div>

      {/* 知识检索 Modal */}
      <Modal
        open={searchModalOpen}
        onClose={() => setSearchModalOpen(false)}
        title="知识检索"
        size="lg"
      >
        <div className="space-y-5">
          {/* 检索输入框 */}
          <div className="flex gap-2">
            <div className="relative flex-1">
              <SearchIcon className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-600" />
              <input
                type="text"
                value={retrievalQuery}
                onChange={(e) => setRetrievalQuery(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && handleSearch()}
                placeholder="输入您的问题，如：微服务架构如何拆分？"
                className="w-full pl-9 pr-3 py-2.5 rounded-lg bg-bg-input border border-[#2a3142] text-sm text-white placeholder:text-slate-600 focus:outline-none focus:border-primary/40 transition-colors"
              />
            </div>
            <button onClick={handleSearch} className="btn-primary" disabled={retrievalLoading}>
              <SearchIcon className="w-4 h-4" /> {retrievalLoading ? '检索中...' : '检索'}
            </button>
          </div>

          {/* 检索结果 */}
          <div className="space-y-3">
            {retrievalLoading ? (
              <div className="py-12 text-center">
                <div className="inline-flex items-center gap-2 text-slate-500 text-sm">
                  <Database className="w-4 h-4 animate-pulse" /> 正在向量检索与知识图谱匹配...
                </div>
              </div>
            ) : (
              retrievalResults.map((result, idx) => (
                <div
                  key={idx}
                  className="rounded-xl bg-bg-input border border-[#1e2536] p-4 hover:border-primary/30 transition-colors"
                >
                  <div className="flex items-start justify-between gap-3 mb-2">
                    <div className="flex items-center gap-2">
                      <FileText className="w-4 h-4 text-primary" />
                      <span className="text-sm font-semibold text-white">{result.docName}</span>
                      <StatusBadge variant="info">{result.category}</StatusBadge>
                    </div>
                    <span className="text-xs text-slate-500 shrink-0">来源分类</span>
                  </div>
                  <p className="text-sm text-slate-300 leading-relaxed mb-3">
                    <HighlightText text={result.snippet} keyword={result.highlight} />
                  </p>
                  <div className="flex items-center gap-3">
                    <span className="text-xs text-slate-500 shrink-0">相似度</span>
                    <div className="flex-1 h-2 rounded-full bg-bg-base overflow-hidden">
                      <div
                        className={cn(
                          'h-full rounded-full transition-all',
                          result.score >= 90
                            ? 'bg-accent'
                            : result.score >= 80
                              ? 'bg-primary'
                              : 'bg-status-paused',
                        )}
                        style={{ width: `${result.score}%` }}
                      />
                    </div>
                    <span
                      className={cn(
                        'text-xs font-bold tabular-nums shrink-0',
                        result.score >= 90 ? 'text-accent' : result.score >= 80 ? 'text-primary' : 'text-status-paused',
                      )}
                    >
                      {result.score}%
                    </span>
                  </div>
                </div>
              ))
            )}
          </div>

          {/* 检索模式标签 */}
          <div className="flex items-center gap-2 pt-3 border-t border-[#1e2536]">
            <span className="text-xs text-slate-500">检索模式：</span>
            <StatusBadge variant="primary">
              <Share2 className="w-3 h-3" /> 向量检索
            </StatusBadge>
            <StatusBadge variant="accent">
              <SearchIcon className="w-3 h-3" /> 关键词检索
            </StatusBadge>
            <StatusBadge variant="info">
              <Network className="w-3 h-3" /> 知识图谱
            </StatusBadge>
          </div>
        </div>
      </Modal>

      {/* 文档上传 Modal */}
      <Modal
        open={uploadModalOpen}
        onClose={() => setUploadModalOpen(false)}
        title="上传文档"
        size="md"
      >
        <div className="space-y-4">
          <FormField label="文档标题" required hint="请输入清晰、准确的文档标题">
            <input
              type="text"
              value={uploadForm.title}
              onChange={(e) => setUploadForm({ ...uploadForm, title: e.target.value })}
              placeholder="例如：2026年度信息安全管理制度"
              className={inputClass}
            />
          </FormField>

          <div className="grid grid-cols-2 gap-4">
            <FormField label="分类">
              <div className="relative">
                <select
                  value={uploadForm.category}
                  onChange={(e) => setUploadForm({ ...uploadForm, category: e.target.value })}
                  className={cn(inputClass, 'appearance-none pr-9 cursor-pointer')}
                >
                  {CATEGORIES.map((c) => (
                    <option key={c.name} value={c.name} className="bg-bg-card">
                      {c.name}
                    </option>
                  ))}
                </select>
                <ChevronDown className="absolute right-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-500 pointer-events-none" />
              </div>
            </FormField>

            <FormField label="权限级别" hint="控制文档的可见范围">
              <div className="relative">
                <select
                  value={uploadForm.permission}
                  onChange={(e) =>
                    setUploadForm({ ...uploadForm, permission: e.target.value as typeof uploadForm.permission })
                  }
                  className={cn(inputClass, 'appearance-none pr-9 cursor-pointer')}
                >
                  {PERMISSION_LEVELS.map((p) => (
                    <option key={p} value={p} className="bg-bg-card">
                      {p}
                    </option>
                  ))}
                </select>
                <ChevronDown className="absolute right-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-500 pointer-events-none" />
              </div>
            </FormField>
          </div>

          <FormField label="文档描述" hint="简要描述文档内容，便于后续检索">
            <textarea
              value={uploadForm.description}
              onChange={(e) => setUploadForm({ ...uploadForm, description: e.target.value })}
              placeholder="请输入文档内容摘要..."
              rows={3}
              className={cn(inputClass, 'resize-none')}
            />
          </FormField>

          <FormField label="文档格式" hint="系统将在上传后自动检测文件格式">
            <div className="flex items-center gap-2 px-3 py-2 rounded-lg bg-bg-input border border-[#2a3142] text-xs text-slate-500">
              <FileText className="w-3.5 h-3.5" />
              支持 PDF / Word / Excel / PPT / 图片，上传后自动识别
            </div>
          </FormField>

          {/* 拖拽上传区域 */}
          <div className="rounded-xl border-2 border-dashed border-[#2a3142] hover:border-primary/40 transition-colors p-8 text-center cursor-pointer group">
            <UploadCloud className="w-10 h-10 text-slate-600 group-hover:text-primary transition-colors mx-auto mb-3" />
            <p className="text-sm text-slate-300 font-medium">拖拽文件到此处，或点击选择文件</p>
            <p className="text-xs text-slate-600 mt-1">单个文件最大 100MB，支持批量上传</p>
          </div>

          {/* 底部按钮 */}
          <div className="flex gap-3 pt-2">
            <button
              onClick={() => setUploadModalOpen(false)}
              className="btn-secondary flex-1"
            >
              取消
            </button>
            <button
              onClick={handleUpload}
              disabled={!uploadForm.title.trim()}
              className="btn-primary flex-1"
            >
              <Upload className="w-4 h-4" /> 开始上传
            </button>
          </div>
        </div>
      </Modal>

      {/* 文档审核 Modal */}
      <Modal
        open={!!reviewDoc}
        onClose={() => setReviewDoc(null)}
        title="文档审核"
        size="md"
      >
        {reviewDoc && (
          <div className="space-y-4">
            {/* 文档详情 */}
            <div className="rounded-lg bg-bg-input border border-[#1e2536] p-4">
              <div className="flex items-center justify-between mb-3">
                <div className="flex items-center gap-2">
                  <StatusBadge variant={(FORMAT_CONFIG[reviewDoc.format] ?? Object.values(FORMAT_CONFIG)[0]).variant}>
                    {reviewDoc.format}
                  </StatusBadge>
                  <StatusBadge variant={(STATUS_CONFIG[reviewDoc.status] ?? Object.values(STATUS_CONFIG)[0]).variant} dot>
                    {(STATUS_CONFIG[reviewDoc.status] ?? Object.values(STATUS_CONFIG)[0]).label}
                  </StatusBadge>
                </div>
                <span className="text-xs text-slate-500 tabular-nums">{reviewDoc.size}</span>
              </div>
              <h4 className="text-sm font-semibold text-white">{reviewDoc.title}</h4>
              <p className="text-xs text-slate-400 mt-1.5 leading-relaxed">{reviewDoc.description}</p>
              <div className="grid grid-cols-2 gap-x-6 gap-y-2 mt-3 pt-3 border-t border-[#1e2536]">
                <div className="flex items-center justify-between">
                  <span className="text-xs text-slate-500">所属分类</span>
                  <span className="text-xs text-slate-300">{reviewDoc.category}</span>
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-xs text-slate-500">上传者</span>
                  <span className="text-xs text-slate-300">{reviewDoc.uploader}</span>
                </div>
                <div className="flex items-center justify-between col-span-2">
                  <span className="text-xs text-slate-500">上传时间</span>
                  <span className="text-xs text-slate-300">{reviewDoc.uploadedAt}</span>
                </div>
              </div>
            </div>

            {/* 索引策略选择（批准时） */}
            <FormField label="索引策略" hint="选择文档批准发布后的向量化分块方式">
              <div className="grid grid-cols-2 gap-3">
                {INDEX_STRATEGIES.map((strategy) => (
                  <button
                    key={strategy}
                    onClick={() => setIndexStrategy(strategy)}
                    className={cn(
                      'flex flex-col items-start gap-1 px-4 py-3 rounded-lg border text-sm transition-all',
                      indexStrategy === strategy
                        ? 'border-primary/40 bg-primary/10 text-white'
                        : 'border-[#2a3142] bg-bg-input text-slate-400 hover:border-[#3a4258]',
                    )}
                  >
                    <span className="flex items-center gap-1.5 font-medium">
                      <Database className="w-3.5 h-3.5" /> {strategy}
                    </span>
                    <span className="text-xs text-slate-500">
                      {strategy === '自动分块' ? '按语义智能切分' : '按页/段手动切分'}
                    </span>
                  </button>
                ))}
              </div>
            </FormField>

            <p className="text-xs text-slate-500">
              批准后文档将进入索引队列，并按所选策略进行向量化处理；拒绝后文档将退回上传者修改。
            </p>

            {/* 底部操作按钮 */}
            <div className="flex gap-3 pt-2">
              <button
                onClick={handleReject}
                className="btn-secondary flex-1 !bg-status-failed/10 !text-status-failed !border-status-failed/30 hover:!bg-status-failed/20"
              >
                <XCircle className="w-4 h-4" /> 拒绝
              </button>
              <button onClick={handleApprove} className="btn-accent flex-1">
                <CheckCircle2 className="w-4 h-4" /> 批准发布
              </button>
            </div>
          </div>
        )}
      </Modal>
    </div>
  )
}
