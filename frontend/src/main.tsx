import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import './styles.css'
import Login from './pages/Login'
import Home from './pages/Home'
import Knowledge from './pages/Knowledge'
import Records from './pages/Records'
import IM from './pages/IM'
import Market from './pages/Market'
import Dashboard from './pages/Dashboard'
import AdminConsole from './pages/AdminConsole'
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
        <Route path="/" element={<RequireAuth><Home /></RequireAuth>} />
        <Route path="/knowledge" element={<RequireAuth><Knowledge /></RequireAuth>} />
        <Route path="/records" element={<RequireAuth><Records /></RequireAuth>} />
        <Route path="/im" element={<RequireAuth><IM /></RequireAuth>} />
        <Route path="/market" element={<RequireAuth><Market /></RequireAuth>} />
        <Route path="/dashboard" element={<RequireAuth><Dashboard /></RequireAuth>} />
        <Route path="/admin" element={<RequireAuth><AdminConsole /></RequireAuth>} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  </StrictMode>,
)
