import { lazy, Suspense } from 'react';
import { Route, Routes } from 'react-router-dom';
import AuthGate from './components/AuthGate';
import AdminLayout from './components/AdminLayout';
import { useAuth } from './context/AuthContext';
import { CurrentUserProvider } from './context/CurrentUserContext';
import NotFoundPage from './components/NotFoundPage';
import { LoadingState } from './components/PageState';

const Dashboard = lazy(() => import('./pages/Dashboard'));
const EmployeeDetail = lazy(() => import('./pages/EmployeeDetail'));
const Employees = lazy(() => import('./pages/Employees'));
const Plugins = lazy(() => import('./pages/Plugins'));
const Knowledge = lazy(() => import('./pages/Knowledge'));
const Security = lazy(() => import('./pages/Security'));
const Settings = lazy(() => import('./pages/Settings'));
const AgentOperations = lazy(() => import('./pages/AgentOperations'));

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
      <Suspense fallback={<LoadingState rows={8} />}><Routes>
        <Route element={<AdminLayout />}>
          <Route path="/" element={<Dashboard />} />
          <Route path="/employees" element={<Employees />} />
          <Route path="/agents" element={<AgentOperations />} />
          <Route path="/employees/:employeeNo" element={<EmployeeDetail />} />
          <Route path="/plugins" element={<Plugins />} />
          <Route path="/knowledge" element={<Knowledge />} />
          <Route path="/security" element={<Security />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="*" element={<NotFoundPage admin />} />
        </Route>
      </Routes></Suspense>
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
