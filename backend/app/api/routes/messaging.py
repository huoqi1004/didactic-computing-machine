"""
内部通讯 API 路由
================

提供企业内部即时通讯能力，支持人与人、人与 Agent 的实时对话。

端点概览（前缀 ``/api/v1/messaging``）：

频道管理：
- ``GET  /channels``                            获取当前用户的频道列表（含未读数）
- ``POST /channels``                            创建频道（群聊/单聊/Agent对话）
- ``GET  /channels/{channel_id}/messages``      获取频道消息历史（分页）
- ``POST /channels/{channel_id}/messages``      发送消息
- ``GET  /channels/{channel_id}/members``       获取频道成员列表
- ``POST /channels/{channel_id}/read``          标记已读

文件管理：
- ``POST /channels/{channel_id}/files``         上传文件
- ``GET  /files/{file_id}``                     下载文件

Agent 指令：
- ``POST /channels/{channel_id}/agent-command`` 发送 Agent 指令

用户与 Agent 列表：
- ``GET  /users``                               获取可对话的用户列表
- ``GET  /agents``                              获取可对话的 Agent 列表

设计要点：
1. 使用 ``get_current_user`` 做认证
2. 数据库不可用时降级到内存存储（try/except 包裹 DB 操作）
3. 所有写操作成功后通过 WebSocket 实时推送
4. 用户首次访问频道列表时自动创建与每个 Agent 的单聊频道
5. Agent 频道中消息以 ``/`` 开头时触发 Agent 执行
"""
from __future__ import annotations

import os
import tempfile
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.api.middleware.auth import get_current_user
from app.api.middleware.ws_manager import ws_connection_manager
from app.core.database import get_session_factory
from app.core.rbac import rbac_engine
from app.core.agent.orchestrator import orchestrator
from app.db.models import ChatChannel, ChatMember, ChatMessage, ChatFile

# Agent 引用（Phase 4/5 Agent 通过 _agent_refs 获取）
from app.api.routes import _agent_refs

logger = structlog.get_logger()

router = APIRouter(prefix="/api/v1/messaging", tags=["内部通讯"])


# ===========================================================================
# 常量定义
# ===========================================================================

# 聊天文件存储目录
CHAT_FILES_DIR = os.path.join(tempfile.gettempdir(), "chat_files")
os.makedirs(CHAT_FILES_DIR, exist_ok=True)

# 消息分页默认大小
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200


# ===========================================================================
# Agent 目录（33 个业务 Agent）
# ===========================================================================
# 每个 Agent 的 domain key 用于频道成员标识与实例查找，
# orchestrator_name 用于通过 orchestrator.get_agent_by_name() 获取实例，
# display_name 是聊天界面展示的中文名称。

AGENT_CATALOG: list[dict[str, str]] = [
    # ---- 跨域协同 ----
    {"key": "supervisor", "orchestrator_name": "总调度Agent", "display_name": "总调度",
     "description": "跨域指令路由与汇总：意图分类后委派各业务域助手协同完成", "icon": "Network"},
    # ---- 核心财务运营 Agent ----
    {"key": "inventory", "orchestrator_name": "库存守护Agent", "display_name": "库存管家",
     "description": "负责库存监控、自动补货决策与出入库核对", "icon": "Package"},
    {"key": "accounting", "orchestrator_name": "记账Agent", "display_name": "会计专员",
     "description": "自动生成会计凭证、科目核对与税务计算", "icon": "Calculator"},
    {"key": "procurement", "orchestrator_name": "采购Agent", "display_name": "采购助手",
     "description": "供应商比价、采购订单生成与到货跟踪", "icon": "ShoppingCart"},
    {"key": "treasury", "orchestrator_name": "资金Agent", "display_name": "资金管家",
     "description": "资金调度、现金流预测与支付排程", "icon": "Wallet"},
    {"key": "audit", "orchestrator_name": "内审Agent", "display_name": "内审哨兵",
     "description": "内部审计、异常检测与合规风险监控", "icon": "ShieldCheck"},
    {"key": "tax", "orchestrator_name": "税务Agent", "display_name": "税务专员",
     "description": "税务申报、税率管理与税务筹划", "icon": "Receipt"},
    {"key": "compliance", "orchestrator_name": "合规Agent", "display_name": "合规官",
     "description": "法规库管理、合规检查与控制点监控", "icon": "Scale"},
    {"key": "demand", "orchestrator_name": "需求先知Agent", "display_name": "需求预测师",
     "description": "需求预测、趋势分析与销量预估", "icon": "TrendingUp"},
    {"key": "supply", "orchestrator_name": "供应规划Agent", "display_name": "供应规划师",
     "description": "供应规划、产能匹配与供应链优化", "icon": "Network"},
    {"key": "production", "orchestrator_name": "生产Agent", "display_name": "生产调度",
     "description": "生产计划、排产调度与BOM管理", "icon": "Factory"},
    {"key": "logistics", "orchestrator_name": "物流Agent", "display_name": "物流管家",
     "description": "物流跟踪、仓储管理与配送优化", "icon": "Truck"},
    {"key": "scheduler", "orchestrator_name": "动态调度Agent", "display_name": "动态调度",
     "description": "动态任务调度与资源分配", "icon": "CalendarClock"},
    {"key": "invoice", "orchestrator_name": "发票Agent", "display_name": "发票管家",
     "description": "发票开具、校验与作废管理", "icon": "FileText"},
    {"key": "reimbursement", "orchestrator_name": "报销Agent", "display_name": "报销助手",
     "description": "报销申请、审核与支付管理", "icon": "CreditCard"},
    {"key": "knowledge", "orchestrator_name": "知识Agent", "display_name": "知识管家",
     "description": "知识库检索、文档摄入与规则管理", "icon": "BookOpen"},
    # ---- HR 与销售营销 Agent ----
    {"key": "hr", "orchestrator_name": "人力资源Agent", "display_name": "人事管家",
     "description": "员工管理、考勤与人事档案", "icon": "Users"},
    {"key": "recruitment", "orchestrator_name": "招聘Agent", "display_name": "招聘助手",
     "description": "招聘需求、候选人筛选与面试管理", "icon": "UserPlus"},
    {"key": "performance", "orchestrator_name": "绩效Agent", "display_name": "绩效分析师",
     "description": "绩效评估、目标管理与考核", "icon": "Target"},
    {"key": "payroll", "orchestrator_name": "薪酬Agent", "display_name": "薪酬管家",
     "description": "薪资核算、社保与个税计算", "icon": "Banknote"},
    {"key": "crm", "orchestrator_name": "客户关系Agent", "display_name": "客户关系管家",
     "description": "客户主数据、信用评级与客户分析", "icon": "Contact"},
    {"key": "sales", "orchestrator_name": "销售Agent", "display_name": "销售助手",
     "description": "销售订单、客户开发与合同执行", "icon": "TrendingUp"},
    {"key": "marketing", "orchestrator_name": "市场Agent", "display_name": "营销策划",
     "description": "市场推广、品牌运营与营销活动", "icon": "Megaphone"},
    {"key": "customer_service", "orchestrator_name": "客服Agent", "display_name": "客服管家",
     "description": "客户服务、投诉处理与满意度管理", "icon": "Headphones"},
    # ---- 法务风控与运营管理 Agent ----
    {"key": "legal", "orchestrator_name": "法务Agent", "display_name": "法务顾问",
     "description": "法律咨询、合同审查与法律风险评估", "icon": "Gavel"},
    {"key": "risk", "orchestrator_name": "风险Agent", "display_name": "风控哨兵",
     "description": "风险识别、评估与熔断决策", "icon": "ShieldAlert"},
    {"key": "contract", "orchestrator_name": "合同Agent", "display_name": "合同管家",
     "description": "合同起草、审批与履约管理", "icon": "FileSignature"},
    {"key": "quality", "orchestrator_name": "质量Agent", "display_name": "质量检验员",
     "description": "质量检验、缺陷管理与质量改进", "icon": "CheckCircle"},
    {"key": "asset", "orchestrator_name": "资产Agent", "display_name": "资产管家",
     "description": "固定资产管理、折旧与盘点", "icon": "Boxes"},
    {"key": "project", "orchestrator_name": "项目Agent", "display_name": "项目管家",
     "description": "项目规划、进度跟踪与预算管控", "icon": "ClipboardList"},
    # ---- 战略分析 Agent ----
    {"key": "budget", "orchestrator_name": "预算Agent", "display_name": "预算分析师",
     "description": "预算编制、执行监控与差异分析", "icon": "PieChart"},
    {"key": "bi", "orchestrator_name": "商业智能Agent", "display_name": "商业智能分析师",
     "description": "数据分析、报表生成与经营洞察", "icon": "BarChart3"},
    {"key": "esg", "orchestrator_name": "ESG Agent", "display_name": "ESG管家",
     "description": "ESG指标监控与可持续发展报告", "icon": "Leaf"},
    {"key": "admin", "orchestrator_name": "管理Agent", "display_name": "行政管家",
     "description": "行政管理、采购与日常运营", "icon": "Settings"},
]

# agent_key -> 目录条目
_AGENT_KEY_MAP: dict[str, dict[str, str]] = {a["key"]: a for a in AGENT_CATALOG}

# Phase 4/5 Agent 的 _agent_refs getter 映射
_AGENT_REFS_GETTERS = {
    "supervisor": lambda: __import__(
        "app.core.agent.supervisor", fromlist=["get_supervisor_agent"]
    ).get_supervisor_agent(),
    "hr": _agent_refs.get_hr_agent,
    "recruitment": _agent_refs.get_recruitment_agent,
    "performance": _agent_refs.get_performance_agent,
    "payroll": _agent_refs.get_payroll_agent,
    "crm": _agent_refs.get_crm_agent,
    "sales": _agent_refs.get_sales_agent,
    "marketing": _agent_refs.get_marketing_agent,
    "customer_service": _agent_refs.get_customer_service_agent,
    "legal": _agent_refs.get_legal_agent,
    "risk": _agent_refs.get_risk_agent,
    "contract": _agent_refs.get_contract_agent,
    "quality": _agent_refs.get_quality_agent,
    "asset": _agent_refs.get_asset_agent,
    "project": _agent_refs.get_project_agent,
    "budget": _agent_refs.get_budget_agent,
    "bi": _agent_refs.get_bi_agent,
    "esg": _agent_refs.get_esg_agent,
    "admin": _agent_refs.get_admin_agent,
}


def _get_agent_instance(agent_key: str):
    """根据 agent_key 获取 Agent 实例。

    优先通过 _agent_refs 获取（Phase 4/5 Agent），
    其次通过 orchestrator 按名称获取（核心 Agent）。
    """
    getter = _AGENT_REFS_GETTERS.get(agent_key)
    if getter is not None:
        try:
            agent = getter()
            if agent is not None:
                return agent
        except Exception as e:
            logger.warning("messaging.agent_refs_failed", agent_key=agent_key, error=str(e))

    # 通过 orchestrator 获取
    catalog = _AGENT_KEY_MAP.get(agent_key)
    if catalog is not None:
        try:
            return orchestrator.get_agent_by_name(catalog["orchestrator_name"])
        except Exception as e:
            logger.warning("messaging.orchestrator_lookup_failed",
                           agent_key=agent_key, error=str(e))
    return None


# ===========================================================================
# 内存降级存储（数据库不可用时使用）
# ===========================================================================

_mem_channels: dict[str, dict[str, Any]] = {}      # channel_id -> channel dict
_mem_members: dict[str, list[dict[str, Any]]] = {}  # channel_id -> [member dict, ...]
_mem_messages: dict[str, list[dict[str, Any]]] = {}  # channel_id -> [message dict, ...]
_mem_files: dict[str, dict[str, Any]] = {}           # file_id -> file dict
_mem_msg_counter: int = 0
_mem_member_counter: int = 0


def _next_mem_message_id() -> int:
    global _mem_msg_counter
    _mem_msg_counter += 1
    return _mem_msg_counter


def _next_mem_member_id() -> int:
    global _mem_member_counter
    _mem_member_counter += 1
    return _mem_member_counter


def _dt_iso(dt: Any) -> Optional[str]:
    """将 datetime 转换为 ISO 字符串"""
    if dt is None:
        return None
    if isinstance(dt, datetime):
        return dt.isoformat()
    return str(dt)


def _now() -> datetime:
    return datetime.now()


# ===========================================================================
# 数据访问层（DB 优先，降级到内存）
# ===========================================================================

def _db_available() -> bool:
    """检查数据库会话工厂是否可用"""
    return get_session_factory() is not None


async def _db_get_user_channels(user_id: str) -> list[dict[str, Any]]:
    """从 DB 获取用户所属频道列表"""
    factory = get_session_factory()
    if factory is None:
        return []
    session = factory()
    try:
        from sqlalchemy import select
        # 查询用户所属的频道
        stmt = select(ChatMember.channel_id).where(ChatMember.user_id == user_id)
        result = await session.execute(stmt)
        channel_ids = [row[0] for row in result.all()]
        if not channel_ids:
            return []
        # 查询频道详情
        stmt = select(ChatChannel).where(
            ChatChannel.channel_id.in_(channel_ids),
            ChatChannel.is_active == True,  # noqa: E712
        )
        result = await session.execute(stmt)
        channels = []
        for ch in result.scalars():
            channels.append(_channel_to_dict(ch))
        return channels
    finally:
        await session.close()


async def _db_get_channel(channel_id: str) -> Optional[dict[str, Any]]:
    """从 DB 获取单个频道"""
    factory = get_session_factory()
    if factory is None:
        return None
    session = factory()
    try:
        from sqlalchemy import select
        stmt = select(ChatChannel).where(ChatChannel.channel_id == channel_id)
        result = await session.execute(stmt)
        ch = result.scalars().first()
        if ch is None:
            return None
        return _channel_to_dict(ch)
    finally:
        await session.close()


async def _db_create_channel(channel: dict[str, Any]) -> dict[str, Any]:
    """在 DB 中创建频道"""
    factory = get_session_factory()
    if factory is None:
        raise RuntimeError("DB unavailable")
    session = factory()
    try:
        ch = ChatChannel(
            channel_id=channel["channel_id"],
            channel_type=channel["channel_type"],
            name=channel.get("name"),
            avatar=channel.get("avatar"),
            created_by=channel["created_by"],
            is_active=channel.get("is_active", True),
        )
        session.add(ch)
        await session.commit()
        return _channel_to_dict(ch)
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


async def _db_add_member(channel_id: str, user_id: str, member_type: str,
                         role: str) -> dict[str, Any]:
    """在 DB 中添加频道成员"""
    factory = get_session_factory()
    if factory is None:
        raise RuntimeError("DB unavailable")
    session = factory()
    try:
        member = ChatMember(
            channel_id=channel_id,
            user_id=user_id,
            member_type=member_type,
            role=role,
        )
        session.add(member)
        await session.commit()
        return _member_to_dict(member)
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


async def _db_get_members(channel_id: str) -> list[dict[str, Any]]:
    """从 DB 获取频道成员列表"""
    factory = get_session_factory()
    if factory is None:
        return []
    session = factory()
    try:
        from sqlalchemy import select
        stmt = select(ChatMember).where(ChatMember.channel_id == channel_id)
        result = await session.execute(stmt)
        return [_member_to_dict(m) for m in result.scalars()]
    finally:
        await session.close()


async def _db_get_member(channel_id: str, user_id: str) -> Optional[dict[str, Any]]:
    """从 DB 获取单个频道成员"""
    factory = get_session_factory()
    if factory is None:
        return None
    session = factory()
    try:
        from sqlalchemy import select
        stmt = select(ChatMember).where(
            ChatMember.channel_id == channel_id,
            ChatMember.user_id == user_id,
        )
        result = await session.execute(stmt)
        m = result.scalars().first()
        if m is None:
            return None
        return _member_to_dict(m)
    finally:
        await session.close()


async def _db_save_message(msg: dict[str, Any]) -> dict[str, Any]:
    """在 DB 中保存消息"""
    factory = get_session_factory()
    if factory is None:
        raise RuntimeError("DB unavailable")
    session = factory()
    try:
        message = ChatMessage(
            channel_id=msg["channel_id"],
            sender_id=msg["sender_id"],
            sender_type=msg.get("sender_type", "user"),
            sender_name=msg["sender_name"],
            content=msg["content"],
            message_type=msg.get("message_type", "text"),
            file_id=msg.get("file_id"),
            reply_to_id=msg.get("reply_to_id"),
            metadata_json=msg.get("metadata_json"),
        )
        session.add(message)
        await session.flush()  # 获取自增 message_id
        msg_id = message.message_id
        created_at = message.created_at
        await session.commit()
        return {
            "message_id": msg_id,
            "channel_id": msg["channel_id"],
            "sender_id": msg["sender_id"],
            "sender_type": msg.get("sender_type", "user"),
            "sender_name": msg["sender_name"],
            "content": msg["content"],
            "message_type": msg.get("message_type", "text"),
            "file_id": msg.get("file_id"),
            "reply_to_id": msg.get("reply_to_id"),
            "metadata_json": msg.get("metadata_json"),
            "created_at": _dt_iso(created_at),
        }
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


async def _db_get_messages(channel_id: str, limit: int,
                           before_id: Optional[int] = None) -> list[dict[str, Any]]:
    """从 DB 获取频道消息历史（分页）"""
    factory = get_session_factory()
    if factory is None:
        return []
    session = factory()
    try:
        from sqlalchemy import select
        stmt = select(ChatMessage).where(ChatMessage.channel_id == channel_id)
        if before_id is not None:
            stmt = stmt.where(ChatMessage.message_id < before_id)
        stmt = stmt.order_by(ChatMessage.message_id.desc()).limit(limit)
        result = await session.execute(stmt)
        messages = [_message_to_dict(m) for m in result.scalars()]
        messages.reverse()  # 按时间正序返回
        return messages
    finally:
        await session.close()


async def _db_update_channel_last_message(channel_id: str, preview: str) -> None:
    """更新频道的最后消息时间和预览"""
    factory = get_session_factory()
    if factory is None:
        return
    session = factory()
    try:
        from sqlalchemy import update
        stmt = (
            update(ChatChannel)
            .where(ChatChannel.channel_id == channel_id)
            .values(last_message_at=_now(), last_message_preview=preview[:256])
        )
        await session.execute(stmt)
        await session.commit()
    except Exception:
        await session.rollback()
    finally:
        await session.close()


async def _db_mark_read(channel_id: str, user_id: str, message_id: int) -> None:
    """标记已读"""
    factory = get_session_factory()
    if factory is None:
        return
    session = factory()
    try:
        from sqlalchemy import update
        stmt = (
            update(ChatMember)
            .where(ChatMember.channel_id == channel_id, ChatMember.user_id == user_id)
            .values(last_read_message_id=message_id)
        )
        await session.execute(stmt)
        await session.commit()
    except Exception:
        await session.rollback()
    finally:
        await session.close()


async def _db_count_unread(channel_id: str, user_id: str) -> int:
    """统计未读消息数"""
    factory = get_session_factory()
    if factory is None:
        return 0
    session = factory()
    try:
        from sqlalchemy import select, func
        # 获取用户在该频道的 last_read_message_id
        stmt = select(ChatMember.last_read_message_id).where(
            ChatMember.channel_id == channel_id,
            ChatMember.user_id == user_id,
        )
        result = await session.execute(stmt)
        row = result.first()
        last_read_id = row[0] if row else None

        # 统计该频道中大于 last_read_id 的消息数
        count_stmt = select(func.count(ChatMessage.message_id)).where(
            ChatMessage.channel_id == channel_id,
        )
        if last_read_id is not None:
            count_stmt = count_stmt.where(ChatMessage.message_id > last_read_id)
        result = await session.execute(count_stmt)
        return result.scalar() or 0
    finally:
        await session.close()


async def _db_get_last_message(channel_id: str) -> Optional[dict[str, Any]]:
    """获取频道最后一条消息"""
    factory = get_session_factory()
    if factory is None:
        return None
    session = factory()
    try:
        from sqlalchemy import select
        stmt = (
            select(ChatMessage)
            .where(ChatMessage.channel_id == channel_id)
            .order_by(ChatMessage.message_id.desc())
            .limit(1)
        )
        result = await session.execute(stmt)
        m = result.scalars().first()
        if m is None:
            return None
        return _message_to_dict(m)
    finally:
        await session.close()


async def _db_find_direct_channel(user_a: str, user_b: str) -> Optional[dict[str, Any]]:
    """查找两个用户之间的单聊频道"""
    factory = get_session_factory()
    if factory is None:
        return None
    session = factory()
    try:
        from sqlalchemy import select
        # 查找 user_a 所属的直接频道
        stmt = select(ChatMember.channel_id).where(
            ChatMember.user_id == user_a,
        )
        result = await session.execute(stmt)
        channel_ids = [row[0] for row in result.all()]
        for cid in channel_ids:
            # 检查该频道是否为 direct 类型
            ch_stmt = select(ChatChannel).where(
                ChatChannel.channel_id == cid,
                ChatChannel.channel_type == "direct",
            )
            ch_result = await session.execute(ch_stmt)
            ch = ch_result.scalars().first()
            if ch is None:
                continue
            # 检查 user_b 是否也是该频道成员
            m_stmt = select(ChatMember).where(
                ChatMember.channel_id == cid,
                ChatMember.user_id == user_b,
            )
            m_result = await session.execute(m_stmt)
            if m_result.scalars().first() is not None:
                return _channel_to_dict(ch)
        return None
    finally:
        await session.close()


async def _db_find_agent_channel(user_id: str, agent_key: str) -> Optional[dict[str, Any]]:
    """查找用户与 Agent 之间的单聊频道"""
    factory = get_session_factory()
    if factory is None:
        return None
    session = factory()
    try:
        from sqlalchemy import select
        # 查找用户所属的 agent 类型频道
        stmt = select(ChatMember.channel_id).where(
            ChatMember.user_id == user_id,
        )
        result = await session.execute(stmt)
        channel_ids = [row[0] for row in result.all()]
        for cid in channel_ids:
            ch_stmt = select(ChatChannel).where(
                ChatChannel.channel_id == cid,
                ChatChannel.channel_type == "agent",
            )
            ch_result = await session.execute(ch_stmt)
            ch = ch_result.scalars().first()
            if ch is None:
                continue
            # 检查该频道是否有 agent_key 成员
            m_stmt = select(ChatMember).where(
                ChatMember.channel_id == cid,
                ChatMember.user_id == agent_key,
                ChatMember.member_type == "agent",
            )
            m_result = await session.execute(m_stmt)
            if m_result.scalars().first() is not None:
                return _channel_to_dict(ch)
        return None
    finally:
        await session.close()


async def _db_save_file(file_data: dict[str, Any]) -> dict[str, Any]:
    """在 DB 中保存文件记录"""
    factory = get_session_factory()
    if factory is None:
        raise RuntimeError("DB unavailable")
    session = factory()
    try:
        f = ChatFile(
            file_id=file_data["file_id"],
            channel_id=file_data["channel_id"],
            uploaded_by=file_data["uploaded_by"],
            filename=file_data["filename"],
            original_name=file_data["original_name"],
            file_size=file_data["file_size"],
            mime_type=file_data.get("mime_type", "application/octet-stream"),
            storage_path=file_data["storage_path"],
        )
        session.add(f)
        await session.commit()
        return _file_to_dict(f)
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


async def _db_get_file(file_id: str) -> Optional[dict[str, Any]]:
    """从 DB 获取文件记录"""
    factory = get_session_factory()
    if factory is None:
        return None
    session = factory()
    try:
        from sqlalchemy import select
        stmt = select(ChatFile).where(ChatFile.file_id == file_id)
        result = await session.execute(stmt)
        f = result.scalars().first()
        if f is None:
            return None
        return _file_to_dict(f)
    finally:
        await session.close()


# ===========================================================================
# 模型转字典辅助函数
# ===========================================================================

def _channel_to_dict(ch: ChatChannel) -> dict[str, Any]:
    return {
        "channel_id": ch.channel_id,
        "channel_type": ch.channel_type,
        "name": ch.name,
        "avatar": ch.avatar,
        "created_by": ch.created_by,
        "created_at": _dt_iso(ch.created_at),
        "updated_at": _dt_iso(ch.updated_at),
        "last_message_at": _dt_iso(ch.last_message_at),
        "last_message_preview": ch.last_message_preview,
        "is_active": ch.is_active,
    }


def _member_to_dict(m: ChatMember) -> dict[str, Any]:
    return {
        "id": m.id,
        "channel_id": m.channel_id,
        "user_id": m.user_id,
        "member_type": m.member_type,
        "role": m.role,
        "joined_at": _dt_iso(m.joined_at),
        "last_read_message_id": m.last_read_message_id,
        "is_muted": m.is_muted,
    }


def _message_to_dict(m: ChatMessage) -> dict[str, Any]:
    return {
        "message_id": m.message_id,
        "channel_id": m.channel_id,
        "sender_id": m.sender_id,
        "sender_type": m.sender_type,
        "sender_name": m.sender_name,
        "content": m.content,
        "message_type": m.message_type,
        "file_id": m.file_id,
        "reply_to_id": m.reply_to_id,
        "metadata_json": m.metadata_json,
        "created_at": _dt_iso(m.created_at),
    }


def _file_to_dict(f: ChatFile) -> dict[str, Any]:
    return {
        "file_id": f.file_id,
        "channel_id": f.channel_id,
        "uploaded_by": f.uploaded_by,
        "filename": f.filename,
        "original_name": f.original_name,
        "file_size": f.file_size,
        "mime_type": f.mime_type,
        "storage_path": f.storage_path,
        "created_at": _dt_iso(f.created_at),
    }


# ===========================================================================
# 统一数据访问（DB 优先，降级到内存）
# ===========================================================================

async def get_user_channels(user_id: str) -> list[dict[str, Any]]:
    """获取用户所属频道列表（DB 优先，降级到内存）"""
    try:
        channels = await _db_get_user_channels(user_id)
        if channels:
            return channels
        # DB 查询成功但为空，或 DB 不可用返回空列表 — 尝试内存
        if not _db_available():
            mem_channels = []
            for cid, members in _mem_members.items():
                if any(m["user_id"] == user_id for m in members):
                    ch = _mem_channels.get(cid)
                    if ch and ch.get("is_active", True):
                        mem_channels.append(dict(ch))
            return mem_channels
        return channels
    except Exception as e:
        logger.warning("messaging.get_user_channels_db_failed", error=str(e), fallback="memory")
        mem_channels = []
        for cid, members in _mem_members.items():
            if any(m["user_id"] == user_id for m in members):
                ch = _mem_channels.get(cid)
                if ch and ch.get("is_active", True):
                    mem_channels.append(dict(ch))
        return mem_channels


async def get_channel(channel_id: str) -> Optional[dict[str, Any]]:
    """获取单个频道（DB 优先，降级到内存）"""
    try:
        ch = await _db_get_channel(channel_id)
        if ch is not None:
            return ch
        if not _db_available():
            return _mem_channels.get(channel_id)
        return None
    except Exception as e:
        logger.warning("messaging.get_channel_db_failed", error=str(e), fallback="memory")
        return _mem_channels.get(channel_id)


async def create_channel(channel_data: dict[str, Any],
                         members: list[dict[str, Any]]) -> dict[str, Any]:
    """创建频道并添加成员（DB 优先，降级到内存）"""
    try:
        ch = await _db_create_channel(channel_data)
        for m in members:
            await _db_add_member(
                channel_id=channel_data["channel_id"],
                user_id=m["user_id"],
                member_type=m.get("member_type", "user"),
                role=m.get("role", "member"),
            )
        return ch
    except Exception as e:
        logger.warning("messaging.create_channel_db_failed", error=str(e), fallback="memory")
        # 内存降级
        now = _now()
        ch = {
            "channel_id": channel_data["channel_id"],
            "channel_type": channel_data["channel_type"],
            "name": channel_data.get("name"),
            "avatar": channel_data.get("avatar"),
            "created_by": channel_data["created_by"],
            "created_at": _dt_iso(now),
            "updated_at": _dt_iso(now),
            "last_message_at": None,
            "last_message_preview": None,
            "is_active": channel_data.get("is_active", True),
        }
        _mem_channels[channel_data["channel_id"]] = ch
        _mem_members[channel_data["channel_id"]] = []
        _mem_messages[channel_data["channel_id"]] = []
        for m in members:
            member = {
                "id": _next_mem_member_id(),
                "channel_id": channel_data["channel_id"],
                "user_id": m["user_id"],
                "member_type": m.get("member_type", "user"),
                "role": m.get("role", "member"),
                "joined_at": _dt_iso(now),
                "last_read_message_id": None,
                "is_muted": False,
            }
            _mem_members[channel_data["channel_id"]].append(member)
        return ch


async def get_members(channel_id: str) -> list[dict[str, Any]]:
    """获取频道成员列表（DB 优先，降级到内存）"""
    try:
        members = await _db_get_members(channel_id)
        if members:
            return members
        if not _db_available():
            return list(_mem_members.get(channel_id, []))
        return members
    except Exception as e:
        logger.warning("messaging.get_members_db_failed", error=str(e), fallback="memory")
        return list(_mem_members.get(channel_id, []))


async def get_member(channel_id: str, user_id: str) -> Optional[dict[str, Any]]:
    """获取单个频道成员（DB 优先，降级到内存）"""
    try:
        m = await _db_get_member(channel_id, user_id)
        if m is not None:
            return m
        if not _db_available():
            for mem in _mem_members.get(channel_id, []):
                if mem["user_id"] == user_id:
                    return dict(mem)
            return None
        return None
    except Exception as e:
        logger.warning("messaging.get_member_db_failed", error=str(e), fallback="memory")
        for mem in _mem_members.get(channel_id, []):
            if mem["user_id"] == user_id:
                return dict(mem)
        return None


async def save_message(msg: dict[str, Any]) -> dict[str, Any]:
    """保存消息（DB 优先，降级到内存）"""
    try:
        return await _db_save_message(msg)
    except Exception as e:
        logger.warning("messaging.save_message_db_failed", error=str(e), fallback="memory")
        now = _now()
        saved = {
            "message_id": _next_mem_message_id(),
            "channel_id": msg["channel_id"],
            "sender_id": msg["sender_id"],
            "sender_type": msg.get("sender_type", "user"),
            "sender_name": msg["sender_name"],
            "content": msg["content"],
            "message_type": msg.get("message_type", "text"),
            "file_id": msg.get("file_id"),
            "reply_to_id": msg.get("reply_to_id"),
            "metadata_json": msg.get("metadata_json"),
            "created_at": _dt_iso(now),
        }
        _mem_messages.setdefault(msg["channel_id"], []).append(saved)
        # 更新内存频道预览
        ch = _mem_channels.get(msg["channel_id"])
        if ch is not None:
            ch["last_message_at"] = _dt_iso(now)
            ch["last_message_preview"] = msg["content"][:256]
        return saved


async def get_messages(channel_id: str, limit: int,
                        before_id: Optional[int] = None) -> list[dict[str, Any]]:
    """获取频道消息历史（DB 优先，降级到内存）"""
    try:
        messages = await _db_get_messages(channel_id, limit, before_id)
        if messages:
            return messages
        if not _db_available():
            all_msgs = list(_mem_messages.get(channel_id, []))
            if before_id is not None:
                all_msgs = [m for m in all_msgs if m["message_id"] < before_id]
            all_msgs.sort(key=lambda m: m["message_id"], reverse=True)
            all_msgs = all_msgs[:limit]
            all_msgs.reverse()
            return all_msgs
        return []
    except Exception as e:
        logger.warning("messaging.get_messages_db_failed", error=str(e), fallback="memory")
        all_msgs = list(_mem_messages.get(channel_id, []))
        if before_id is not None:
            all_msgs = [m for m in all_msgs if m["message_id"] < before_id]
        all_msgs.sort(key=lambda m: m["message_id"], reverse=True)
        all_msgs = all_msgs[:limit]
        all_msgs.reverse()
        return all_msgs


async def update_channel_last_message(channel_id: str, preview: str) -> None:
    """更新频道最后消息（DB 优先，降级到内存）"""
    try:
        await _db_update_channel_last_message(channel_id, preview)
    except Exception as e:
        logger.warning("messaging.update_channel_last_message_failed", error=str(e))
    # 同步更新内存
    ch = _mem_channels.get(channel_id)
    if ch is not None:
        now = _now()
        ch["last_message_at"] = _dt_iso(now)
        ch["last_message_preview"] = preview[:256]


async def mark_read(channel_id: str, user_id: str, message_id: int) -> None:
    """标记已读（DB 优先，降级到内存）"""
    try:
        await _db_mark_read(channel_id, user_id, message_id)
    except Exception as e:
        logger.warning("messaging.mark_read_failed", error=str(e))
    # 同步更新内存
    for mem in _mem_members.get(channel_id, []):
        if mem["user_id"] == user_id:
            mem["last_read_message_id"] = message_id
            break


async def count_unread(channel_id: str, user_id: str) -> int:
    """统计未读消息数（DB 优先，降级到内存）"""
    try:
        if _db_available():
            return await _db_count_unread(channel_id, user_id)
        return _mem_count_unread(channel_id, user_id)
    except Exception as e:
        logger.warning("messaging.count_unread_failed", error=str(e), fallback="memory")
        return _mem_count_unread(channel_id, user_id)


def _mem_count_unread(channel_id: str, user_id: str) -> int:
    """内存模式下的未读计数"""
    last_read_id = None
    for mem in _mem_members.get(channel_id, []):
        if mem["user_id"] == user_id:
            last_read_id = mem.get("last_read_message_id")
            break
    count = 0
    for msg in _mem_messages.get(channel_id, []):
        if last_read_id is None or msg["message_id"] > last_read_id:
            count += 1
    return count


async def get_last_message(channel_id: str) -> Optional[dict[str, Any]]:
    """获取频道最后一条消息（DB 优先，降级到内存）"""
    try:
        if _db_available():
            msg = await _db_get_last_message(channel_id)
            if msg is not None:
                return msg
        # 内存
        msgs = _mem_messages.get(channel_id, [])
        if msgs:
            return dict(msgs[-1])
        return None
    except Exception as e:
        logger.warning("messaging.get_last_message_failed", error=str(e), fallback="memory")
        msgs = _mem_messages.get(channel_id, [])
        if msgs:
            return dict(msgs[-1])
        return None


async def find_direct_channel(user_a: str, user_b: str) -> Optional[dict[str, Any]]:
    """查找两个用户之间的单聊频道"""
    try:
        if _db_available():
            ch = await _db_find_direct_channel(user_a, user_b)
            if ch is not None:
                return ch
        # 内存查找
        for cid, members in _mem_members.items():
            ch = _mem_channels.get(cid)
            if ch is None or ch.get("channel_type") != "direct":
                continue
            user_ids = {m["user_id"] for m in members}
            if user_a in user_ids and user_b in user_ids:
                return dict(ch)
        return None
    except Exception as e:
        logger.warning("messaging.find_direct_channel_failed", error=str(e), fallback="memory")
        for cid, members in _mem_members.items():
            ch = _mem_channels.get(cid)
            if ch is None or ch.get("channel_type") != "direct":
                continue
            user_ids = {m["user_id"] for m in members}
            if user_a in user_ids and user_b in user_ids:
                return dict(ch)
        return None


async def find_agent_channel(user_id: str, agent_key: str) -> Optional[dict[str, Any]]:
    """查找用户与 Agent 之间的单聊频道"""
    try:
        if _db_available():
            ch = await _db_find_agent_channel(user_id, agent_key)
            if ch is not None:
                return ch
        # 内存查找
        for cid, members in _mem_members.items():
            ch = _mem_channels.get(cid)
            if ch is None or ch.get("channel_type") != "agent":
                continue
            has_user = any(m["user_id"] == user_id for m in members)
            has_agent = any(
                m["user_id"] == agent_key and m.get("member_type") == "agent"
                for m in members
            )
            if has_user and has_agent:
                return dict(ch)
        return None
    except Exception as e:
        logger.warning("messaging.find_agent_channel_failed", error=str(e), fallback="memory")
        for cid, members in _mem_members.items():
            ch = _mem_channels.get(cid)
            if ch is None or ch.get("channel_type") != "agent":
                continue
            has_user = any(m["user_id"] == user_id for m in members)
            has_agent = any(
                m["user_id"] == agent_key and m.get("member_type") == "agent"
                for m in members
            )
            if has_user and has_agent:
                return dict(ch)
        return None


async def save_file(file_data: dict[str, Any]) -> dict[str, Any]:
    """保存文件记录（DB 优先，降级到内存）"""
    try:
        return await _db_save_file(file_data)
    except Exception as e:
        logger.warning("messaging.save_file_db_failed", error=str(e), fallback="memory")
        now = _now()
        saved = {
            "file_id": file_data["file_id"],
            "channel_id": file_data["channel_id"],
            "uploaded_by": file_data["uploaded_by"],
            "filename": file_data["filename"],
            "original_name": file_data["original_name"],
            "file_size": file_data["file_size"],
            "mime_type": file_data.get("mime_type", "application/octet-stream"),
            "storage_path": file_data["storage_path"],
            "created_at": _dt_iso(now),
        }
        _mem_files[file_data["file_id"]] = saved
        return saved


async def get_file(file_id: str) -> Optional[dict[str, Any]]:
    """获取文件记录（DB 优先，降级到内存）"""
    try:
        if _db_available():
            f = await _db_get_file(file_id)
            if f is not None:
                return f
        return _mem_files.get(file_id)
    except Exception as e:
        logger.warning("messaging.get_file_failed", error=str(e), fallback="memory")
        return _mem_files.get(file_id)


# ===========================================================================
# Agent 频道自动创建
# ===========================================================================

async def ensure_agent_channels(user_id: str, username: str) -> None:
    """确保用户拥有与每个 Agent 的单聊频道（首次访问时自动创建）。

    对 AGENT_CATALOG 中的每个 Agent，检查是否已存在对应的 agent 频道，
    若不存在则自动创建。
    """
    for agent in AGENT_CATALOG:
        agent_key = agent["key"]
        try:
            existing = await find_agent_channel(user_id, agent_key)
            if existing is not None:
                continue
            # 创建 Agent 频道
            channel_id = f"CH-{uuid.uuid4().hex[:12]}"
            channel_data = {
                "channel_id": channel_id,
                "channel_type": "agent",
                "name": agent["display_name"],
                "avatar": None,
                "created_by": user_id,
                "is_active": True,
            }
            members = [
                {"user_id": user_id, "member_type": "user", "role": "owner"},
                {"user_id": agent_key, "member_type": "agent", "role": "member"},
            ]
            await create_channel(channel_data, members)
            logger.info("messaging.agent_channel_created",
                        user_id=user_id, agent_key=agent_key, channel_id=channel_id)
        except Exception as e:
            logger.warning("messaging.ensure_agent_channel_failed",
                           user_id=user_id, agent_key=agent_key, error=str(e))


async def get_channel_agent_key(channel_id: str) -> Optional[str]:
    """获取 Agent 频道中对应的 agent_key（从成员列表中查找 member_type=agent 的成员）"""
    members = await get_members(channel_id)
    for m in members:
        if m.get("member_type") == "agent":
            return m.get("user_id")
    return None


# ===========================================================================
# 请求/响应模型
# ===========================================================================

class CreateChannelRequest(BaseModel):
    """创建频道请求"""
    channel_type: str = Field(..., description="频道类型: group / direct / agent")
    name: Optional[str] = Field(None, description="群聊名称")
    member_ids: list[str] = Field(default_factory=list, description="成员 user_id 列表")
    agent_id: Optional[str] = Field(None, description="Agent 频道的 agent_key")


class SendMessageRequest(BaseModel):
    """发送消息请求"""
    content: str = Field(..., description="消息内容")
    message_type: str = Field("text", description="消息类型: text / file / command / system")
    reply_to_id: Optional[int] = Field(None, description="回复的消息ID")
    metadata: Optional[dict] = Field(None, description="附加元数据")


class AgentCommandRequest(BaseModel):
    """Agent 指令请求"""
    command: str = Field(..., description="指令内容")
    agent_id: Optional[str] = Field(None, description="目标 Agent key（不填则用频道绑定的 Agent）")


class MarkReadRequest(BaseModel):
    """标记已读请求"""
    message_id: Optional[int] = Field(None, description="已读到的消息ID（不填则标记为最新消息）")


# ===========================================================================
# 路由端点
# ===========================================================================

@router.get("/channels")
async def list_channels(current_user: dict = Depends(get_current_user)):
    """获取当前用户的频道列表（含未读数和最后消息）。

    首次访问时自动创建与每个 Agent 的单聊频道。
    """
    user_id = current_user["user_id"]
    username = current_user.get("username", user_id)

    # 自动创建 Agent 频道
    try:
        await ensure_agent_channels(user_id, username)
    except Exception as e:
        logger.warning("messaging.ensure_agent_channels_failed", error=str(e))

    # 获取用户所属频道
    channels = await get_user_channels(user_id)

    # 为每个频道附加 unread_count 和 last_message
    result = []
    for ch in channels:
        channel_id = ch["channel_id"]
        try:
            unread = await count_unread(channel_id, user_id)
        except Exception:
            unread = 0
        try:
            last_msg = await get_last_message(channel_id)
        except Exception:
            last_msg = None

        # 获取成员数
        try:
            members = await get_members(channel_id)
            members_count = len(members)
        except Exception:
            members_count = 0

        result.append({
            **ch,
            "unread_count": unread,
            "last_message": last_msg,
            "members_count": members_count,
        })

    # 按最后消息时间倒序排列（无消息的排最后）
    result.sort(
        key=lambda c: c.get("last_message_at") or c.get("created_at") or "",
        reverse=True,
    )

    return {"channels": result}


@router.post("/channels")
async def create_channel_endpoint(
    req: CreateChannelRequest,
    current_user: dict = Depends(get_current_user),
):
    """创建频道（群聊/单聊/Agent对话）。

    - ``group``:  群聊，需指定 name 和 member_ids
    - ``direct``: 单聊，需指定 member_ids（对方 user_id）
    - ``agent``:  Agent 对话，需指定 agent_id
    """
    user_id = current_user["user_id"]
    username = current_user.get("username", user_id)

    channel_type = req.channel_type
    if channel_type not in ("group", "direct", "agent"):
        raise HTTPException(status_code=400, detail="channel_type 必须为 group / direct / agent")

    # ---- 单聊：检查是否已存在 ----
    if channel_type == "direct":
        if not req.member_ids:
            raise HTTPException(status_code=400, detail="单聊需指定 member_ids")
        target_id = req.member_ids[0]
        existing = await find_direct_channel(user_id, target_id)
        if existing is not None:
            return {"channel": existing}

    # ---- Agent 频道：检查是否已存在 ----
    if channel_type == "agent":
        if not req.agent_id:
            raise HTTPException(status_code=400, detail="Agent 频道需指定 agent_id")
        if req.agent_id not in _AGENT_KEY_MAP:
            raise HTTPException(status_code=400, detail=f"未知的 Agent: {req.agent_id}")
        existing = await find_agent_channel(user_id, req.agent_id)
        if existing is not None:
            return {"channel": existing}

    # ---- 创建频道 ----
    channel_id = f"CH-{uuid.uuid4().hex[:12]}"

    if channel_type == "agent":
        agent_info = _AGENT_KEY_MAP[req.agent_id]
        channel_name = agent_info["display_name"]
        members = [
            {"user_id": user_id, "member_type": "user", "role": "owner"},
            {"user_id": req.agent_id, "member_type": "agent", "role": "member"},
        ]
    elif channel_type == "direct":
        channel_name = None
        members = [
            {"user_id": user_id, "member_type": "user", "role": "owner"},
            {"user_id": req.member_ids[0], "member_type": "user", "role": "member"},
        ]
    else:  # group
        channel_name = req.name or "群聊"
        members = [{"user_id": user_id, "member_type": "user", "role": "owner"}]
        for mid in req.member_ids:
            if mid != user_id:
                members.append({"user_id": mid, "member_type": "user", "role": "member"})

    channel_data = {
        "channel_id": channel_id,
        "channel_type": channel_type,
        "name": channel_name,
        "avatar": None,
        "created_by": user_id,
        "is_active": True,
    }

    ch = await create_channel(channel_data, members)

    # 让创建者加入 WebSocket 频道
    ws_connection_manager.join_channel(channel_id, user_id)

    return {"channel": ch}


@router.get("/channels/{channel_id}/messages")
async def get_channel_messages(
    channel_id: str,
    limit: int = DEFAULT_PAGE_SIZE,
    before_id: Optional[int] = None,
    current_user: dict = Depends(get_current_user),
):
    """获取频道消息历史（分页）。

    参数：
    - ``limit``:     每页数量（默认 50，最大 200）
    - ``before_id``: 分页游标，返回 message_id < before_id 的消息
    """
    user_id = current_user["user_id"]

    # 权限检查：用户必须是频道成员
    member = await get_member(channel_id, user_id)
    if member is None:
        raise HTTPException(status_code=403, detail="您不是该频道的成员")

    limit = min(max(1, limit), MAX_PAGE_SIZE)

    messages = await get_messages(channel_id, limit, before_id)
    return {
        "messages": messages,
        "total": len(messages),
        "has_more": len(messages) >= limit,
    }


@router.post("/channels/{channel_id}/messages")
async def send_message(
    channel_id: str,
    req: SendMessageRequest,
    current_user: dict = Depends(get_current_user),
):
    """发送消息。

    流程：
    1. 保存消息到 DB / 内存
    2. 更新频道 last_message_at / last_message_preview
    3. 通过 WebSocket 实时推送
    4. 如果是 Agent 频道且消息以 ``/`` 开头，触发 Agent 执行
    """
    user_id = current_user["user_id"]
    username = current_user.get("username", user_id)

    # 权限检查
    member = await get_member(channel_id, user_id)
    if member is None:
        raise HTTPException(status_code=403, detail="您不是该频道的成员")

    content = req.content.strip()
    if not content:
        raise HTTPException(status_code=400, detail="消息内容不能为空")

    # 保存用户消息
    msg_data = {
        "channel_id": channel_id,
        "sender_id": user_id,
        "sender_type": "user",
        "sender_name": username,
        "content": content,
        "message_type": req.message_type,
        "reply_to_id": req.reply_to_id,
        "metadata_json": req.metadata,
    }
    saved_msg = await save_message(msg_data)

    # 更新频道预览
    await update_channel_last_message(channel_id, content)

    # WebSocket 推送
    await ws_connection_manager.broadcast_to_channel(channel_id, {
        "type": "message",
        "channel_id": channel_id,
        "message": saved_msg,
    })

    # 如果是 Agent 频道且消息以 / 开头，触发 Agent 执行
    ch = await get_channel(channel_id)
    if ch and ch.get("channel_type") == "agent" and content.startswith("/"):
        try:
            await _trigger_agent_execution(channel_id, content, user_id, username, ch)
        except Exception as e:
            logger.warning("messaging.agent_trigger_failed",
                           channel_id=channel_id, error=str(e))

    return {"message": saved_msg}


@router.get("/channels/{channel_id}/members")
async def get_channel_members(
    channel_id: str,
    current_user: dict = Depends(get_current_user),
):
    """获取频道成员列表。"""
    user_id = current_user["user_id"]

    # 权限检查
    member = await get_member(channel_id, user_id)
    if member is None:
        raise HTTPException(status_code=403, detail="您不是该频道的成员")

    members = await get_members(channel_id)

    # 附加用户详情
    enriched = []
    for m in members:
        m_dict = dict(m)
        if m.get("member_type") == "agent":
            # Agent 成员：附加 Agent 目录信息
            agent_info = _AGENT_KEY_MAP.get(m["user_id"])
            if agent_info:
                m_dict["name"] = agent_info["display_name"]
                m_dict["avatar"] = None
                m_dict["description"] = agent_info["description"]
                m_dict["icon"] = agent_info["icon"]
            else:
                m_dict["name"] = m["user_id"]
        else:
            # 用户成员：附加 RBAC 用户信息
            user_info = rbac_engine.get_user(m["user_id"])
            if user_info:
                m_dict["name"] = user_info.get("full_name", m["user_id"])
                m_dict["department"] = user_info.get("department")
                m_dict["position"] = user_info.get("position")
            else:
                m_dict["name"] = m["user_id"]
        enriched.append(m_dict)

    return {"members": enriched}


@router.post("/channels/{channel_id}/read")
async def mark_channel_read(
    channel_id: str,
    req: Optional[MarkReadRequest] = None,
    current_user: dict = Depends(get_current_user),
):
    """标记频道已读（更新 last_read_message_id）。

    如果未提供 ``message_id``，则自动标记为频道最新消息。
    """
    user_id = current_user["user_id"]

    # 权限检查
    member = await get_member(channel_id, user_id)
    if member is None:
        raise HTTPException(status_code=403, detail="您不是该频道的成员")

    # 获取 message_id：优先使用请求体中的值，否则取频道最新消息
    message_id = req.message_id if req and req.message_id else 0
    if message_id == 0:
        last_msg = await get_last_message(channel_id)
        if last_msg:
            message_id = last_msg.get("message_id", 0)

    await mark_read(channel_id, user_id, message_id)

    return {"success": True, "channel_id": channel_id, "last_read_message_id": message_id}


# ===========================================================================
# 文件管理
# ===========================================================================

@router.post("/channels/{channel_id}/files")
async def upload_file(
    channel_id: str,
    file: UploadFile = File(...),
    current_user: dict = Depends(get_current_user),
):
    """上传文件到频道。

    文件保存到 ``tempfile.gettempdir()/chat_files/`` 目录，
    同时在 DB / 内存中记录文件元数据，并发送一条 file 类型消息。
    """
    user_id = current_user["user_id"]
    username = current_user.get("username", user_id)

    # 权限检查
    member = await get_member(channel_id, user_id)
    if member is None:
        raise HTTPException(status_code=403, detail="您不是该频道的成员")

    # 读取文件内容
    content_bytes = await file.read()
    file_size = len(content_bytes)
    if file_size == 0:
        raise HTTPException(status_code=400, detail="文件为空")

    # 生成 file_id 和存储路径
    file_id = f"F-{uuid.uuid4().hex[:16]}"
    original_name = file.filename or "unnamed"
    # 防止路径遍历：只取文件名部分
    safe_name = os.path.basename(original_name)
    stored_filename = f"{file_id}_{safe_name}"
    storage_path = os.path.join(CHAT_FILES_DIR, stored_filename)

    # 写入磁盘
    try:
        with open(storage_path, "wb") as f:
            f.write(content_bytes)
    except Exception as e:
        logger.error("messaging.file_write_failed", error=str(e))
        raise HTTPException(status_code=500, detail="文件保存失败")

    mime_type = file.content_type or "application/octet-stream"

    # 保存文件记录
    file_data = {
        "file_id": file_id,
        "channel_id": channel_id,
        "uploaded_by": user_id,
        "filename": stored_filename,
        "original_name": safe_name,
        "file_size": file_size,
        "mime_type": mime_type,
        "storage_path": storage_path,
    }
    saved_file = await save_file(file_data)

    # 发送文件消息
    msg_data = {
        "channel_id": channel_id,
        "sender_id": user_id,
        "sender_type": "user",
        "sender_name": username,
        "content": f"[文件] {safe_name}",
        "message_type": "file",
        "file_id": file_id,
        "metadata_json": {"file_name": safe_name, "file_size": file_size, "mime_type": mime_type},
    }
    saved_msg = await save_message(msg_data)
    await update_channel_last_message(channel_id, f"[文件] {safe_name}")

    # WebSocket 推送
    await ws_connection_manager.broadcast_to_channel(channel_id, {
        "type": "message",
        "channel_id": channel_id,
        "message": saved_msg,
    })

    return {"file": saved_file, "message": saved_msg}


@router.get("/files/{file_id}")
async def download_file(
    file_id: str,
    current_user: dict = Depends(get_current_user),
):
    """下载文件。"""
    file_record = await get_file(file_id)
    if file_record is None:
        raise HTTPException(status_code=404, detail="文件不存在")

    storage_path = file_record["storage_path"]
    if not os.path.exists(storage_path):
        raise HTTPException(status_code=404, detail="文件已丢失")

    return FileResponse(
        path=storage_path,
        filename=file_record["original_name"],
        media_type=file_record.get("mime_type", "application/octet-stream"),
    )


# ===========================================================================
# Agent 指令
# ===========================================================================

@router.post("/channels/{channel_id}/agent-command")
async def send_agent_command(
    channel_id: str,
    req: AgentCommandRequest,
    current_user: dict = Depends(get_current_user),
):
    """发送 Agent 指令（领导层/有权限者通过聊天指挥 Agent 执行操作）。

    流程：
    1. 保存用户指令消息
    2. 解析目标 agent_id（从请求或频道绑定）
    3. 通过 _agent_refs / orchestrator 获取 Agent 实例
    4. 调用 Agent 的 llm_chat 方法执行指令
    5. 将执行结果作为 Agent 消息保存并发送回频道
    """
    user_id = current_user["user_id"]
    username = current_user.get("username", user_id)

    # 权限检查
    member = await get_member(channel_id, user_id)
    if member is None:
        raise HTTPException(status_code=403, detail="您不是该频道的成员")

    # 解析目标 Agent
    agent_key = req.agent_id
    if not agent_key:
        agent_key = await get_channel_agent_key(channel_id)
    if not agent_key:
        raise HTTPException(status_code=400, detail="无法确定目标 Agent，请指定 agent_id")

    if agent_key not in _AGENT_KEY_MAP:
        raise HTTPException(status_code=400, detail=f"未知的 Agent: {agent_key}")

    command = req.command.strip()
    if not command:
        raise HTTPException(status_code=400, detail="指令内容不能为空")

    agent_info = _AGENT_KEY_MAP[agent_key]
    agent_display_name = agent_info["display_name"]

    # 1. 保存用户指令消息
    cmd_msg_data = {
        "channel_id": channel_id,
        "sender_id": user_id,
        "sender_type": "user",
        "sender_name": username,
        "content": command,
        "message_type": "command",
        "metadata_json": {"agent_id": agent_key, "command": command},
    }
    saved_cmd = await save_message(cmd_msg_data)
    await update_channel_last_message(channel_id, command)

    # WebSocket 推送用户指令
    await ws_connection_manager.broadcast_to_channel(channel_id, {
        "type": "message",
        "channel_id": channel_id,
        "message": saved_cmd,
    })

    # 2-4. 获取 Agent 实例并执行
    agent = _get_agent_instance(agent_key)
    agent_response = ""
    if agent is not None:
        try:
            # 调用 Agent 的 llm_chat 方法
            response = await agent.llm_chat(
                messages=[{"role": "user", "content": command}],
                temperature=0.3,
                max_tokens=2048,
                # 编排语境（Phase A：LangGraph 工具调用 + 人审中断归属）
                user_id=user_id,
                username=username,
                channel_id=channel_id,
                thread_id=f"channel:{channel_id}",
            )
            if response and response.strip():
                agent_response = response.strip()
            else:
                # LLM 不可用时的降级响应
                agent_response = (
                    f"我是{agent_display_name}，已收到您的指令：'{command}'。\n"
                    f"当前认知引擎未启用，无法生成智能回复。"
                    f"您可以通过系统管理界面查看我的实时状态与能力。"
                )
        except Exception as e:
            logger.warning("messaging.agent_command_failed",
                           agent_key=agent_key, error=str(e))
            agent_response = f"Agent 执行指令时发生错误：{str(e)}"
    else:
        agent_response = (
            f"{agent_display_name} 当前未启动，无法执行指令。请稍后再试。"
        )

    # 5. 保存 Agent 响应消息
    response_msg_data = {
        "channel_id": channel_id,
        "sender_id": agent_key,
        "sender_type": "agent",
        "sender_name": agent_display_name,
        "content": agent_response,
        "message_type": "text",
        "metadata_json": {"agent_id": agent_key, "is_command_response": True},
    }
    saved_response = await save_message(response_msg_data)
    await update_channel_last_message(channel_id, agent_response)

    # WebSocket 推送 Agent 响应
    await ws_connection_manager.broadcast_to_channel(channel_id, {
        "type": "message",
        "channel_id": channel_id,
        "message": saved_response,
    })

    return {
        "command_message": saved_cmd,
        "response_message": saved_response,
    }


async def _trigger_agent_execution(
    channel_id: str,
    content: str,
    user_id: str,
    username: str,
    channel: dict[str, Any],
) -> None:
    """当 Agent 频道收到以 ``/`` 开头的消息时，自动触发 Agent 执行。

    与 agent-command 端点类似，但作为消息发送流程的内联触发器。
    """
    # 获取频道绑定的 Agent
    agent_key = await get_channel_agent_key(channel_id)
    if not agent_key or agent_key not in _AGENT_KEY_MAP:
        return

    agent_info = _AGENT_KEY_MAP[agent_key]
    agent_display_name = agent_info["display_name"]

    # 去掉开头的 /
    command = content[1:].strip()
    if not command:
        return

    agent = _get_agent_instance(agent_key)
    agent_response = ""
    if agent is not None:
        try:
            response = await agent.llm_chat(
                messages=[{"role": "user", "content": command}],
                temperature=0.3,
                max_tokens=2048,
            )
            if response and response.strip():
                agent_response = response.strip()
            else:
                agent_response = (
                    f"我是{agent_display_name}，已收到您的指令：'{command}'。"
                    f"当前认知引擎未启用，无法生成智能回复。"
                )
        except Exception as e:
            logger.warning("messaging.agent_inline_failed",
                           agent_key=agent_key, error=str(e))
            agent_response = f"Agent 执行指令时发生错误：{str(e)}"
    else:
        agent_response = f"{agent_display_name} 当前未启动，无法执行指令。"

    # 保存并发送 Agent 响应
    response_msg_data = {
        "channel_id": channel_id,
        "sender_id": agent_key,
        "sender_type": "agent",
        "sender_name": agent_display_name,
        "content": agent_response,
        "message_type": "text",
        "metadata_json": {"agent_id": agent_key, "is_command_response": True, "inline": True},
    }
    saved_response = await save_message(response_msg_data)
    await update_channel_last_message(channel_id, agent_response)

    await ws_connection_manager.broadcast_to_channel(channel_id, {
        "type": "message",
        "channel_id": channel_id,
        "message": saved_response,
    })


# ===========================================================================
# 用户与 Agent 列表
# ===========================================================================

@router.get("/users")
async def list_users(current_user: dict = Depends(get_current_user)):
    """获取可对话的用户列表（用于创建单聊/群聊时选择成员）。

    从 RBAC 引擎获取所有用户，排除当前用户自身。
    """
    user_id = current_user["user_id"]
    try:
        all_users = rbac_engine.list_users()
    except Exception as e:
        logger.warning("messaging.list_users_failed", error=str(e))
        all_users = []

    users = [
        {
            "user_id": u["user_id"],
            "username": u.get("username"),
            "full_name": u.get("full_name"),
            "department": u.get("department"),
            "position": u.get("position"),
            "status": u.get("status", "active"),
            "online": ws_connection_manager.is_online(u["user_id"]),
        }
        for u in all_users
        if u["user_id"] != user_id
    ]

    return {"users": users, "total": len(users)}


@router.get("/agents")
async def list_agents(current_user: dict = Depends(get_current_user)):
    """获取可对话的 Agent 列表。

    返回 AGENT_CATALOG 中所有 Agent 的元数据，附加在线状态
    （Agent 是否已在 orchestrator 中注册并运行）。
    """
    user_id = current_user["user_id"]
    agents = []
    for agent_info in AGENT_CATALOG:
        agent_key = agent_info["key"]
        # 检查是否已有频道
        try:
            existing_ch = await find_agent_channel(user_id, agent_key)
        except Exception:
            existing_ch = None

        # 检查 Agent 实例是否可用
        agent_instance = _get_agent_instance(agent_key)
        is_online = agent_instance is not None

        agents.append({
            "agent_id": agent_key,
            "name": agent_info["display_name"],
            "description": agent_info["description"],
            "icon": agent_info["icon"],
            "online": is_online,
            "has_channel": existing_ch is not None,
            "channel_id": existing_ch["channel_id"] if existing_ch else None,
        })

    return {"agents": agents, "total": len(agents)}
