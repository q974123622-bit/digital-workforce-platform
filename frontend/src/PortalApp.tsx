import { lazy, Suspense } from 'react';
import { Navigate, Route, Routes } from 'react-router-dom';
import AuthGate from './components/AuthGate';
import PortalLayout from './components/PortalLayout';
import { CurrentUserProvider } from './context/CurrentUserContext';
import { useAuth } from './context/AuthContext';
import NotFoundPage from './components/NotFoundPage';
import { LoadingState } from './components/PageState';

const WorkplacePage = lazy(() => import('./pages/workplace/WorkplacePage'));
const DirectoryPage = lazy(() => import('./pages/DirectoryPage'));
const PluginCenter = lazy(() => import('./pages/PluginCenter'));
const MemoryPage = lazy(() => import('./pages/MemoryPage'));
const MePage = lazy(() => import('./pages/MePage'));

function AuthenticatedPortal() {
  const { account } = useAuth();
  if (!account) return null;
  return (
    <CurrentUserProvider authenticatedUser={{ employee_no: account.employee_no, name: account.name, department: account.department }}>
      <Suspense fallback={<LoadingState rows={8} />}><Routes>
        <Route element={<PortalLayout />}>
          <Route path="/" element={<Navigate to="/workplace" replace />} />
          <Route path="/workplace" element={<WorkplacePage />} />
          <Route path="/directory" element={<DirectoryPage />} />
          <Route path="/plugins" element={<PluginCenter />} />
          <Route path="/memories" element={<MemoryPage />} />
          <Route path="/me" element={<MePage />} />
          {/* 旧聊天入口退役：统一转至消息会话体验 */}
          <Route path="/chat/:employeeNo" element={<Navigate to="/workplace" replace />} />
          <Route path="*" element={<NotFoundPage />} />
        </Route>
      </Routes></Suspense>
    </CurrentUserProvider>
  );
}

export default function PortalApp() {
  return <AuthGate><AuthenticatedPortal /></AuthGate>;
}
