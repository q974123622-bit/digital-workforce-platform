import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import 'antd/dist/reset.css';
import './index.css';
import './styles/product.css';
import App from './App';
import { ThemeProvider } from './context/ThemeContext';
import { CurrentUserProvider } from './context/CurrentUserContext';

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ThemeProvider>
      <CurrentUserProvider>
        <BrowserRouter>
          <App />
        </BrowserRouter>
      </CurrentUserProvider>
    </ThemeProvider>
  </React.StrictMode>,
);
