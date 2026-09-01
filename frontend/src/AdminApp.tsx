import { Navigate, Route, Routes } from 'react-router-dom';
import AuthGate from './components/AuthGate';
import AdminLayout from './components/AdminLayout';
import Dashboard from './pages/Dashboard';
import EmployeeDetail from './pages/EmployeeDetail';
import Employees from './pages/Employees';
import Plugins from './pages/Plugins';
import Security from './pages/Security';
import AgentOperations from './pages/AgentOperations';
import { useAuth } from './context/AuthContext';
import { CurrentUserProvider } from './context/CurrentUserContext';

function AuthenticatedAdmin() {
  const { account } = useAuth();
  if (!account) return null;

  return (
    <CurrentUserProvider
      authenticatedUser={{
        employee_no: account.employee_no,
        name: account.name,
        department: account.department,
      }}
    >
      <Routes>
        <Route element={<AdminLayout />}>
          <Route path="/" element={<Dashboard />} />
          <Route path="/employees" element={<Employees />} />
          <Route path="/agents" element={<AgentOperations />} />
          <Route path="/employees/:employeeNo" element={<EmployeeDetail />} />
          <Route path="/plugins" element={<Plugins />} />
          <Route path="/security" element={<Security />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
    </CurrentUserProvider>
  );
}

export default function AdminApp() {
  return (
    <AuthGate admin>
      <AuthenticatedAdmin />
    </AuthGate>
  );
}
