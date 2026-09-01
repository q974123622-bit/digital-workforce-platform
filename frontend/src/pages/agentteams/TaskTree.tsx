import { ApartmentOutlined, MessageOutlined, ShareAltOutlined } from '@ant-design/icons';
import { Empty, Progress, Tag, Typography } from 'antd';
import type { AgentTaskGraph, AgentTaskNode } from '@dwp/shared-schema';

const STATUS_META: Record<string, { label: string; color: string }> = {
  planning: { label: '规划中', color: 'blue' },
  assigned: { label: '已分派', color: 'cyan' },
  accepted: { label: '已接单', color: 'processing' },
  running: { label: '执行中', color: 'processing' },
  waiting_children: { label: '等待子任务', color: 'gold' },
  summarizing: { label: '汇总中', color: 'purple' },
  completed: { label: '已完成', color: 'success' },
  failed: { label: '失败', color: 'error' },
  timed_out: { label: '已超时', color: 'warning' },
  cancelled: { label: '已取消', color: 'default' },
};

interface Props {
  nodes: AgentTaskNode[];
  graph?: AgentTaskGraph | null;
  selectedId?: string | null;
  onSelect: (taskId: string) => void;
}

type Edge = Record<string, string>;

const edgeValue = (edge: Edge, key: string) => edge[key] || '未标识';

function edgeRows(edges: Edge[], fromKey: string, toKey: string, empty: string) {
  if (edges.length === 0) return <Typography.Text type="secondary">{empty}</Typography.Text>;
  return edges.map((edge, index) => (
    <div className="at-edge" key={`${edgeValue(edge, fromKey)}-${edgeValue(edge, toKey)}-${index}`}>
      <span className="at-edge-node">{edgeValue(edge, fromKey)}</span>
      <span className="at-edge-line" aria-hidden="true">→</span>
      <span className="at-edge-node">{edgeValue(edge, toKey)}</span>
    </div>
  ));
}

export function TaskTree({ nodes, graph, selectedId, onSelect }: Props) {
  const nodeIds = new Set(nodes.map((node) => node.id));
  const children = new Map<string | null, AgentTaskNode[]>();
  const parentEdges: Edge[] = graph?.parent_edges ?? nodes
    .filter((node) => node.parent_task_id)
    .map((node) => ({ parent_task_id: node.parent_task_id ?? '', task_id: node.id }));

  for (const node of nodes) {
    const parentId = node.parent_task_id && nodeIds.has(node.parent_task_id) ? node.parent_task_id : null;
    children.set(parentId, [...(children.get(parentId) ?? []), node]);
  }

  const renderNode = (node: AgentTaskNode) => {
    const status = STATUS_META[node.status] ?? { label: node.status, color: 'default' };
    const nested = children.get(node.id) ?? [];
    return (
      <li key={node.id} className="at-tree-item">
        <button
          type="button"
          className={`at-tree-node ${selectedId === node.id ? 'selected' : ''}`}
          aria-label={`选择任务 ${node.title}`}
          aria-pressed={selectedId === node.id}
          onClick={() => onSelect(node.id)}
        >
          <span className="at-tree-node-top">
            <span className="at-tree-title">{node.title}</span>
            <Tag color={status.color} style={{ marginInlineEnd: 0 }}>{status.label}</Tag>
          </span>
          <span className="at-tree-meta">
            <span>{node.assignee_agent_id}</span>
            <span>层级 {node.depth}</span>
          </span>
          <Progress percent={Math.max(0, Math.min(100, node.progress))} size="small" showInfo={false} />
        </button>
        {nested.length > 0 && <ul className="at-tree-children">{nested.map(renderNode)}</ul>}
      </li>
    );
  };

  const roots = children.get(null) ?? [];
  const dependencyEdges = graph?.dependency_edges ?? [];
  const messageEdges = graph?.communication_edges ?? [];

  return (
    <div className="at-tree-wrap">
      <section aria-labelledby="task-tree-heading">
        <Typography.Title level={5} id="task-tree-heading">任务委派树</Typography.Title>
        {roots.length > 0 ? (
          <ul className="at-tree">{roots.map(renderNode)}</ul>
        ) : (
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="服务端尚未返回任务节点" />
        )}
      </section>

      <section className="at-relationship-map" aria-labelledby="relationship-heading">
        <Typography.Title level={5} id="relationship-heading">关系图</Typography.Title>
        <div className="at-relationship-grid">
          <div className="at-relationship-group">
            <Typography.Text strong><ShareAltOutlined /> 父子委派</Typography.Text>
            {edgeRows(parentEdges, 'parent_task_id', 'task_id', '暂无父子委派边')}
          </div>
          <div className="at-relationship-group">
            <Typography.Text strong><ApartmentOutlined /> 任务依赖</Typography.Text>
            {edgeRows(dependencyEdges, 'depends_on_task_id', 'task_id', '暂无任务依赖边')}
          </div>
          <div className="at-relationship-group">
            <Typography.Text strong><MessageOutlined /> 消息关系</Typography.Text>
            {messageEdges.length === 0 ? (
              <Typography.Text type="secondary">暂无消息关系边</Typography.Text>
            ) : (
              messageEdges.map((edge, index) => (
                <div className="at-edge" key={`${edgeValue(edge, 'id')}-${index}`}>
                  <span className="at-edge-node">{edgeValue(edge, 'sender_agent_id')}</span>
                  <span className="at-edge-line" aria-hidden="true">→</span>
                  <span className="at-edge-node">{edgeValue(edge, 'receiver_agent_id')}</span>
                  <Tag style={{ marginInlineEnd: 0 }}>{edgeValue(edge, 'message_type')}</Tag>
                </div>
              ))
            )}
          </div>
        </div>
      </section>
    </div>
  );
}

export { STATUS_META as AGENT_TASK_STATUS_META };
