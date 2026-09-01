import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import { ConfigProvider } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import 'antd/dist/reset.css';
import './index.css';
import AdminApp from './AdminApp';
import { AuthProvider } from './context/AuthContext';
import { themeConfig } from './theme';

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ConfigProvider locale={zhCN} theme={themeConfig} button={{ autoInsertSpace: false }}>
      <AuthProvider><BrowserRouter basename="/admin"><AdminApp /></BrowserRouter></AuthProvider>
    </ConfigProvider>
  </React.StrictMode>,
);

