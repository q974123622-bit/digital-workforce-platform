import { Component } from 'react';
import type { ErrorInfo, ReactNode } from 'react';
import { Button, Result } from 'antd';

interface Props {
  children: ReactNode;
}

interface State {
  error: Error | null;
}

export default class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error('页面渲染异常：', error, info);
  }

  render() {
    if (this.state.error) {
      return (
        <div style={{ padding: 48, maxWidth: 720, margin: '0 auto' }}>
          <Result
            status="error"
            title="页面出现异常"
            subTitle={this.state.error.message}
            extra={
              <Button type="primary" onClick={() => window.location.reload()}>
                刷新页面
              </Button>
            }
          />
        </div>
      );
    }
    return this.props.children;
  }
}
