import { Route, Routes } from 'react-router-dom';
import AppLayout from './components/AppLayout';
import ErrorBoundary from './components/ErrorBoundary';
import ChatPage from './pages/ChatPage';
import Dashboard from './pages/Dashboard';
import EmployeeDetail from './pages/EmployeeDetail';
import Employees from './pages/Employees';
import Plugins from './pages/Plugins';
import Security from './pages/Security';
import Teams from './pages/Teams';

export default function App() {
  return (
    <ErrorBoundary>
      <Routes>
        <Route element={<AppLayout />}>
          <Route path="/" element={<Dashboard />} />
          <Route path="/employees" element={<Employees />} />
          <Route path="/employees/:employeeNo" element={<EmployeeDetail />} />
          <Route path="/employees/:employeeNo/chat" element={<ChatPage />} />
          <Route path="/plugins" element={<Plugins />} />
          <Route path="/security" element={<Security />} />
          <Route path="/teams" element={<Teams />} />
        </Route>
      </Routes>
    </ErrorBoundary>
  );
}
