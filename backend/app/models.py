"""新架构数据模型（全新 schema，不复用旧 58 表）。

域划分：
- 身份：users / audit_chain（原生哈希链，D-R3）
- 知识：knowledge_entries（个人/部门/公司三层）
- 资料库：documents + document_versions（定稿防篡改 + 版本链，D-R3）
- IM：im_channels / im_messages（全员群/部门群/数字分身私聊）
- 待办：todos
- 市场：market_items（专家/专家团/Skill/MCP 四市场，FR-MKT）
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _now():
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


# ========== 身份与角色（五层角色体系） ==========
# 角色枚举：sys_admin / employee / dept_admin / leader / chairman

class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(128))
    display_name: Mapped[str] = mapped_column(String(64), default="")
    role: Mapped[str] = mapped_column(String(24), default="employee")
    # employee / dept_admin / sys_admin / leader / chairman
    department: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(16), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


# ========== 审计哈希链（D-R3：原生重建，防篡改） ==========

class AuditEntry(Base):
    __tablename__ = "audit_chain"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    entry_id: Mapped[str] = mapped_column(String(36), unique=True)
    seq: Mapped[int] = mapped_column(Integer)                # 链序号
    actor: Mapped[str] = mapped_column(String(128))
    action: Mapped[str] = mapped_column(String(128))
    resource: Mapped[str] = mapped_column(String(256), default="")
    result: Mapped[str] = mapped_column(String(32), default="success")
    details: Mapped[str] = mapped_column(Text, default="{}")
    prev_hash: Mapped[str] = mapped_column(String(64), default="")
    entry_hash: Mapped[str] = mapped_column(String(64))
    # 时间戳用 String 存储——DateTime 往返丢 timezone 导致哈希不匹配（实测）
    timestamp: Mapped[str] = mapped_column(String(64))


# ========== 知识库（个人/部门/公司三层） ==========

class KnowledgeEntry(Base):
    __tablename__ = "knowledge_entries"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    scope: Mapped[str] = mapped_column(String(16))   # personal / department / company
    owner_id: Mapped[str] = mapped_column(String(64))            # personal=本人
    department: Mapped[str] = mapped_column(String(64), default="")
    title: Mapped[str] = mapped_column(String(256))
    content: Mapped[str] = mapped_column(Text)
    tags: Mapped[str] = mapped_column(String(512), default="")   # 逗号分隔
    is_public: Mapped[bool] = mapped_column(Boolean, default=False)  # 分身可答的"已授权公开"条目
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


# ========== 实时资料库（定稿防篡改 + 版本链，D-R3） ==========

class BusinessDocument(Base):
    """实时资料库文档：发票/报销单/报税/合同/资产/库存台账/工资单等。

    生命周期：draft（草稿，可编辑）→ final（定稿：落时间戳+哈希+水印，
    不可直接删改）→ 需变更走审批产生新版本（版本链留存）。
    """
    __tablename__ = "documents"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    doc_type: Mapped[str] = mapped_column(String(32))  # invoice/expense/tax/contract/asset/inventory/payslip
    title: Mapped[str] = mapped_column(String(256))
    owner_id: Mapped[str] = mapped_column(String(64))
    department: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="draft")  # draft/final
    version: Mapped[int] = mapped_column(Integer, default=1)
    payload: Mapped[str] = mapped_column(Text, default="{}")          # 业务数据 JSON
    content_hash: Mapped[str] = mapped_column(String(64), default="")  # 定稿哈希（不可抵赖）
    watermark: Mapped[str] = mapped_column(String(256), default="")    # 可见水印：责任人/时间/编号
    finalized_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class DocumentVersion(Base):
    """定稿文档版本链：审批通过的修改产生新版本，旧版本永久留存。"""
    __tablename__ = "document_versions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_id: Mapped[int] = mapped_column(Integer)
    version: Mapped[int] = mapped_column(Integer)
    payload: Mapped[str] = mapped_column(Text)
    changed_by: Mapped[str] = mapped_column(String(64))
    change_reason: Mapped[str] = mapped_column(String(512), default="")
    approved_by: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


# ========== IM（全员群/部门群/数字分身私聊） ==========

class IMChannel(Base):
    __tablename__ = "im_channels"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    type: Mapped[str] = mapped_column(String(24))   # company / department / avatar_private
    name: Mapped[str] = mapped_column(String(128), default="")
    department: Mapped[str] = mapped_column(String(64), default="")
    member_ids: Mapped[str] = mapped_column(Text, default="[]")  # JSON 数组
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class IMMessage(Base):
    __tablename__ = "im_messages"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    channel_id: Mapped[int] = mapped_column(Integer)
    sender_type: Mapped[str] = mapped_column(String(16))  # user / avatar / system
    sender_id: Mapped[str] = mapped_column(String(64))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


# ========== 待办 ==========

class Todo(Base):
    __tablename__ = "todos"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64))
    source: Mapped[str] = mapped_column(String(32))   # approval / workflow / mention / system
    title: Mapped[str] = mapped_column(String(256))
    link: Mapped[str] = mapped_column(String(512), default="")
    status: Mapped[str] = mapped_column(String(16), default="open")  # open/done
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


# ========== 能力市场（专家/专家团/Skill/MCP 四市场） ==========

class MarketItem(Base):
    __tablename__ = "market_items"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(16))     # expert / team / skill / mcp
    name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(Text, default="")
    payload: Mapped[str] = mapped_column(Text, default="{}")  # 能力定义 JSON
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending/approved/rejected
    uploader: Mapped[str] = mapped_column(String(64))
    reviewer: Mapped[str] = mapped_column(String(64), default="")
    review_comment: Mapped[str] = mapped_column(String(512), default="")
    downloads: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


# ========== 智能体会话（工作区 + 三种模式，M6） ==========

class Conversation(Base):
    __tablename__ = "conversations"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64))          # 归属用户
    title: Mapped[str] = mapped_column(String(128), default="新对话")
    workspace: Mapped[str] = mapped_column(String(256), default="")  # 工作区目录名
    agent_mode: Mapped[str] = mapped_column(String(16), default="approve_risky")
    # full_auto / approve_risky / sandbox
    model_id: Mapped[int] = mapped_column(Integer, default=0)  # 0=系统默认模型
    capabilities: Mapped[str] = mapped_column(Text, default="{}")  # 能力引用 JSON
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class ConversationMessage(Base):
    __tablename__ = "conversation_messages"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[int] = mapped_column(Integer)
    role: Mapped[str] = mapped_column(String(16))   # user / assistant
    content: Mapped[str] = mapped_column(Text, default="")
    thinking: Mapped[str] = mapped_column(Text, default="[]")       # 思考/执行步骤 JSON
    attachments: Mapped[str] = mapped_column(Text, default="[]")    # 附件元数据 JSON
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


# ========== 模型接入与 token 额度（管理员管理，M6） ==========

class ModelProvider(Base):
    __tablename__ = "model_providers"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)   # 展示名
    protocol: Mapped[str] = mapped_column(String(16))            # openai / claude
    base_url: Mapped[str] = mapped_column(String(256))
    api_key: Mapped[str] = mapped_column(String(256), default="")
    model_name: Mapped[str] = mapped_column(String(128))         # 供应商侧模型 ID
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class TokenQuota(Base):
    __tablename__ = "token_quotas"
    username: Mapped[str] = mapped_column(String(64), primary_key=True)
    daily_limit: Mapped[int] = mapped_column(Integer, default=100000)  # tokens/日
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class TokenUsage(Base):
    __tablename__ = "token_usage"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64))
    model_name: Mapped[str] = mapped_column(String(128), default="")
    tokens: Mapped[int] = mapped_column(Integer, default=0)
    usage_date: Mapped[str] = mapped_column(String(10))          # YYYY-MM-DD（本地时区）
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


# ========== 财务与项目（看板图表数据，M6） ==========

class FinanceRecord(Base):
    """收支流水（demo 看板数据源）。"""
    __tablename__ = "finance_records"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    month: Mapped[str] = mapped_column(String(7))        # YYYY-MM
    department: Mapped[str] = mapped_column(String(64), default="")
    kind: Mapped[str] = mapped_column(String(8))          # income / expense
    category: Mapped[str] = mapped_column(String(64), default="")
    amount: Mapped[int] = mapped_column(Integer, default=0)  # 万元
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class Project(Base):
    """项目台账（现状/预算/进度）。"""
    __tablename__ = "projects"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128))
    department: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(16), default="running")
    # planning / running / risk / done / paused
    budget: Mapped[int] = mapped_column(Integer, default=0)    # 万元
    spent: Mapped[int] = mapped_column(Integer, default=0)     # 万元
    progress: Mapped[int] = mapped_column(Integer, default=0)  # 0-100
    owner: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


# ========== 智能体长期记忆（M8 · AgentScope 式抽取/检索/注入） ==========

class AgentMemory(Base):
    """用户级长期记忆：偏好/事实/结论，供后续会话召回注入。"""
    __tablename__ = "agent_memories"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(16), default="fact")   # preference / fact / conclusion
    content: Mapped[str] = mapped_column(Text, default="")
    importance: Mapped[int] = mapped_column(Integer, default=3)     # 1-5
    source_conv_id: Mapped[int] = mapped_column(Integer, default=0)
    use_count: Mapped[int] = mapped_column(Integer, default=0)
    last_used_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
