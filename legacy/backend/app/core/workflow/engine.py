"""
审批工作流引擎核心
==================

实现三级审批工作流：
1. **普通审批（ordinary）**：部门经理 → 完成，1个工作日超时
2. **敏感审批（sensitive）**：部门经理 → C-level → 完成，2个工作日超时
3. **高危审批（high_risk）**：部门经理 → C-level → 董事长 → 完成，3个工作日超时

审批状态流转：
    pending → in_review → approved / rejected / expired / cancelled

使用方式::

    from app.core.workflow import workflow_engine

    # 创建审批请求
    req = workflow_engine.create_request(
        requester_id="user_001",
        request_type="permission_grant",
        title="申请 treasury:approve 权限",
        risk_level="sensitive",
        details={"permission": "treasury:approve", "target_user": "user_002"},
        approver_chain=["user_fin_manager", "user_cfo"],
    )

    # 审批
    workflow_engine.approve(req["request_id"], "user_fin_manager", "同意")
    workflow_engine.approve(req["request_id"], "user_cfo", "批准")
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from uuid import uuid4

import structlog

logger = structlog.get_logger()


# ===================================================================
# 常量定义
# ===================================================================

class RiskLevel:
    """风险等级"""
    ORDINARY = "ordinary"
    SENSITIVE = "sensitive"
    HIGH_RISK = "high_risk"


class RequestStatus:
    """审批请求状态"""
    PENDING = "pending"          # 待审批（等待第一审批人）
    IN_REVIEW = "in_review"      # 审批中（部分审批已通过）
    APPROVED = "approved"        # 全部通过
    REJECTED = "rejected"        # 被拒绝
    EXPIRED = "expired"          # 超时未处理
    CANCELLED = "cancelled"        # 申请人撤回


class RequestType:
    """审批请求类型"""
    PERMISSION_GRANT = "permission_grant"
    PERMISSION_REVOKE = "permission_revoke"
    PERMISSION_EMERGENCY_REVOKE = "permission_emergency_revoke"
    PERMISSION_VIOLATION_REVOKE = "permission_violation_revoke"
    REIMBURSEMENT = "reimbursement"
    PROCUREMENT = "procurement"
    BUDGET = "budget"
    TREASURY_TRANSFER = "treasury_transfer"
    CONTRACT = "contract"
    CUSTOM = "custom"


# 风险等级 → 超时工作日
RISK_LEVEL_TIMEOUT_DAYS: dict[str, int] = {
    RiskLevel.ORDINARY: 1,
    RiskLevel.SENSITIVE: 2,
    RiskLevel.HIGH_RISK: 3,
}

# 风险等级 → 默认审批链长度
RISK_LEVEL_CHAIN_LENGTH: dict[str, int] = {
    RiskLevel.ORDINARY: 1,    # 部门经理
    RiskLevel.SENSITIVE: 2,    # 部门经理 → C-level
    RiskLevel.HIGH_RISK: 3,    # 部门经理 → C-level → 董事长
}


class WorkflowEngine:
    """
    审批工作流引擎

    内存存储，零依赖启动。

    存储结构：
    - ``_requests``: ``{request_id: request_dict}``
    - ``_approvals``: ``{request_id: [approval_record, ...]}`` 审批记录
    """

    def __init__(self):
        self._requests: dict[str, dict[str, Any]] = {}
        self._approvals: dict[str, list[dict[str, Any]]] = {}

    # ===================================================================
    # 创建审批请求
    # ===================================================================

    def create_request(
        self,
        requester_id: str,
        request_type: str,
        title: str,
        risk_level: str = RiskLevel.ORDINARY,
        details: Optional[dict] = None,
        approver_chain: Optional[list[str]] = None,
        timeout_days: Optional[int] = None,
    ) -> dict[str, Any]:
        """
        创建审批请求。

        Args:
            requester_id:    申请人 user_id
            request_type:    请求类型（见 RequestType）
            title:           申请标题
            risk_level:      风险等级（ordinary/sensitive/high_risk）
            details:         申请详情（业务数据）
            approver_chain:  审批人 user_id 列表（按顺序）
            timeout_days:    超时天数（不指定则按风险等级默认）

        Returns:
            审批请求信息字典
        """
        request_id = f"wf_{uuid4().hex[:12]}"
        now = datetime.now(timezone.utc)

        # 自动确定超时天数
        if timeout_days is None:
            timeout_days = RISK_LEVEL_TIMEOUT_DAYS.get(risk_level, 1)

        # 自动生成审批链（如果未指定）
        if approver_chain is None:
            approver_chain = self._auto_approver_chain(requester_id, risk_level)

        expires_at = now + timedelta(days=timeout_days)

        request = {
            "request_id": request_id,
            "requester_id": requester_id,
            "request_type": request_type,
            "title": title,
            "risk_level": risk_level,
            "details": details or {},
            "approver_chain": approver_chain,
            "current_step": 0,
            "status": RequestStatus.PENDING,
            "created_at": now,
            "updated_at": now,
            "expires_at": expires_at,
            "completed_at": None,
        }

        self._requests[request_id] = request
        self._approvals[request_id] = []

        logger.info(
            "workflow.request_created",
            request_id=request_id,
            requester_id=requester_id,
            request_type=request_type,
            risk_level=risk_level,
            approver_chain=approver_chain,
            expires_at=expires_at.isoformat(),
        )
        return self._request_to_dict(request)

    # ===================================================================
    # 审批操作
    # ===================================================================

    def approve(
        self,
        request_id: str,
        approver_id: str,
        comment: str = "",
    ) -> dict[str, Any]:
        """
        审批通过。

        推进审批链：当前步骤通过后自动进入下一步，
        全部步骤通过后状态变为 approved。

        Args:
            request_id:  审批请求ID
            approver_id: 审批人 user_id
            comment:     审批意见

        Returns:
            更新后的审批请求信息

        Raises:
            KeyError:  请求不存在
            ValueError: 请求状态不允许审批、审批人无权审批当前步骤
        """
        request = self._get_request_or_raise(request_id)

        # 检查请求状态
        if request["status"] not in (RequestStatus.PENDING, RequestStatus.IN_REVIEW):
            raise ValueError(
                f"请求状态不允许审批: {request['status']}"
            )

        # 检查超时
        if self._is_expired(request):
            request["status"] = RequestStatus.EXPIRED
            logger.warning("workflow.request_expired_on_approve", request_id=request_id)
            raise ValueError("请求已超时")

        # 检查审批人是否匹配当前步骤
        chain = request["approver_chain"]
        step = request["current_step"]
        if step >= len(chain):
            raise ValueError("审批链已完成，无需更多审批")

        expected_approver = chain[step]
        if approver_id != expected_approver:
            raise ValueError(
                f"当前步骤应由 {expected_approver} 审批，而非 {approver_id}"
            )

        # 记录审批
        now = datetime.now(timezone.utc)
        approval_record = {
            "step": step,
            "approver_id": approver_id,
            "action": "approve",
            "comment": comment,
            "timestamp": now,
        }
        self._approvals[request_id].append(approval_record)

        # 推进步骤
        request["current_step"] = step + 1
        request["updated_at"] = now

        # 检查是否完成全部审批
        if request["current_step"] >= len(chain):
            request["status"] = RequestStatus.APPROVED
            request["completed_at"] = now
            logger.info(
                "workflow.request_approved",
                request_id=request_id,
                steps_completed=request["current_step"],
            )
        else:
            request["status"] = RequestStatus.IN_REVIEW
            logger.info(
                "workflow.step_approved",
                request_id=request_id,
                step=step,
                next_step=request["current_step"],
                next_approver=chain[request["current_step"]],
            )

        return self._request_to_dict(request)

    def reject(
        self,
        request_id: str,
        approver_id: str,
        comment: str = "",
    ) -> dict[str, Any]:
        """
        审批拒绝。

        任何步骤的审批人都可以拒绝，请求状态立即变为 rejected。

        Args:
            request_id:  审批请求ID
            approver_id: 审批人 user_id
            comment:     拒绝理由

        Returns:
            更新后的审批请求信息
        """
        request = self._get_request_or_raise(request_id)

        if request["status"] not in (RequestStatus.PENDING, RequestStatus.IN_REVIEW):
            raise ValueError(f"请求状态不允许审批: {request['status']}")

        if self._is_expired(request):
            request["status"] = RequestStatus.EXPIRED
            raise ValueError("请求已超时")

        # 检查审批人是否在审批链中
        chain = request["approver_chain"]
        step = request["current_step"]
        expected_approver = chain[step] if step < len(chain) else None

        # 当前步骤的审批人可以拒绝
        # 也允许审批链中后续审批人提前拒绝
        if approver_id not in chain:
            raise ValueError(f"审批人 {approver_id} 不在审批链中")

        now = datetime.now(timezone.utc)
        approval_record = {
            "step": step,
            "approver_id": approver_id,
            "action": "reject",
            "comment": comment,
            "timestamp": now,
        }
        self._approvals[request_id].append(approval_record)

        request["status"] = RequestStatus.REJECTED
        request["completed_at"] = now
        request["updated_at"] = now

        logger.info(
            "workflow.request_rejected",
            request_id=request_id,
            approver_id=approver_id,
            comment=comment,
        )
        return self._request_to_dict(request)

    def cancel(self, request_id: str, requester_id: str, reason: str = "") -> dict[str, Any]:
        """
        申请人撤回审批请求。

        只有申请人本人可以撤回，且请求状态必须是 pending 或 in_review。

        Args:
            request_id:   审批请求ID
            requester_id:  申请人 user_id（验证身份）
            reason:        撤回原因

        Returns:
            更新后的审批请求信息
        """
        request = self._get_request_or_raise(request_id)

        if request["requester_id"] != requester_id:
            raise ValueError("只有申请人可以撤回请求")

        if request["status"] not in (RequestStatus.PENDING, RequestStatus.IN_REVIEW):
            raise ValueError(f"请求状态不允许撤回: {request['status']}")

        now = datetime.now(timezone.utc)
        request["status"] = RequestStatus.CANCELLED
        request["completed_at"] = now
        request["updated_at"] = now

        logger.info(
            "workflow.request_cancelled",
            request_id=request_id,
            requester_id=requester_id,
            reason=reason,
        )
        return self._request_to_dict(request)

    # ===================================================================
    # 查询接口
    # ===================================================================

    def get_request(self, request_id: str) -> Optional[dict[str, Any]]:
        """获取审批请求详情（含审批记录）。"""
        request = self._requests.get(request_id)
        if request is None:
            return None
        result = self._request_to_dict(request)
        result["approvals"] = [
            {
                **{k: v.isoformat() if isinstance(v, datetime) else v
                   for k, v in rec.items()},
            }
            for rec in self._approvals.get(request_id, [])
        ]
        return result

    def list_requests(
        self,
        requester_id: Optional[str] = None,
        approver_id: Optional[str] = None,
        status: Optional[str] = None,
        request_type: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """
        查询审批请求列表。

        支持按申请人、审批人、状态、类型过滤。

        Args:
            requester_id: 按申请人过滤
            approver_id:  按当前待审批人过滤
            status:       按状态过滤
            request_type: 按类型过滤

        Returns:
            审批请求列表
        """
        results = []
        for request in self._requests.values():
            if requester_id and request["requester_id"] != requester_id:
                continue
            if status and request["status"] != status:
                continue
            if request_type and request["request_type"] != request_type:
                continue
            if approver_id:
                chain = request["approver_chain"]
                step = request["current_step"]
                # 当前待审批人匹配
                if step < len(chain) and chain[step] != approver_id:
                    continue
                elif step >= len(chain):
                    continue

            results.append(self._request_to_dict(request))

        # 按创建时间降序
        results.sort(key=lambda r: r["created_at"], reverse=True)
        return results

    def get_pending_approvals(self, approver_id: str) -> list[dict[str, Any]]:
        """获取指定审批人的待审批列表。"""
        return self.list_requests(approver_id=approver_id, status=RequestStatus.PENDING) + \
               self.list_requests(approver_id=approver_id, status=RequestStatus.IN_REVIEW)

    # ===================================================================
    # 超时与清理
    # ===================================================================

    def check_expired(self) -> int:
        """
        扫描并标记超时请求。

        Returns:
            标记为超时的请求数量
        """
        now = datetime.now(timezone.utc)
        expired_count = 0
        for request in self._requests.values():
            if request["status"] in (RequestStatus.PENDING, RequestStatus.IN_REVIEW):
                if request["expires_at"] < now:
                    request["status"] = RequestStatus.EXPIRED
                    request["completed_at"] = now
                    request["updated_at"] = now
                    expired_count += 1
                    logger.info(
                        "workflow.request_expired",
                        request_id=request["request_id"],
                        expired_at=request["expires_at"].isoformat(),
                    )
        return expired_count

    # ===================================================================
    # 统计
    # ===================================================================

    def get_stats(self) -> dict[str, Any]:
        """获取工作流引擎统计信息。"""
        status_counts: dict[str, int] = {}
        for req in self._requests.values():
            status_counts[req["status"]] = status_counts.get(req["status"], 0) + 1
        return {
            "total_requests": len(self._requests),
            "by_status": status_counts,
            "pending_approvals": sum(
                1 for r in self._requests.values()
                if r["status"] in (RequestStatus.PENDING, RequestStatus.IN_REVIEW)
            ),
        }

    # ===================================================================
    # 内部方法
    # ===================================================================

    def _get_request_or_raise(self, request_id: str) -> dict[str, Any]:
        """获取请求或抛出 KeyError。"""
        request = self._requests.get(request_id)
        if request is None:
            raise KeyError(f"审批请求不存在: {request_id}")
        return request

    @staticmethod
    def _is_expired(request: dict[str, Any]) -> bool:
        """检查请求是否已超时。"""
        return request["expires_at"] < datetime.now(timezone.utc)

    def _auto_approver_chain(
        self, requester_id: str, risk_level: str
    ) -> list[str]:
        """
        根据风险等级自动生成审批链。

        实际生产中应从 RBAC 引擎查询用户上级，
        此处使用简化的默认逻辑：
        - ordinary: ["dept_manager"]
        - sensitive: ["dept_manager", "c_level"]
        - high_risk: ["dept_manager", "c_level", "chairman"]
        """
        chains = {
            RiskLevel.ORDINARY: ["user_dept_manager"],
            RiskLevel.SENSITIVE: ["user_dept_manager", "user_c_level"],
            RiskLevel.HIGH_RISK: ["user_dept_manager", "user_c_level", "user_chairman"],
        }
        return chains.get(risk_level, chains[RiskLevel.ORDINARY])

    @staticmethod
    def _request_to_dict(request: dict[str, Any]) -> dict[str, Any]:
        """将内部请求存储转换为返回字典。"""
        return {
            "request_id": request["request_id"],
            "requester_id": request["requester_id"],
            "request_type": request["request_type"],
            "title": request["title"],
            "risk_level": request["risk_level"],
            "details": request["details"],
            "approver_chain": request["approver_chain"],
            "current_step": request["current_step"],
            "status": request["status"],
            "created_at": request["created_at"].isoformat() if isinstance(request["created_at"], datetime) else request["created_at"],
            "updated_at": request["updated_at"].isoformat() if isinstance(request["updated_at"], datetime) else request["updated_at"],
            "expires_at": request["expires_at"].isoformat() if isinstance(request["expires_at"], datetime) else request["expires_at"],
            "completed_at": request["completed_at"].isoformat() if isinstance(request.get("completed_at"), datetime) else request.get("completed_at"),
        }

    def clear(self) -> None:
        """清空所有数据（测试隔离用）。"""
        self._requests.clear()
        self._approvals.clear()


# ---------------------------------------------------------------------------
# 全局单例
# ---------------------------------------------------------------------------

workflow_engine = WorkflowEngine()
