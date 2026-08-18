import {
  ApiOutlined,
  ApartmentOutlined,
  BookOutlined,
  CheckCircleOutlined,
  ClockCircleOutlined,
  CloseCircleOutlined,
  DeploymentUnitOutlined,
  LinkOutlined,
  RobotOutlined,
  ThunderboltOutlined,
  UserOutlined,
} from '@ant-design/icons';
import { Badge, Tag } from 'antd';
import type { ReactNode } from 'react';

export interface TypeMeta {
  label: string;
  /** AntD 预设 Tag 色 */
  color: string;
  icon: ReactNode;
  /** CSS 色值，用于图标底色与图表 */
  hex: string;
  /** 图标浅色底 */
  bg: string;
}

/** 数字员工类型 */
export const TYPE_META: Record<string, TypeMeta> = {
  twin: { label: '数字分身', color: 'blue', icon: <RobotOutlined />, hex: '#2f54eb', bg: '#eef4ff' },
  virtual: { label: '虚拟员工', color: 'green', icon: <UserOutlined />, hex: '#52c41a', bg: '#f6ffed' },
  rpa: { label: 'RPA', color: 'orange', icon: <ThunderboltOutlined />, hex: '#fa8c16', bg: '#fff7e6' },
};

/** 插件类型 */
export const PLUGIN_TYPE_META: Record<string, TypeMeta> = {
  knowledge: { label: '知识库', color: 'geekblue', icon: <BookOutlined />, hex: '#2f54eb', bg: '#f0f5ff' },
  mcp: { label: 'MCP', color: 'purple', icon: <ApiOutlined />, hex: '#722ed1', bg: '#f9f0ff' },
  workflow: { label: 'Workflow', color: 'cyan', icon: <ApartmentOutlined />, hex: '#13c2c2', bg: '#e6fffb' },
  rpa: { label: 'RPA', color: 'orange', icon: <DeploymentUnitOutlined />, hex: '#fa8c16', bg: '#fff7e6' },
  http: { label: 'HTTP API', color: 'blue', icon: <LinkOutlined />, hex: '#1677ff', bg: '#e6f4ff' },
};

/** 决策 / 授权模式 */
export const DECISION_META: Record<string, { label: string; color: string; icon: ReactNode }> = {
  allow: { label: '允许', color: 'success', icon: <CheckCircleOutlined /> },
  deny: { label: '拒绝', color: 'error', icon: <CloseCircleOutlined /> },
  approval: { label: '待审批', color: 'warning', icon: <ClockCircleOutlined /> },
};

const STATUS_META: Record<string, { label: string; status: 'success' | 'error' | 'warning' | 'default' }> = {
  active: { label: '启用', status: 'success' },
  inactive: { label: '停用', status: 'default' },
  disabled: { label: '禁用', status: 'error' },
  pending: { label: '待激活', status: 'warning' },
};

const LEVEL_META: Record<string, { label: string; color: string }> = {
  L1: { label: 'L1 公开', color: 'green' },
  L2: { label: 'L2 内部', color: 'blue' },
  L3: { label: 'L3 高敏', color: 'red' },
};

export function TypeTag({ value }: { value: string }) {
  const meta = TYPE_META[value];
  if (!meta) return <Tag>{value}</Tag>;
  return (
    <Tag icon={meta.icon} color={meta.color}>
      {meta.label}
    </Tag>
  );
}

export function PluginTypeTag({ value }: { value: string }) {
  const meta = PLUGIN_TYPE_META[value];
  if (!meta) return <Tag>{value}</Tag>;
  return (
    <Tag icon={meta.icon} color={meta.color}>
      {meta.label}
    </Tag>
  );
}

export function StatusBadge({ value }: { value: string }) {
  const meta = STATUS_META[value];
  if (!meta) return <Badge status="default" text={value} />;
  return <Badge status={meta.status} text={meta.label} />;
}

export function LevelTag({ value }: { value: string }) {
  const meta = LEVEL_META[value.toUpperCase()];
  if (!meta) return <Tag>{value}</Tag>;
  return <Tag color={meta.color}>{meta.label}</Tag>;
}

export function DecisionTag({ value }: { value: string }) {
  const meta = DECISION_META[value];
  if (!meta) return <Tag>{value}</Tag>;
  return (
    <Tag icon={meta.icon} color={meta.color}>
      {meta.label}
    </Tag>
  );
}
