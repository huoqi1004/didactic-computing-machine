"""智能体长期记忆（M8 · 借鉴 AgentScope LongTermMemory / Octop 共享上下文）。

- ``extract_memories``：从一轮对话中抽取可沉淀的记忆（规则确定性抽取，
  命中显式记忆指令按重要记忆处理）
- ``retrieve_memories``：按 消息词面匹配×2 + 重要度 + 新近度 打分召回 Top-K
- ``touch_memory``：命中即更新 use_count / last_used_at（遗忘曲线依据）
- 会话链路（conversation.chat）：回复前召回注入系统提示词，
  回复后抽取沉淀，全程记入 thinking 步骤
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

import structlog
from sqlalchemy import select

from app.models import AgentMemory

logger = structlog.get_logger()

# 显式记忆指令（"记住/以后都/请记下" → 高重要度沉淀）
_EXPLICIT_PAT = re.compile(r"(记住|记下|以后都|以后请|请牢记|默认帮我|我的偏好)")
# 偏好表达
_PREF_PAT = re.compile(r"(我喜欢|我偏好|我习惯|请用|尽量|以后用|别用|不要用|优先)")
# 事实/背景表达
_FACT_PAT = re.compile(r"(我们公司|我们部门|我是|我在做|我们项目|负责|目前|当前阶段)")

_STOP = {"的", "了", "是", "我", "你", "他", "她", "在", "和", "与", "或", "要",
         "有", "个", "这", "那", "请", "帮", "用", "会", "都", "很", "也", "就",
         "不", "没", "为", "到", "对", "把", "让", "给", "上", "下", "中", "等",
         "the", "a", "an", "is", "are", "to", "of", "and", "for", "in", "on"}


def _tokens(text: str) -> set[str]:
    """极简分词：英文按词、中文按二元组（够用于词面召回打分）。"""
    tokens: set[str] = set()
    for w in re.findall(r"[a-zA-Z][a-zA-Z0-9_-]{1,}", text):
        tokens.add(w.lower())
    zh = re.sub(r"[^\u4e00-\u9fff]+", " ", text)
    for seg in zh.split():
        if len(seg) == 1:
            tokens.add(seg)
            continue
        for i in range(len(seg) - 1):
            tokens.add(seg[i:i + 2])
    return {t for t in tokens if t not in _STOP and len(t) >= 2}


def _classify(content: str) -> tuple[Optional[str], int]:
    """返回 (kind, importance)；无可沉淀内容返回 (None, 0)。"""
    if _EXPLICIT_PAT.search(content):
        return "preference", 5
    if _PREF_PAT.search(content):
        return "preference", 3
    if _FACT_PAT.search(content):
        return "fact", 3
    return None, 0


async def extract_memories(session, username: str, message: str,
                           conv_id: int = 0) -> list[dict]:
    """从用户消息中抽取记忆并沉淀（按内容去重，上限保护）。"""
    # 只从用户消息抽取；截断防超长
    text = message.strip()[:500]
    if len(text) < 6:
        return []
    kind, importance = _classify(text)
    if kind is None:
        return []
    existing = (await session.execute(
        select(AgentMemory).where(AgentMemory.username == username)
        .order_by(AgentMemory.id.desc()).limit(200))).scalars().all()
    if any(m.content == text for m in existing):
        return []
    from app.core.embeddings import embed

    stored = AgentMemory(username=username, kind=kind, content=text,
                         importance=importance, source_conv_id=conv_id)
    session.add(stored)
    await session.flush()
    try:
        stored.embedding = json.dumps(await embed(text))
    except Exception as e:
        logger.warning("memory.embed_failed", error=str(e))
    await session.flush()
    result = [{"id": stored.id, "kind": kind, "importance": importance,
               "content": text[:80]}]
    logger.info("memory.extracted", username=username, kind=kind)
    return result


async def retrieve_memories(session, username: str, query: str,
                            limit: int = 5) -> list[AgentMemory]:
    """召回与查询相关的长期记忆（词面匹配×2 + 重要度 + 30 天内新近加成）。"""
    rows = (await session.execute(
        select(AgentMemory).where(AgentMemory.username == username)
        .order_by(AgentMemory.importance.desc(), AgentMemory.last_used_at.desc())
        .limit(100))).scalars().all()
    if not rows:
        return []
    from app.core.embeddings import cosine, embed

    q_tokens = _tokens(query)
    q_vec = await embed(query)
    now = datetime.now(timezone.utc)
    scored = []
    for m in rows:
        m_tokens = _tokens(m.content)
        overlap = len(q_tokens & m_tokens)
        # 语义相似度（M13）：记忆无向量时懒补并回写
        if m.embedding:
            try:
                m_vec = json.loads(m.embedding)
            except Exception:
                m_vec = None
        else:
            try:
                m_vec = await embed(m.content)
                m.embedding = json.dumps(m_vec)
            except Exception:
                m_vec = None
        sem = cosine(q_vec, m_vec) if m_vec else 0.0
        score = overlap * 2 + m.importance + round(sem * 6, 2)
        try:
            created = m.created_at if m.created_at.tzinfo else m.created_at.replace(tzinfo=timezone.utc)
        except Exception:
            created = m.created_at
        if (now - created) < timedelta(days=30):
            score += 1
        if score >= 4 or sem >= 0.55:  # 词面门槛 或 语义高相似直接命中
            scored.append((score, m))
    scored.sort(key=lambda x: (-x[0], -x[1].importance))
    return [m for _, m in scored[:limit]]


async def touch_memory(session, memory_id: int) -> None:
    m = (await session.execute(
        select(AgentMemory).where(AgentMemory.id == memory_id))).scalar_one_or_none()
    if m is not None:
        m.use_count += 1
        m.last_used_at = datetime.now(timezone.utc)
        await session.flush()


def memory_context(memories: list[AgentMemory]) -> str:
    """把召回的记忆格式化为系统提示词段落。"""
    if not memories:
        return ""
    lines = [f"- [{m.kind}] {m.content[:200]}" for m in memories]
    return ("以下是该用户的长期记忆（个性化参考，非当前对话内容）：\n" + "\n".join(lines))
