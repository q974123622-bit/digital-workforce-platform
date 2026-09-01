import { Navigate, Route, Routes } from 'react-router-dom';
import AuthGate from './components/AuthGate';
import PortalLayout from './components/PortalLayout';
import { CurrentUserProvider } from './context/CurrentUserContext';
import { useAuth } from './context/AuthContext';
import WorkplacePage from './pages/workplace/WorkplacePage';
import ChatPage from './pages/ChatPage';

function AuthenticatedPortal() {
  const { account } = useAuth();
  if (!account) return null;
  return (
    <CurrentUserProvider authenticatedUser={{ employee_no: account.employee_no, name: account.name, department: account.department }}>
      <Routes>
        <Route element={<PortalLayout />}>
          <Route path="/" element={<Navigate to="/workplace" replace />} />
          <Route path="/workplace" element={<WorkplacePage />} />
          <Route path="/chat/:employeeNo" element={<ChatPage />} />
          <Route path="*" element={<Navigate to="/workplace" replace />} />
        </Route>
      </Routes>
    </CurrentUserProvider>
  );
}

export default function PortalApp() {
  return <AuthGate><AuthenticatedPortal /></AuthGate>;
}
