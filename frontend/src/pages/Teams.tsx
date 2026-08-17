import { useEffect, useState } from 'react';
import { Alert, Card, Col, List, Row, Spin, Table, Typography } from 'antd';
import type { Team, TeamDetail } from '@dwp/shared-schema';
import { api } from '../api/client';

export default function Teams() {
  const [teams, setTeams] = useState<Team[]>([]);
  const [detail, setDetail] = useState<TeamDetail | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api
      .listTeams()
      .then((rows) => {
        setTeams(rows);
        if (rows.length > 0) {
          return api.getTeam(rows[0].id).then(setDetail);
        }
      })
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <Spin />;

  return (
    <div>
      <Typography.Title level={3}>协作团队</Typography.Title>
      <Alert
        type="info"
        showIcon
        message="占位页面"
        description="团队任务协作（LLM 拆解、Worker 执行、审批、汇总）将在 Sprint 2 实现。"
        style={{ marginBottom: 16 }}
      />
      <Row gutter={16}>
        <Col span={10}>
          <Card title="团队列表">
            <List
              dataSource={teams}
              renderItem={(team) => (
                <List.Item onClick={() => api.getTeam(team.id).then(setDetail)} style={{ cursor: 'pointer' }}>
                  <List.Item.Meta title={team.name} description={`${team.id} · Leader ${team.leader_employee_id}`} />
                </List.Item>
              )}
            />
          </Card>
        </Col>
        <Col span={14}>
          {detail && (
            <Card title={`${detail.name}（${detail.id}）`}>
              <Typography.Paragraph>{detail.description}</Typography.Paragraph>
              <Table
                rowKey="employee_id"
                size="small"
                pagination={false}
                dataSource={detail.members}
                columns={[
                  { title: '成员', dataIndex: 'employee_id' },
                  { title: '角色', dataIndex: 'role' },
                ]}
              />
            </Card>
          )}
        </Col>
      </Row>
    </div>
  );
}
