"""
Agent 编排压测脚本（路线图 Phase E / E5，部署后对运行中服务执行）
==================================================================

前置：pip install locust
执行：locust -f tests/perf/locustfile.py --host=http://<目标地址> \
             --users 50 --spawn-rate 5 --run-time 3m
说明：
- 压测前需在 ERP 后端配置有效的 ERP_LLM_API_KEY（agent-command 走
  真实编排；无 LLM 时走降级路径，仅压 HTTP/DB 层）；
- 关注指标：p95 响应时间 < 5s（agent_slo_response_time_ms）、
  无 5xx、Checkpointer 无锁等待超时（后端日志无 database is locked）。
"""
import json
import os
import random

from locust import HttpUser, between, task

USERNAME = os.environ.get("LOCUST_USER", "admin")
PASSWORD = os.environ.get("LOCUST_PASS", "admin123")

SUPERVISOR_QUERIES = [
    "汇总一下报销和资金的情况",
    "查一下最近的报销单",
    "看看资金余额和支付排程",
    "技术部有哪些人",
    "A级客户有哪些",
]


class ERPUser(HttpUser):
    wait_time = between(1, 3)

    def on_start(self):
        resp = self.client.post("/api/v1/auth/login",
                                json={"username": USERNAME, "password": PASSWORD})
        token = resp.json().get("access_token", "")
        self.headers = {"Authorization": f"Bearer {token}"}
        channels = self.client.get("/api/v1/messaging/channels",
                                   headers=self.headers).json().get("channels", [])
        self.sup_channel = next(
            (c["channel_id"] for c in channels if "总调度" in str(c.get("name") or "")), None)

    @task(3)
    def supervisor_cross_domain(self):
        """跨域协同指令（LLM 分类+委派子域工具）。"""
        if not self.sup_channel:
            return
        self.client.post(
            f"/api/v1/messaging/channels/{self.sup_channel}/agent-command",
            headers=self.headers,
            json={"command": random.choice(SUPERVISOR_QUERIES)},
            timeout=120,
        )

    @task(2)
    def workflow_start(self):
        """报销审批流启动（低额自动完成）。"""
        self.client.post(
            "/api/v1/agent-ops/workflows/reimbursement_approval/start",
            headers=self.headers,
            json={"input": {"employee_name": f"压测用户{random.randint(1, 999)}",
                            "category": "office", "amount": round(random.uniform(50, 800), 2),
                            "description": "压测自动报销", "has_receipt": True}},
            timeout=60,
        )

    @task(1)
    def approval_list(self):
        self.client.get("/api/v1/agent-ops/approvals?status=pending",
                        headers=self.headers, timeout=30)
