# -*- coding: utf-8 -*-
"""
合同Agent
负责合同管理、合同执行、合同归档与模板管理。
监听合同创建、审批、到期及商机赢单、采购审批事件，
管理合同全生命周期。

职责：
- 监听 CONTRACT_CREATED 事件（新合同创建时登记）
- 监听 CONTRACT_APPROVED 事件（合同审批通过时更新状态）
- 监听 CONTRACT_EXPIRED 事件（合同到期时归档）
- 监听 OPPORTUNITY_WON 事件（商机赢单时自动生成销售合同草稿）
- 监听 PURCHASE_ORDER_APPROVED 事件（采购审批通过时生成采购合同）
- 合同管理：创建、审批、执行、完成合同全生命周期
- 模板管理：维护合同模板库
- 到期预警：识别即将到期的合同
- 合同执行：跟踪合同执行进度
"""
import structlog
from typing import Optional
from datetime import datetime, timezone, timedelta
from uuid import uuid4

from app.core.agent.base import BaseAgent, AgentDecision
from app.db.repositories.base import has_db, schedule
from app.db.repositories.treasury_contract_repository import contract_repository
from app.core.event.models import Event, EventType

logger = structlog.get_logger()


class ContractAgent(BaseAgent):
    """
    合同Agent

    职责：
    - 监听合同创建、审批、到期及商机赢单、采购审批事件
    - 合同管理：创建、审批、执行、完成合同全生命周期
    - 模板管理：维护合同模板库，支持基于模板快速创建合同
    - 到期预警：识别即将到期的合同并通知
    - 合同执行记录：跟踪合同执行进度
    """

    def __init__(self):
        super().__init__(
            name="合同Agent",
            domain="contract",
            capabilities=[
                "contract_management",       # 合同管理
                "contract_execution",        # 合同执行
                "contract_archiving",        # 合同归档
                "template_management",       # 模板管理
            ],
            security_clearance="confidential",
        )
        # 合同：contract_id -> 合同详情
        self._contracts: dict[str, dict] = {}
        # 合同模板：template_id -> 模板详情
        self._templates: dict[str, dict] = {}
        # 合同执行记录：list[dict]
        self._execution_records: list[dict] = []

        # 初始化种子数据
        self._seed_data()

    @property
    def description(self) -> str:
        return "合同管理、合同执行、合同归档与模板管理"

    # ========== 初始化 ==========

    def _seed_data(self):
        """注入种子数据：3个合同模板 + 3个示例合同"""
        # 种子合同模板
        seed_templates = [
            {
                "template_id": f"TPL-{str(uuid4())[:8]}",
                "name": "标准销售合同模板",
                "type": "sales",
                "content": (
                    "甲方（卖方）：____\n"
                    "乙方（买方）：____\n\n"
                    "第一条 标的物\n"
                    "甲方向乙方销售以下产品/服务。\n\n"
                    "第二条 价格与付款\n"
                    "合同总金额为____元，乙方应在____日内支付。\n\n"
                    "第三条 交付\n"
                    "甲方应在____日内完成交付。\n\n"
                    "第四条 违约责任\n"
                    "任何一方违约应承担违约责任。\n\n"
                    "第五条 争议解决\n"
                    "本合同争议提交合同签订地法院管辖。"
                ),
                "clauses": [
                    "标的物条款", "价格与付款条款", "交付条款",
                    "违约责任条款", "争议解决条款",
                ],
                "status": "active",
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
            {
                "template_id": f"TPL-{str(uuid4())[:8]}",
                "name": "标准采购合同模板",
                "type": "procurement",
                "content": (
                    "甲方（买方）：____\n"
                    "乙方（卖方）：____\n\n"
                    "第一条 采购内容\n"
                    "甲方向乙方采购以下产品/服务。\n\n"
                    "第二条 价格与付款\n"
                    "采购总金额为____元，甲方验收合格后____日内支付。\n\n"
                    "第三条 交付与验收\n"
                    "乙方应在____日内交付，甲方在收到后____日内验收。\n\n"
                    "第四条 质量保证\n"
                    "乙方保证产品/服务符合约定质量标准。\n\n"
                    "第五条 违约责任\n"
                    "任何一方违约应承担违约责任。"
                ),
                "clauses": [
                    "采购内容条款", "价格与付款条款", "交付与验收条款",
                    "质量保证条款", "违约责任条款",
                ],
                "status": "active",
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
            {
                "template_id": f"TPL-{str(uuid4())[:8]}",
                "name": "标准服务合同模板",
                "type": "service",
                "content": (
                    "甲方（委托方）：____\n"
                    "乙方（服务方）：____\n\n"
                    "第一条 服务内容\n"
                    "乙方为甲方提供以下服务。\n\n"
                    "第二条 服务期限\n"
                    "服务期限自____至____。\n\n"
                    "第三条 服务费用\n"
                    "服务费总额为____元，按____方式支付。\n\n"
                    "第四条 保密条款\n"
                    "双方对服务过程中知悉的商业秘密负有保密义务。\n\n"
                    "第五条 违约责任\n"
                    "任何一方违约应承担违约责任。"
                ),
                "clauses": [
                    "服务内容条款", "服务期限条款", "服务费用条款",
                    "保密条款", "违约责任条款",
                ],
                "status": "active",
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
        ]
        for tpl in seed_templates:
            self._templates[tpl["template_id"]] = tpl

        # 种子示例合同
        now = datetime.now(timezone.utc)
        seed_contracts = [
            {
                "contract_id": f"CTR-{str(uuid4())[:8]}",
                "title": "2024年度办公设备采购合同",
                "type": "procurement",
                "counterparty": "华为技术有限公司",
                "amount": 580000.0,
                "department": "行政部",
                "start_date": (now - timedelta(days=60)).strftime("%Y-%m-%d"),
                "end_date": (now + timedelta(days=20)).strftime("%Y-%m-%d"),
                "terms": "采购办公电脑及网络设备，含一年质保。",
                "status": "executing",
                "approved_by": "张总监",
                "approval_comment": "同意采购",
                "approved_at": (now - timedelta(days=62)).isoformat(),
                "completion_notes": "",
                "created_at": (now - timedelta(days=65)).isoformat(),
                "updated_at": (now - timedelta(days=60)).isoformat(),
            },
            {
                "contract_id": f"CTR-{str(uuid4())[:8]}",
                "title": "ERP系统实施服务合同",
                "type": "service",
                "counterparty": "用友网络科技股份有限公司",
                "amount": 1200000.0,
                "department": "信息技术部",
                "start_date": (now - timedelta(days=30)).strftime("%Y-%m-%d"),
                "end_date": (now + timedelta(days=150)).strftime("%Y-%m-%d"),
                "terms": "ERP系统需求分析、开发实施、培训及运维服务。",
                "status": "executing",
                "approved_by": "李副总裁",
                "approval_comment": "重大项目，按计划推进",
                "approved_at": (now - timedelta(days=32)).isoformat(),
                "completion_notes": "",
                "created_at": (now - timedelta(days=35)).isoformat(),
                "updated_at": (now - timedelta(days=30)).isoformat(),
            },
            {
                "contract_id": f"CTR-{str(uuid4())[:8]}",
                "title": "产品销售合同-华东区经销商",
                "type": "sales",
                "counterparty": "上海华联商贸有限公司",
                "amount": 860000.0,
                "department": "销售部",
                "start_date": (now - timedelta(days=10)).strftime("%Y-%m-%d"),
                "end_date": (now + timedelta(days=355)).strftime("%Y-%m-%d"),
                "terms": "甲方授权乙方为华东区经销商，年度销售目标1000万元。",
                "status": "approved",
                "approved_by": "王总经理",
                "approval_comment": "同意签订",
                "approved_at": (now - timedelta(days=11)).isoformat(),
                "completion_notes": "",
                "created_at": (now - timedelta(days=15)).isoformat(),
                "updated_at": (now - timedelta(days=10)).isoformat(),
            },
        ]
        for contract in seed_contracts:
            self._contracts[contract["contract_id"]] = contract

    async def initialize(self):
        """初始化Agent：订阅合同与业务事件"""
        self.subscribe_events(
            event_types=[
                EventType.CONTRACT_CREATED,
                EventType.CONTRACT_APPROVED,
                EventType.CONTRACT_EXPIRED,
                EventType.OPPORTUNITY_WON,
                EventType.PURCHASE_ORDER_APPROVED,
            ],
            handler=self._handle_event,
        )
        logger.info("contract_agent.initialized", agent=self.name,
                    contracts=len(self._contracts),
                    templates=len(self._templates))

    async def hydrate_from_db(self) -> bool:
        """
        启动水合：DB 优先、内存种子兜底、空库回播种（路线图 2.3 批次二）。

        合同模板与执行记录属静态/运行期辅助数据，暂不持久化（Phase 3）。
        """
        if not has_db():
            return False

        hydrated = False
        contracts = await contract_repository.list_all()
        if contracts:
            self._contracts = {
                c["contract_id"]: {**self._contracts.get(c["contract_id"], {}), **c}
                for c in contracts
            }
            hydrated = True
        elif self._contracts:
            for c in list(self._contracts.values()):
                await contract_repository.upsert(c, "contract_id")
            logger.info("contract_agent.contracts_seeded_to_db",
                        count=len(self._contracts))
            hydrated = True

        logger.info("contract_agent.hydrated",
                    from_db=hydrated, contracts=len(self._contracts))
        return hydrated

    async def _handle_event(self, event: Event):
        """事件处理入口（包装异常处理）"""
        try:
            await self.process_event(event)
        except Exception as e:
            self._error_count += 1
            self.circuit_breaker.record_failure()
            logger.error("contract_agent.event_error",
                         agent=self.name, error=str(e))

    # ========== 事件处理 ==========

    async def process_event(self, event: Event):
        """处理接收到的事件"""
        if event.event_type == EventType.CONTRACT_CREATED:
            # 外部合同创建事件：确保登记
            return await self._handle_contract_created(event.event_data)
        elif event.event_type == EventType.CONTRACT_APPROVED:
            # 合同审批通过事件：更新状态
            return await self._handle_contract_approved(event.event_data)
        elif event.event_type == EventType.CONTRACT_EXPIRED:
            # 合同到期事件：归档
            return await self._handle_contract_expired(event.event_data)
        elif event.event_type == EventType.OPPORTUNITY_WON:
            # 商机赢单：自动生成销售合同草稿
            return await self._handle_opportunity_won(event.event_data)
        elif event.event_type == EventType.PURCHASE_ORDER_APPROVED:
            # 采购审批通过：自动生成采购合同草稿
            return await self._handle_po_approved(event.event_data)

    async def _handle_contract_created(self, data: dict):
        """处理合同创建事件"""
        contract_id = data.get("contract_id", "")
        if contract_id and contract_id not in self._contracts:
            # 外部创建的合同，登记到本地
            contract = {
                "contract_id": contract_id,
                "title": data.get("title", "外部创建合同"),
                "type": data.get("contract_type", data.get("type", "general")),
                "counterparty": data.get("counterparty", ""),
                "amount": float(data.get("amount", 0) or 0),
                "department": data.get("department", ""),
                "start_date": data.get("start_date", ""),
                "end_date": data.get("end_date", ""),
                "terms": data.get("terms", data.get("contract_text", "")),
                "status": "draft",
                "approved_by": "",
                "approval_comment": "",
                "approved_at": None,
                "completion_notes": "",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            self._contracts[contract_id] = contract
            logger.info("contract_agent.external_created",
                        contract_id=contract_id)
            return contract
        return None

    async def _handle_contract_approved(self, data: dict):
        """处理合同审批通过事件"""
        contract_id = data.get("contract_id", "")
        if contract_id in self._contracts:
            self._contracts[contract_id]["status"] = "approved"
            self._contracts[contract_id]["approved_by"] = data.get(
                "approved_by", ""
            )
            self._contracts[contract_id]["approval_comment"] = data.get(
                "comment", ""
            )
            self._contracts[contract_id]["approved_at"] = (
                datetime.now(timezone.utc).isoformat()
            )
            self._contracts[contract_id]["updated_at"] = (
                datetime.now(timezone.utc).isoformat()
            )
            logger.info("contract_agent.approved_via_event",
                        contract_id=contract_id)
            return self._contracts[contract_id]
        return None

    async def _handle_contract_expired(self, data: dict):
        """处理合同到期事件"""
        contract_id = data.get("contract_id", "")
        if contract_id in self._contracts:
            self._contracts[contract_id]["status"] = "expired"
            self._contracts[contract_id]["updated_at"] = (
                datetime.now(timezone.utc).isoformat()
            )
            logger.info("contract_agent.expired_via_event",
                        contract_id=contract_id)
            return self._contracts[contract_id]
        return None

    async def _handle_opportunity_won(self, data: dict):
        """商机赢单：自动生成销售合同草稿"""
        customer = data.get("customer_name", data.get("customer", ""))
        amount = float(data.get("amount", data.get("deal_amount", 0)) or 0)
        opportunity_id = data.get("opportunity_id", "")

        now = datetime.now(timezone.utc)
        contract = await self.create_contract(
            title=f"销售合同-{customer}-{now.strftime('%Y%m%d')}",
            type="sales",
            counterparty=customer,
            amount=amount,
            department="销售部",
            start_date=now.strftime("%Y-%m-%d"),
            end_date=(now + timedelta(days=365)).strftime("%Y-%m-%d"),
            terms=f"基于商机{opportunity_id}自动生成，具体条款待协商。",
        )
        return contract

    async def _handle_po_approved(self, data: dict):
        """采购审批通过：自动生成采购合同草稿"""
        supplier = data.get("supplier_name", data.get("supplier_id", ""))
        amount = float(data.get("total_amount", data.get("amount", 0)) or 0)
        po_id = data.get("po_id", "")

        now = datetime.now(timezone.utc)
        contract = await self.create_contract(
            title=f"采购合同-{supplier}-{now.strftime('%Y%m%d')}",
            type="procurement",
            counterparty=supplier,
            amount=amount,
            department="采购部",
            start_date=now.strftime("%Y-%m-%d"),
            end_date=(now + timedelta(days=180)).strftime("%Y-%m-%d"),
            terms=f"基于采购订单{po_id}自动生成，具体条款待协商。",
        )
        return contract

    # ========== 合同管理 ==========

    def get_contracts(
        self, status: Optional[str] = None,
        type: Optional[str] = None,
        department: Optional[str] = None,
    ) -> list[dict]:
        """获取合同列表，可按状态、类型、部门过滤"""
        contracts = list(self._contracts.values())
        if status:
            contracts = [
                c for c in contracts if c.get("status") == status
            ]
        if type:
            contracts = [
                c for c in contracts if c.get("type") == type
            ]
        if department:
            contracts = [
                c for c in contracts if c.get("department") == department
            ]
        return contracts

    def get_contract(self, contract_id: str) -> Optional[dict]:
        """获取单个合同详情"""
        return self._contracts.get(contract_id)

    async def create_contract(
        self, title: str, type: str, counterparty: str,
        amount: float, department: str,
        start_date: str, end_date: str, terms: str,
    ) -> dict:
        """创建新合同"""
        contract_id = f"CTR-{str(uuid4())[:8]}"
        contract = {
            "contract_id": contract_id,
            "title": title,
            "type": type,
            "counterparty": counterparty,
            "amount": float(amount),
            "department": department,
            "start_date": start_date,
            "end_date": end_date,
            "terms": terms,
            "status": "draft",
            "approved_by": "",
            "approval_comment": "",
            "approved_at": None,
            "completion_notes": "",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        self._contracts[contract_id] = contract
        schedule(
            contract_repository.upsert(contract, "contract_id"),
            "contract_repo",
        )

        # 发布合同创建事件
        await self._publish_event(
            EventType.CONTRACT_CREATED,
            {
                "contract_id": contract_id,
                "title": title,
                "type": type,
                "counterparty": counterparty,
                "amount": amount,
                "department": department,
                "start_date": start_date,
                "end_date": end_date,
                "terms": terms,
            },
        )

        # 记录决策
        decision = AgentDecision(
            decision_type="contract_creation",
            reasoning=(
                f"创建合同：{title}（类型：{type}，对方：{counterparty}，"
                f"金额：{amount:.2f}元，部门：{department}）"
            ),
            action=f"创建合同{contract_id}",
            confidence=0.9,
            requires_human_approval=(amount > 1000000),
            context={
                "contract_id": contract_id,
                "type": type,
                "amount": amount,
            },
        )
        await self.record_decision(decision)

        logger.info("contract_agent.created",
                    contract_id=contract_id, title=title,
                    counterparty=counterparty, amount=amount)
        return contract

    async def approve_contract(
        self, contract_id: str, approved_by: str, comment: str,
    ) -> dict:
        """审批合同"""
        contract = self._contracts.get(contract_id)
        if not contract:
            return {"error": "contract_not_found",
                    "contract_id": contract_id}

        if contract["status"] != "draft":
            return {"error": "invalid_status",
                    "contract_id": contract_id,
                    "current_status": contract["status"],
                    "message": "只有草拟状态的合同可以审批"}

        contract["status"] = "approved"
        schedule(
            contract_repository.update_status(
                contract["contract_id"], "approved",
                extra={
                    "approved_by": contract.get("approved_by") or None,
                    "approval_comment": contract.get("approval_comment") or None,
                    "approved_at": contract.get("approved_at"),
                },
            ),
            "contract_repo",
        )
        contract["approved_by"] = approved_by
        contract["approval_comment"] = comment
        contract["approved_at"] = datetime.now(timezone.utc).isoformat()
        contract["updated_at"] = datetime.now(timezone.utc).isoformat()

        # 发布合同审批事件
        await self._publish_event(
            EventType.CONTRACT_APPROVED,
            {
                "contract_id": contract_id,
                "approved_by": approved_by,
                "comment": comment,
                "title": contract["title"],
                "amount": contract["amount"],
            },
        )

        # 记录决策
        decision = AgentDecision(
            decision_type="contract_approval",
            reasoning=(
                f"审批合同'{contract['title']}'，审批人：{approved_by}，"
                f"意见：{comment}"
            ),
            action=f"审批通过合同{contract_id}",
            confidence=0.9,
            requires_human_approval=False,
            context={
                "contract_id": contract_id,
                "approved_by": approved_by,
                "amount": contract["amount"],
            },
        )
        await self.record_decision(decision)

        logger.info("contract_agent.approved",
                    contract_id=contract_id, approved_by=approved_by)
        return contract

    async def execute_contract(self, contract_id: str) -> dict:
        """执行合同"""
        contract = self._contracts.get(contract_id)
        if not contract:
            return {"error": "contract_not_found",
                    "contract_id": contract_id}

        if contract["status"] != "approved":
            return {"error": "invalid_status",
                    "contract_id": contract_id,
                    "current_status": contract["status"],
                    "message": "只有已审批的合同可以执行"}

        contract["status"] = "executing"
        schedule(
            contract_repository.update_status(contract["contract_id"], "executing"),
            "contract_repo",
        )
        contract["updated_at"] = datetime.now(timezone.utc).isoformat()

        # 记录执行记录
        record = {
            "record_id": f"EXE-{str(uuid4())[:8]}",
            "contract_id": contract_id,
            "action": "execute",
            "description": f"合同'{contract['title']}'开始执行",
            "executed_at": datetime.now(timezone.utc).isoformat(),
        }
        self._execution_records.append(record)

        # 记录决策
        decision = AgentDecision(
            decision_type="contract_execution",
            reasoning=(
                f"开始执行合同'{contract['title']}'"
                f"（金额{contract['amount']:.2f}元）"
            ),
            action=f"启动合同执行{contract_id}",
            confidence=0.9,
            requires_human_approval=False,
            context={
                "contract_id": contract_id,
                "amount": contract["amount"],
            },
        )
        await self.record_decision(decision)

        logger.info("contract_agent.executing",
                    contract_id=contract_id)
        return contract

    async def complete_contract(
        self, contract_id: str, completion_notes: str,
    ) -> dict:
        """完成合同"""
        contract = self._contracts.get(contract_id)
        if not contract:
            return {"error": "contract_not_found",
                    "contract_id": contract_id}

        if contract["status"] != "executing":
            return {"error": "invalid_status",
                    "contract_id": contract_id,
                    "current_status": contract["status"],
                    "message": "只有执行中的合同可以完成"}

        contract["status"] = "completed"
        schedule(
            contract_repository.update_status(
                contract["contract_id"], "completed",
                extra={"completion_notes": contract.get("completion_notes") or None},
            ),
            "contract_repo",
        )
        contract["completion_notes"] = completion_notes
        contract["updated_at"] = datetime.now(timezone.utc).isoformat()

        # 记录执行记录
        record = {
            "record_id": f"EXE-{str(uuid4())[:8]}",
            "contract_id": contract_id,
            "action": "complete",
            "description": f"合同'{contract['title']}'已完成：{completion_notes}",
            "executed_at": datetime.now(timezone.utc).isoformat(),
        }
        self._execution_records.append(record)

        # 记录决策
        decision = AgentDecision(
            decision_type="contract_completion",
            reasoning=(
                f"完成合同'{contract['title']}'，"
                f"完成说明：{completion_notes}"
            ),
            action=f"归档合同{contract_id}",
            confidence=0.9,
            requires_human_approval=False,
            context={
                "contract_id": contract_id,
                "completion_notes": completion_notes,
            },
        )
        await self.record_decision(decision)

        logger.info("contract_agent.completed",
                    contract_id=contract_id)
        return contract

    # ========== 模板管理 ==========

    def get_templates(self, type: Optional[str] = None) -> list[dict]:
        """获取合同模板列表，可按类型过滤"""
        templates = list(self._templates.values())
        if type:
            templates = [
                t for t in templates if t.get("type") == type
            ]
        return templates

    async def create_template(
        self, name: str, type: str,
        content: str, clauses: list[str],
    ) -> dict:
        """创建合同模板"""
        template_id = f"TPL-{str(uuid4())[:8]}"
        template = {
            "template_id": template_id,
            "name": name,
            "type": type,
            "content": content,
            "clauses": clauses,
            "status": "active",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        self._templates[template_id] = template

        logger.info("contract_agent.template_created",
                    template_id=template_id, name=name, type=type)
        return template

    # ========== 到期预警 ==========

    def get_expiring_contracts(self, days: int = 30) -> list[dict]:
        """
        获取即将到期的合同

        参数：
        - days: 提前预警天数（默认30天）

        返回：在指定天数内到期的、状态为执行中或已审批的合同列表
        """
        now = datetime.now(timezone.utc)
        threshold = now + timedelta(days=days)
        expiring = []

        for contract in self._contracts.values():
            if contract["status"] not in ("executing", "approved"):
                continue
            end_date_str = contract.get("end_date", "")
            if not end_date_str:
                continue
            try:
                # 支持 YYYY-MM-DD 格式
                end_date = datetime.strptime(
                    end_date_str, "%Y-%m-%d"
                ).replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                try:
                    # 支持 ISO 格式
                    end_date = datetime.fromisoformat(end_date_str)
                    if end_date.tzinfo is None:
                        end_date = end_date.replace(tzinfo=timezone.utc)
                except (ValueError, TypeError):
                    continue

            if now <= end_date <= threshold:
                days_remaining = (end_date - now).days
                expiring.append({
                    **contract,
                    "days_remaining": days_remaining,
                    "expiry_date": end_date_str,
                })

        # 按剩余天数排序
        expiring.sort(key=lambda c: c["days_remaining"])
        return expiring

    # ========== 决策 ==========

    async def make_decision(self, context: dict) -> AgentDecision:
        """做出合同管理决策"""
        action_type = context.get("action_type", "create")

        if action_type == "create":
            amount = float(context.get("amount", 0) or 0)
            return AgentDecision(
                decision_type="contract_creation",
                reasoning=(
                    f"创建合同：{context.get('title', '')}"
                    f"（金额{amount:.2f}元）"
                ),
                action="创建合同草稿",
                confidence=0.9,
                requires_human_approval=(amount > 1000000),
                context=context,
            )

        elif action_type == "approve":
            contract_id = context.get("contract_id", "")
            contract = self._contracts.get(contract_id)
            if not contract:
                return AgentDecision(
                    decision_type="error",
                    reasoning=f"合同{contract_id}不存在",
                    action="无法做出审批决策",
                    confidence=0.0,
                    context=context,
                )
            amount = contract["amount"]
            if amount > 5000000:
                return AgentDecision(
                    decision_type="contract_approval",
                    reasoning=(
                        f"合同'{contract['title']}'金额"
                        f"{amount:.2f}元超过500万，需高层审批"
                    ),
                    action="提交高层审批",
                    confidence=0.9,
                    requires_human_approval=True,
                    context=context,
                )
            return AgentDecision(
                decision_type="contract_approval",
                reasoning=(
                    f"合同'{contract['title']}'金额{amount:.2f}元，"
                    f"常规审批流程"
                ),
                action="常规审批通过",
                confidence=0.9,
                requires_human_approval=False,
                context=context,
            )

        elif action_type == "execute":
            return AgentDecision(
                decision_type="contract_execution",
                reasoning=f"启动合同执行：{context.get('contract_id', '')}",
                action="开始执行合同",
                confidence=0.9,
                requires_human_approval=False,
                context=context,
            )

        elif action_type == "complete":
            return AgentDecision(
                decision_type="contract_completion",
                reasoning=(
                    f"完成合同：{context.get('contract_id', '')}，"
                    f"说明：{context.get('completion_notes', '')}"
                ),
                action="归档已完成合同",
                confidence=0.9,
                requires_human_approval=False,
                context=context,
            )

        return AgentDecision(
            decision_type="contract_general",
            reasoning="常规合同管理事务",
            action="处理合同管理事务",
            confidence=0.75,
            requires_human_approval=False,
            context=context,
        )

    async def on_tick(self):
        """周期性任务：检查即将到期的合同"""
        expiring = self.get_expiring_contracts(days=30)
        if expiring:
            for contract in expiring:
                if contract["days_remaining"] <= 7:
                    logger.warning(
                        "contract_agent.expiring_soon",
                        contract_id=contract["contract_id"],
                        title=contract["title"],
                        days_remaining=contract["days_remaining"],
                    )

    # ========== 统计 ==========

    def get_stats(self) -> dict:
        """获取合同Agent统计数据"""
        contracts = list(self._contracts.values())

        status_distribution: dict[str, int] = {}
        type_distribution: dict[str, int] = {}
        total_amount = 0.0

        for contract in contracts:
            status = contract.get("status", "unknown")
            status_distribution[status] = (
                status_distribution.get(status, 0) + 1
            )
            ctype = contract.get("type", "unknown")
            type_distribution[ctype] = (
                type_distribution.get(ctype, 0) + 1
            )
            total_amount += contract.get("amount", 0)

        expiring_count = len(self.get_expiring_contracts(days=30))
        executing_count = status_distribution.get("executing", 0)
        completed_count = status_distribution.get("completed", 0)

        return {
            "contracts_total": len(contracts),
            "contracts_executing": executing_count,
            "contracts_completed": completed_count,
            "contracts_expiring_30d": expiring_count,
            "status_distribution": status_distribution,
            "type_distribution": type_distribution,
            "total_contract_value": round(total_amount, 2),
            "templates_total": len(self._templates),
            "execution_records_total": len(self._execution_records),
        }
