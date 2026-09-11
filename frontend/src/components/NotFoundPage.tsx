import { ArrowLeftOutlined } from '@ant-design/icons';
import { Button } from 'antd';
import { Link } from 'react-router-dom';
import BrandMark from './ui/BrandMark';

export default function NotFoundPage({ admin = false }: { admin?: boolean }) {
  const destination = admin ? '/admin/' : '/workplace';
  return (
    <main className="grid min-h-full place-items-center px-6 py-16 text-center">
      <div className="max-w-[440px]">
        <BrandMark size={64} className="mx-auto" />
        <div className="mt-6 font-mono text-xs tracking-[.18em] text-ink-tertiary">PAGE 404</div>
        <h1 className="mb-2 mt-3 text-2xl font-semibold tracking-[-.02em] text-ink">这个页面暂时不可用</h1>
        <p className="mb-6 text-sm leading-6 text-ink-secondary">地址可能已经调整。返回工作台后，你的会话和当前任务不会受到影响。</p>
        <Link to={destination}><Button type="primary" icon={<ArrowLeftOutlined />}>返回{admin ? '平台管理' : '工作台'}</Button></Link>
      </div>
    </main>
  );
}
