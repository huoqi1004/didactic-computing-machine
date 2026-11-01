import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import './styles.css'
import Login from './pages/Login'
import Shell from './components/Shell'
import Chat from './pages/Chat'
import NewConversationModal from './pages/NewConversationModal'
import Knowledge from './pages/Knowledge'
import Records from './pages/Records'
import IM from './pages/IM'
import Market from './pages/Market'
import Dashboard from './pages/Dashboard'
import AdminConsole from './pages/AdminConsole'
import Settings from './pages/Settings'
import { getToken } from './lib/api'

function RequireAuth({ children }: { children: React.ReactNode }) {
  if (!getToken()) return <Navigate to="/login" replace />
  return <>{children}</>
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <BrowserRouter>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route element={<RequireAuth><Shell /></RequireAuth>}>
          <Route path="/" element={<><Chat /><NewConversationModal /></>} />
          <Route path="/knowledge" element={<Knowledge />} />
          <Route path="/records" element={<Records />} />
          <Route path="/im" element={<IM />} />
          <Route path="/market" element={<Market />} />
          <Route path="/dashboard" element={<Dashboard />} />
          <Route path="/admin" element={<AdminConsole />} />
          <Route path="/settings" element={<Settings />} />
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  </StrictMode>,
)
