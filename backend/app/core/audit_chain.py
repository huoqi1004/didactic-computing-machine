"""原生审计哈希链（D-R3：新架构重建，防篡改不可抵赖）。

追加只读、逐条哈希链接（prev_hash 链接 + 内容哈希）、篡改可验证。
M0 口径：每次 append 现查链尾（规模可控）；规模化后加链尾缓存。
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

import structlog
from sqlalchemy import select

from app.models import AuditEntry

logger = structlog.get_logger()


def _compute_hash(entry: dict, prev_hash: str) -> str:
    payload = json.dumps(
        {k: entry.get(k) for k in
         ("entry_id", "seq", "actor", "action", "resource", "result",
          "details", "timestamp")},
        sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256((payload + prev_hash).encode()).hexdigest()


async def append(session, actor: str, action: str, resource: str = "",
                 result: str = "success",
                 details: Optional[dict] = None) -> dict:
    """追加一条审计（自动接续哈希链）。"""
    last = (await session.execute(
        select(AuditEntry).order_by(AuditEntry.seq.desc()).limit(1))).scalar_one_or_none()
    seq = (last.seq + 1) if last else 1
    prev = last.entry_hash if last else ""

    entry = {
        "entry_id": str(uuid.uuid4()), "seq": seq,
        "actor": actor, "action": action, "resource": resource,
        "result": result, "details": details or {},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    entry["prev_hash"] = prev
    entry["entry_hash"] = _compute_hash(entry, prev)

    session.add(AuditEntry(
        entry_id=entry["entry_id"], seq=seq, actor=actor, action=action,
        resource=resource, result=result,
        details=json.dumps(entry["details"], ensure_ascii=False, default=str),
        prev_hash=prev, entry_hash=entry["entry_hash"],
        timestamp=entry["timestamp"],
    ))
    return entry


async def verify(session) -> dict:
    """全链校验：逐行重算哈希 + 链接核对，篡改/断链点名。

    兼容 AsyncSession 和 AsyncConnection（连接无 ORM 上下文，
    需按列名取值而非 ORM 属性——实测 Connection.execute().scalars()
    对 entity select 返回原始列值）。
    """
    rows = (await session.execute(
        select(AuditEntry).order_by(AuditEntry.seq))).mappings().all()
    prev = ""
    checked = 0
    for r in rows:
        entry = {
            "entry_id": r["entry_id"], "seq": r["seq"], "actor": r["actor"],
            "action": r["action"], "resource": r["resource"],
            "result": r["result"],
            "details": json.loads(r["details"]) if isinstance(r["details"], str) else (r["details"] or {}),
            "timestamp": str(r["timestamp"]),
        }
        expected = _compute_hash(entry, prev)
        if expected != r["entry_hash"] or (r["prev_hash"] or "") != prev:
            return {"status": "broken", "checked": checked + 1,
                    "broken_at": r["entry_id"], "expected": expected,
                    "actual": r["entry_hash"] or ""}
        prev = r["entry_hash"]
        checked += 1
    return {"status": "ok", "checked": checked}
