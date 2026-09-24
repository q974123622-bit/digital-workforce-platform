import { CommentOutlined, ToolOutlined } from '@ant-design/icons';
import { Button, Empty, Space, Tag, Typography } from 'antd';
import { useEffect, useState } from 'react';
import type { EffectiveCapabilities, Employee } from '@dwp/shared-schema';
import { api } from '../../api/client';
import AgentAvatar from '../../components/ui/AgentAvatar';
import { EMPLOYEE_TYPE_LABEL } from '../../components/tags';
import { deployStateMeta } from '../plugins/pluginMeta';
import { formatServerTime } from '../../utils/serverTime';

const { Text } = Typography;

/** 类型展示口径复用 tags.tsx 的唯一来源 */
const TYPE_LABEL: Record<string, string> = {
  twin: EMPLOYEE_TYPE_LABEL.twin,
  virtual: EMPLOYEE_TYPE_LABEL.virtual,
  rpa: EMPLOYEE_TYPE_LABEL.rpa,
};

export default function AgentProfile({ employee, onOpenChat }: { employee: Employee; onOpenChat: (no: string) => void }) {
  const [caps, setCaps] = useState<EffectiveCapabilities | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    let active = true;
    setLoading(true);
    api.getEffectiveCapabilities(employee.employee_no)
      .then((result) => active && setCaps(result))
      .catch(() => active && setCaps(null))
      .finally(() => active && setLoading(false));
    return () => { active = false; };
  }, [employee.employee_no]);

  return (
    <div className="agent-profile space-y-7">
      <div className="agent-profile-identity">
        <AgentAvatar employee={employee} size={72} className={`directory-avatar ${employee.type === 'twin' ? 'twin' : employee.type === 'rpa' ? 'automation' : 'digital_employee'}`} />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="agent-profile-name">{employee.name}</span>
          </div>
          <Text type="secondary" className="!text-xs">
            {employee.employee_no} · {employee.department}
          </Text>
          <div className="mt-2"><Tag color={employee.type === 'rpa' ? 'orange' : 'blue'}>{TYPE_LABEL[employee.type] ?? employee.type}</Tag></div>
        </div>
        <Button type="primary" icon={<CommentOutlined />} onClick={() => onOpenChat(employee.employee_no)}>
          发消息
        </Button>
      </div>

      <div>
        <div className="profile-section-title">职责与工作方式</div>
        <div className="agent-profile-prose">
          {employee.role_prompt || '（尚未配置职责与擅长方向）'}
        </div>
      </div>

      <dl className="agent-profile-facts">
        <div><dt>负责人</dt><dd>{employee.owner_name || employee.owner_human_no}</dd></div>
        <div><dt>数据等级</dt><dd>{employee.max_data_level}</dd></div>
        <div><dt>访问位置</dt><dd>{employee.location || '尚未配置'}</dd></div>
      </dl>

      <div>
        <div className="profile-section-title flex items-center gap-2">
          <ToolOutlined className="text-brand" /> 已启用插件与知识范围
        </div>
        {loading ? (
          <Text type="secondary">加载中…</Text>
        ) : caps ? (
          <>
            <Space size={[6, 6]} wrap className="mb-3">
              <Tag color={['ready', 'busy'].includes(caps.runtime_state) ? 'green' : 'red'}>
                运行 {['ready', 'busy'].includes(caps.runtime_state) ? '就绪' : caps.runtime_state}
              </Tag>
              <Tag color={caps.knowledge_mode === 'internal' ? 'blue' : 'gold'}>
                {caps.knowledge_mode === 'internal' ? '内部知识引擎' : '演示知识'}
              </Tag>
              <Tag color="blue">{caps.available_count} 项可用</Tag>
            </Space>
            {caps.capabilities.filter((c) => c.authorized).length === 0 && <Empty description="暂未授权能力" />}
            <div className="space-y-2">
              {caps.capabilities.filter((c) => c.authorized).map((item) => {
                // 技能比插件多一层投放态：技能包要真落到容器工作区才算可用。
                const deploy = item.kind === 'skill' ? deployStateMeta(item.deploy_state) : null;
                return (
                  <div key={item.id} className="agent-profile-capability">
                    <div className="min-w-0">
                      <div className="text-sm font-medium text-ink">{item.name}</div>
                      <div className="text-xs text-ink-secondary">{item.description}</div>
                      {deploy && (
                        <div className="mt-1 text-xs text-ink-tertiary">
                          投放：{deploy.label}
                          {item.deploy_detail ? `，${item.deploy_detail}` : ''}
                          {item.deployed_at ? `（${formatServerTime(item.deployed_at)}）` : ''}
                        </div>
                      )}
                      {item.example_prompts[0] && (
                        <div className="mt-1 text-xs text-ink-tertiary">示例：{item.example_prompts[0]}</div>
                      )}
                    </div>
                    <Space size={4} wrap>
                      {deploy && <Tag color={deploy.color}>{deploy.label}</Tag>}
                      <Tag color={item.status === 'available' ? 'success' : item.status === 'approval' ? 'warning' : item.status === 'source_unavailable' ? 'default' : 'error'}>
                        {item.status === 'available' ? '可用'
                          : item.status === 'approval' ? '需审批'
                          : item.status === 'source_unavailable' ? '数据源不可用'
                          : '环境未就绪'}
                      </Tag>
                    </Space>
                  </div>
                );
              })}
            </div>
          </>
        ) : (
          <Text type="secondary">能力状态暂未提供</Text>
        )}
      </div>
    </div>
  );
}
