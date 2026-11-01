import { useEffect } from 'react'
import { Routes, Route, Navigate } from 'react-router-dom'
import Layout from './components/Layout'
import ProtectedRoute from './components/ProtectedRoute'
import ErrorBoundary from './components/ErrorBoundary'

// 总览页面
import Dashboard from './pages/Dashboard'
import Agents from './pages/Agents'
import EventStream from './pages/EventStream'
import Conversation from './pages/Conversation'
import Exceptions from './pages/Exceptions'

// 财务页面
import Accounting from './pages/Accounting'
import Invoice from './pages/Invoice'
import Reimbursement from './pages/Reimbursement'
import Tax from './pages/Tax'

// HR 页面
import EmployeeManagement from './pages/EmployeeManagement'
import Attendance from './pages/Attendance'
import Payroll from './pages/Payroll'
import Recruitment from './pages/Recruitment'

// 销售与市场页面
import CustomerManagement from './pages/CustomerManagement'
import SalesPipeline from './pages/SalesPipeline'
import MarketingCampaigns from './pages/MarketingCampaigns'

// 法务与风控页面
import ContractManagement from './pages/ContractManagement'
import RiskManagement from './pages/RiskManagement'

// 运营管理页面
import Inventory from './pages/Inventory'
import ProjectManagement from './pages/ProjectManagement'
import QualityManagement from './pages/QualityManagement'
import AssetManagement from './pages/AssetManagement'

// 战略分析页面
import BIReports from './pages/BIReports'
import BudgetManagement from './pages/BudgetManagement'
import ESGManagement from './pages/ESGManagement'

// 系统管理页面
import Security from './pages/Security'
import RoleManagement from './pages/RoleManagement'
import PermissionManagement from './pages/PermissionManagement'
import ApprovalWorkflow from './pages/ApprovalWorkflow'

// 知识库 & CIO 管理后台
import KnowledgeBase from './pages/KnowledgeBase'
import CIOAdmin from './pages/CIOAdmin'

// 文档中心
import DocumentGeneration from './pages/DocumentGeneration'

// 内部通讯
import Messaging from './pages/Messaging'

// 登录页
import Login from './pages/Login'

// Store
import { useSystemStore } from './stores/systemStore'
import { useAuthStore } from './stores/authStore'
import { websocketService } from './services/websocket'

function App() {
  const initWebSocket = useSystemStore((s) => s.initWebSocket)
  const fetchInitialData = useSystemStore((s) => s.fetchInitialData)
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated)

  useEffect(() => {
    if (!isAuthenticated) {
      websocketService.disconnect()
      return
    }

    initWebSocket()
    fetchInitialData()

    return () => {
      websocketService.disconnect()
    }
  }, [initWebSocket, fetchInitialData, isAuthenticated])

  return (
    <Routes>
      {/* 公开路由 */}
      <Route path="/login" element={<Login />} />

      {/* 受保护路由 — 需要认证 */}
      <Route
        path="/*"
        element={
          <ProtectedRoute>
            <Layout>
              <ErrorBoundary>
              <Routes>
                {/* 总览 */}
                <Route path="/" element={<Dashboard />} />
                <Route path="/agents" element={<ProtectedRoute resource="agent" action="read"><Agents /></ProtectedRoute>} />
                <Route path="/conversation" element={<ProtectedRoute resource="agent" action="read"><Conversation /></ProtectedRoute>} />
                <Route path="/exceptions" element={<ProtectedRoute resource="agent" action="read"><Exceptions /></ProtectedRoute>} />
                <Route path="/events" element={<ProtectedRoute resource="system" action="read"><EventStream /></ProtectedRoute>} />

                {/* 财务 */}
                <Route path="/accounting" element={<ProtectedRoute resource="accounting" action="read"><Accounting /></ProtectedRoute>} />
                <Route path="/invoice" element={<ProtectedRoute resource="invoice" action="read"><Invoice /></ProtectedRoute>} />
                <Route path="/reimbursement" element={<ProtectedRoute resource="reimbursement" action="read"><Reimbursement /></ProtectedRoute>} />
                <Route path="/tax" element={<ProtectedRoute resource="tax" action="read"><Tax /></ProtectedRoute>} />

                {/* 人力资源 */}
                <Route path="/employees" element={<ProtectedRoute resource="hr" action="read"><EmployeeManagement /></ProtectedRoute>} />
                <Route path="/attendance" element={<ProtectedRoute resource="hr" action="read"><Attendance /></ProtectedRoute>} />
                <Route path="/payroll" element={<ProtectedRoute resource="hr" action="read"><Payroll /></ProtectedRoute>} />
                <Route path="/recruitment" element={<ProtectedRoute resource="recruitment" action="read"><Recruitment /></ProtectedRoute>} />

                {/* 销售与市场 */}
                <Route path="/customers" element={<ProtectedRoute resource="crm" action="read"><CustomerManagement /></ProtectedRoute>} />
                <Route path="/sales-pipeline" element={<ProtectedRoute resource="sales" action="read"><SalesPipeline /></ProtectedRoute>} />
                <Route path="/campaigns" element={<ProtectedRoute resource="marketing" action="read"><MarketingCampaigns /></ProtectedRoute>} />

                {/* 法务与风控 */}
                <Route path="/contracts" element={<ProtectedRoute resource="contract" action="read"><ContractManagement /></ProtectedRoute>} />
                <Route path="/risk" element={<ProtectedRoute resource="risk" action="read"><RiskManagement /></ProtectedRoute>} />

                {/* 运营管理 */}
                <Route path="/inventory" element={<ProtectedRoute resource="inventory" action="read"><Inventory /></ProtectedRoute>} />
                <Route path="/projects" element={<ProtectedRoute resource="project" action="read"><ProjectManagement /></ProtectedRoute>} />
                <Route path="/quality" element={<ProtectedRoute resource="quality" action="read"><QualityManagement /></ProtectedRoute>} />
                <Route path="/assets" element={<ProtectedRoute resource="asset" action="read"><AssetManagement /></ProtectedRoute>} />

                {/* 战略分析 */}
                <Route path="/bi-reports" element={<ProtectedRoute resource="bi" action="read"><BIReports /></ProtectedRoute>} />
                <Route path="/budget" element={<ProtectedRoute resource="budget" action="read"><BudgetManagement /></ProtectedRoute>} />
                <Route path="/esg" element={<ProtectedRoute resource="esg" action="read"><ESGManagement /></ProtectedRoute>} />

                {/* 知识库 */}
                <Route path="/knowledge" element={<ProtectedRoute resource="knowledge" action="read"><KnowledgeBase /></ProtectedRoute>} />

                {/* 文档中心 */}
                <Route path="/documents" element={<ProtectedRoute resource="document" action="read"><DocumentGeneration /></ProtectedRoute>} />

                {/* 内部通讯 */}
                <Route path="/messaging" element={<ProtectedRoute resource="message" action="read"><Messaging /></ProtectedRoute>} />

                {/* 系统管理 */}
                <Route path="/security" element={<ProtectedRoute resource="security" action="read"><Security /></ProtectedRoute>} />
                <Route path="/roles" element={<ProtectedRoute resource="role" action="read"><RoleManagement /></ProtectedRoute>} />
                <Route path="/permissions" element={<ProtectedRoute resource="permission" action="read"><PermissionManagement /></ProtectedRoute>} />
                <Route path="/approvals" element={<ProtectedRoute resource="system" action="read"><ApprovalWorkflow /></ProtectedRoute>} />
                <Route path="/cio-admin" element={<ProtectedRoute resource="system" action="manage"><CIOAdmin /></ProtectedRoute>} />

                {/* 兜底 */}
                <Route path="*" element={<Navigate to="/" replace />} />
              </Routes>
              </ErrorBoundary>
            </Layout>
          </ProtectedRoute>
        }
      />
    </Routes>
  )
}

export default App
