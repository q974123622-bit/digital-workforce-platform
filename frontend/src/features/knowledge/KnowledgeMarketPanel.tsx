import { DatabaseOutlined, SearchOutlined } from '@ant-design/icons';
import { Button, Empty, Input, Modal, Segmented, Tag, Typography, message } from 'antd';
import { useMemo, useState } from 'react';
import { api } from '../../api/client';
import type { KnowledgeMarketItem, KnowledgeMarketState } from '../../api/client';

const { Text } = Typography;

/** 四态的展示口径：文案、色板与「现在能做什么」的一行说明。 */
const STATE_META: Record<KnowledgeMarketState, {
  label: string;
  color: 'success' | 'processing' | 'warning' | 'default';
  hint: string;
}> = {
  enabled: { label: '已授权', color: 'success', hint: '已授权给你的分身，可直接检索。' },
  pending: { label: '申请中', color: 'warning', hint: '申请已提交，等待管理员审批。' },
  occupied: { label: '已被占用', color: 'default', hint: '该库由其他 AI 员工独占，需管理员改派后才能申请。' },
  requestable: { label: '可申请', color: 'default', hint: '填写用途提交申请，审批通过后即可检索。' },
};

type StateFilter = 'all' | 'usable' | 'requestable' | 'pending';

function stateOf(item: KnowledgeMarketItem): KnowledgeMarketState {
  return item.state ?? 'requestable';
}

function StateAction({
  item, onRequest,
}: {
  item: KnowledgeMarketItem;
  onRequest: (item: KnowledgeMarketItem) => void;
}) {
  const state = stateOf(item);
  if (state === 'enabled') {
    return <Button size="small" disabled title="已授权给你的分身">已授权</Button>;
  }
  if (state === 'pending') {
    return (
      <Button size="small" disabled title={item.request_id ? `申请单 ARQ-${item.request_id} · 等待管理员审批` : undefined}>
        申请中
      </Button>
    );
  }
  if (state === 'occupied') {
    const who = item.occupant_name || item.occupant || '其他 AI 员工';
    return <Button size="small" disabled title={`已被 ${who} 占用，需管理员改派`}>已被占用</Button>;
  }
  return (
    <Button type="primary" size="small" onClick={() => onRequest(item)}>
      申请使用
    </Button>
  );
}

function KnowledgeCard({
  item, onRequest,
}: {
  item: KnowledgeMarketItem;
  onRequest: (item: KnowledgeMarketItem) => void;
}) {
  const state = stateOf(item);
  const meta = STATE_META[state];
  const available = item.source_available !== false;
  const hint = state === 'pending' && item.request_id
    ? `申请单 ARQ-${item.request_id} · 等待管理员审批`
    : state === 'occupied'
      ? `已被 ${item.occupant_name || item.occupant || '其他 AI 员工'} 占用`
      : meta.hint;

  return (
    <article className={`knowledge-card flex min-h-[168px] flex-col rounded-container p-4 ${state === 'enabled' ? 'bg-surface' : 'bg-surface-subtle'}`}>
      <div className="flex items-start gap-3">
        <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-[var(--dwp-brand-soft)] text-[var(--dwp-brand)]">
          <DatabaseOutlined />
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex items-start justify-between gap-2">
            <Text strong className="block truncate">{item.name}</Text>
            <Tag color={meta.color} className="!mr-0 shrink-0">{meta.label}</Tag>
          </div>
          <div className="mt-1.5 flex flex-wrap items-center gap-1">
            {item.data_level && <Tag>{item.data_level}</Tag>}
            {item.domain && <Tag>{item.domain}</Tag>}
            <Tag color={available ? 'green' : 'default'}>
              {available ? `数据源可用${item.active_source ? ` · ${item.active_source}` : ''}` : '数据源不可达'}
            </Tag>
          </div>
          <p className="mb-0 mt-3 text-xs leading-5 text-ink-secondary">{item.description || '企业知识库资源。'}</p>
        </div>
      </div>
      <div className="mt-auto flex items-end justify-between gap-3 pt-3">
        <span className="text-xs leading-5 text-ink-tertiary">{hint}</span>
        <StateAction item={item} onRequest={onRequest} />
      </div>
    </article>
  );
}

/**
 * 员工侧「知识库市场」：全部知识库全可见，用状态表达「能不能用」。
 *
 * 被他人独占的库照常展示并标注占用方（按钮禁用 + title 说明），而不是藏起来 ——
 * 员工需要知道「这个库存在、但现在不该我来用」。
 */
export default function KnowledgeMarketPanel({
  items, onChanged,
}: {
  items: KnowledgeMarketItem[];
  /** 申请成功后通知外层刷新（页签计数与货架状态一起更新）。 */
  onChanged: () => void;
}) {
  const [keyword, setKeyword] = useState('');
  const [filter, setFilter] = useState<StateFilter>('all');
  const [target, setTarget] = useState<KnowledgeMarketItem | null>(null);
  const [reason, setReason] = useState('');
  const [submitting, setSubmitting] = useState(false);

  const filtered = useMemo(() => {
    const needle = keyword.trim().toLowerCase();
    return items.filter((item) => {
      const state = stateOf(item);
      if (filter === 'usable' && state !== 'enabled') return false;
      if (filter === 'requestable' && state !== 'requestable') return false;
      if (filter === 'pending' && state !== 'pending') return false;
      if (!needle) return true;
      return `${item.name} ${item.domain ?? ''} ${item.description ?? ''}`.toLowerCase().includes(needle);
    });
  }, [items, keyword, filter]);

  const closeRequest = () => {
    setTarget(null);
    setReason('');
  };

  const submitRequest = async () => {
    if (!target) return;
    setSubmitting(true);
    try {
      await api.requestMyKnowledgeBase(target.id, reason.trim());
      message.success('申请已提交，管理员审批通过后即可检索');
      closeRequest();
      onChanged();
    } catch (err) {
      message.error(`提交申请失败：${(err as Error).message}`);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="pb-6">
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <Input
          allowClear
          aria-label="搜索知识库"
          prefix={<SearchOutlined className="text-ink-tertiary" />}
          placeholder="搜索知识库名称或领域"
          value={keyword}
          onChange={(event) => setKeyword(event.target.value)}
          className="w-full sm:w-[240px]"
        />
        <Segmented
          value={filter}
          onChange={(value) => setFilter(value as StateFilter)}
          options={[
            { label: '全部', value: 'all' },
            { label: '已授权', value: 'usable' },
            { label: '可申请', value: 'requestable' },
            { label: '申请中', value: 'pending' },
          ]}
        />
        <span className="text-xs text-ink-tertiary">共 {filtered.length} 个知识库</span>
      </div>
      <div className="plugin-catalog-grid grid gap-3 md:grid-cols-2">
        {filtered.map((item) => (
          <KnowledgeCard key={item.id} item={item} onRequest={setTarget} />
        ))}
        {filtered.length === 0 && (
          <Empty
            className="md:col-span-2"
            description={items.length === 0 ? '暂无知识库资源' : '没有匹配的知识库，试试其它关键字'}
          />
        )}
      </div>
      <Modal
        title="申请使用知识库"
        open={Boolean(target)}
        onCancel={closeRequest}
        onOk={() => void submitRequest()}
        okText="提交申请"
        cancelText="取消"
        confirmLoading={submitting}
        width={520}
        destroyOnClose
      >
        {target && (
          <div className="space-y-4">
            <div className="rounded-container bg-surface-subtle p-3">
              <Text strong>{target.name}</Text>
              <div className="mt-2 flex flex-wrap gap-1">
                {target.data_level && <Tag>{target.data_level}</Tag>}
                {target.domain && <Tag>{target.domain}</Tag>}
                <Tag color={target.source_available === false ? 'default' : 'green'}>
                  {target.source_available === false ? '数据源不可达' : '数据源可用'}
                </Tag>
              </div>
              <p className="mb-0 mt-3 text-xs leading-5 text-ink-secondary">
                {target.description || '企业知识库资源。'}
              </p>
            </div>
            <div>
              <div className="mb-1 text-xs text-ink-secondary">用途说明（可选，最多 500 字）</div>
              <Input.TextArea
                rows={4}
                maxLength={500}
                showCount
                aria-label="用途说明"
                placeholder="说明你要检索什么内容、用在哪个工作场景，便于管理员判断"
                value={reason}
                onChange={(event) => setReason(event.target.value)}
              />
            </div>
            <Text type="secondary" className="!text-xs">
              知识库为独占资源：审批时会校验是否已被其他 AI 员工占用，通过后自动授权给你的分身。
            </Text>
          </div>
        )}
      </Modal>
    </div>
  );
}
