import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import 'antd/dist/reset.css';
import './index.css';
import './styles/product.css';
import PortalApp from './PortalApp';
import { AuthProvider } from './context/AuthContext';
import { ThemeProvider } from './context/ThemeContext';

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ThemeProvider>
      <AuthProvider><BrowserRouter><PortalApp /></BrowserRouter></AuthProvider>
    </ThemeProvider>
  </React.StrictMode>,
);
