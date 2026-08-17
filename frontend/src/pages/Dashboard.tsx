import { useEffect, useState } from 'react';
import { Card, Col, Row, Spin, Statistic, Typography } from 'antd';
import type { Employee } from '@dwp/shared-schema';
import { api } from '../api/client';

export default function Dashboard() {
  const [employees, setEmployees] = useState<Employee[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api
      .listEmployees()
      .then(setEmployees)
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <Spin />;

  const count = (type: string) => employees.filter((e) => e.type === type).length;

  return (
    <div>
      <Typography.Title level={3}>首页</Typography.Title>
      <Row gutter={16}>
        <Col span={6}>
          <Card>
            <Statistic title="数字员工总数" value={employees.length} />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic title="数字分身" value={count('twin')} />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic title="虚拟员工" value={count('virtual')} />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic title="RPA" value={count('rpa')} />
          </Card>
        </Col>
      </Row>
      <Card style={{ marginTop: 16 }} title="Sprint 1 范围">
        <Typography.Paragraph>
          当前为基础工程骨架：员工 / 插件 / 策略 / 审计 CRUD，以及只读的知识库与团队信息。问答、团队任务、Harness 与
          Sandbox 将在后续 Sprint 实现。
        </Typography.Paragraph>
      </Card>
    </div>
  );
}
