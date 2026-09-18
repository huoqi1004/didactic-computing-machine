"""
MySQL Checkpointer（路线图 Phase A / A4，决策 D1）
====================================================

LangGraph ``BaseCheckpointSaver`` 的 MySQL 实现，为人审中断（interrupt）
提供**跨重启的断点恢复**：图状态（含等待审批的挂起点）序列化入 MySQL，
审批通过后经 ``Command(resume=...)`` 从断点继续执行。

设计要点：
- 存储**完整 checkpoint**（含 channel_values）为单一 serde blob——官方
  MemorySaver 按 channel 拆 blob 是内存优化，DB 场景单 blob 更简单可靠；
- 状态 JSON 带版本号与校验和（设计红线：防隐性损坏）；
- 表注册进 ``Base.metadata``，由 bootstrap 差集自动建表；
- 仅实现 async 接口（本项目全异步栈），同步方法显式抛错。

表：
- ``langgraph_checkpoints``：thread/ns/checkpoint 唯一，自增 id 保证时序；
- ``langgraph_checkpoint_writes``：pending writes（含 interrupt 恢复值）。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, AsyncIterator, Iterator, Optional, Sequence

import structlog
from langgraph.checkpoint.base import (
    BaseCheckpointSaver,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
    get_checkpoint_id,
)
from langgraph.types import RunnableConfig  # noqa: TC002  运行时类型

from app.db.repositories.base import has_db, session_scope

logger = structlog.get_logger()

_STATE_VERSION = 1  # 状态格式版本（演进时迁移用）


def _state_checksum(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()[:32]


class MySQLCheckpointSaver(BaseCheckpointSaver):
    """MySQL 持久化 Checkpointer（决策 D1：自研，零新增运维组件）。"""

    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: dict[str, Any],
    ) -> RunnableConfig:
        thread_id = config["configurable"]["thread_id"]
        checkpoint_ns = config["configurable"].get("checkpoint_ns", "")
        checkpoint_id = checkpoint["id"]
        parent_id = config["configurable"].get("checkpoint_id")

        cp_type, cp_blob = self.serde.dumps_typed(checkpoint)
        md_type, md_blob = self.serde.dumps_typed(metadata)

        from app.db.repositories.base import dialect_upsert_stmt

        async with session_scope() as session:
            from app.db.models import langgraph_checkpoints

            values = dict(
                thread_id=thread_id,
                checkpoint_ns=checkpoint_ns,
                checkpoint_id=checkpoint_id,
                checkpoint_type=cp_type,
                checkpoint_blob=cp_blob,
                state_version=_STATE_VERSION,
                state_checksum=_state_checksum(cp_blob),
                metadata_type=md_type,
                metadata_blob=md_blob,
                parent_checkpoint_id=parent_id,
            )
            await session.execute(
                dialect_upsert_stmt(
                    langgraph_checkpoints, values,
                    conflict_cols=["thread_id", "checkpoint_ns", "checkpoint_id"],
                )
            )

        return {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": checkpoint_ns,
                "checkpoint_id": checkpoint_id,
            }
        }

    async def aput_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        thread_id = config["configurable"]["thread_id"]
        checkpoint_ns = config["configurable"].get("checkpoint_ns", "")
        checkpoint_id = get_checkpoint_id(config)
        if checkpoint_id is None:
            return

        from app.db.repositories.base import dialect_upsert_stmt

        async with session_scope() as session:
            from app.db.models import langgraph_checkpoint_writes

            for idx, (channel, value) in enumerate(writes):
                v_type, v_blob = self.serde.dumps_typed(value)
                values = dict(
                    thread_id=thread_id,
                    checkpoint_ns=checkpoint_ns,
                    checkpoint_id=checkpoint_id,
                    task_id=task_id,
                    idx=idx,
                    channel=channel,
                    value_type=v_type,
                    value_blob=v_blob,
                    task_path=task_path,
                )
                await session.execute(
                    dialect_upsert_stmt(langgraph_checkpoint_writes, values)
                )

    async def aget_tuple(self, config: RunnableConfig) -> Optional[CheckpointTuple]:
        thread_id = config["configurable"]["thread_id"]
        checkpoint_ns = config["configurable"].get("checkpoint_ns", "")
        checkpoint_id = get_checkpoint_id(config)

        async with session_scope() as session:
            from app.db.models import langgraph_checkpoints, langgraph_checkpoint_writes
            from sqlalchemy import select

            stmt = select(langgraph_checkpoints).where(
                langgraph_checkpoints.c.thread_id == thread_id,
                langgraph_checkpoints.c.checkpoint_ns == checkpoint_ns,
            )
            if checkpoint_id:
                stmt = stmt.where(langgraph_checkpoints.c.checkpoint_id == checkpoint_id)
            stmt = stmt.order_by(langgraph_checkpoints.c.id.desc()).limit(1)
            row = (await session.execute(stmt)).first()
            if row is None:
                return None

            # 校验和验证（防隐性损坏）
            checksum = _state_checksum(row.checkpoint_blob)
            if checksum != row.state_checksum:
                logger.error(
                    "checkpointer.state_corrupted",
                    thread_id=thread_id,
                    checkpoint_id=row.checkpoint_id,
                    expected=row.state_checksum,
                    actual=checksum,
                )
                raise RuntimeError(
                    f"Checkpointer 状态校验失败（thread={thread_id}），"
                    "该图状态可能已损坏，请删除该 thread 后重试"
                )

            # pending writes
            w_rows = (
                await session.execute(
                    select(langgraph_checkpoint_writes)
                    .where(
                        langgraph_checkpoint_writes.c.thread_id == thread_id,
                        langgraph_checkpoint_writes.c.checkpoint_ns == checkpoint_ns,
                        langgraph_checkpoint_writes.c.checkpoint_id == row.checkpoint_id,
                    )
                    .order_by(langgraph_checkpoint_writes.c.task_id,
                              langgraph_checkpoint_writes.c.idx)
                )
            ).all()

        checkpoint = self.serde.loads_typed((row.checkpoint_type, row.checkpoint_blob))
        metadata = self.serde.loads_typed((row.metadata_type, row.metadata_blob))
        pending_writes = [
            (w.task_id, w.channel, self.serde.loads_typed((w.value_type, w.value_blob)))
            for w in w_rows
        ]
        parent_config = (
            {
                "configurable": {
                    "thread_id": thread_id,
                    "checkpoint_ns": checkpoint_ns,
                    "checkpoint_id": row.parent_checkpoint_id,
                }
            }
            if row.parent_checkpoint_id
            else None
        )
        return CheckpointTuple(
            config=config,
            checkpoint=checkpoint,
            metadata=metadata,
            parent_config=parent_config,
            pending_writes=pending_writes,
        )

    async def alist(
        self,
        config: Optional[RunnableConfig],
        *,
        filter: Optional[dict[str, Any]] = None,
        before: Optional[RunnableConfig] = None,
        limit: Optional[int] = None,
    ) -> AsyncIterator[CheckpointTuple]:
        thread_id = (config or {}).get("configurable", {}).get("thread_id")
        async with session_scope() as session:
            from app.db.models import langgraph_checkpoints
            from sqlalchemy import select

            stmt = select(langgraph_checkpoints).order_by(
                langgraph_checkpoints.c.id.desc()
            )
            if thread_id:
                stmt = stmt.where(langgraph_checkpoints.c.thread_id == thread_id)
            if limit:
                stmt = stmt.limit(limit)
            rows = (await session.execute(stmt)).all()

        for row in rows:
            metadata = self.serde.loads_typed((row.metadata_type, row.metadata_blob))
            if filter and not all(
                metadata.get(k) == v for k, v in filter.items()
            ):
                continue
            # 单条加载（复用 aget_tuple 的 blob 组装逻辑，按 id 精确取）
            tuple_ = await self.aget_tuple(
                {
                    "configurable": {
                        "thread_id": row.thread_id,
                        "checkpoint_ns": row.checkpoint_ns,
                        "checkpoint_id": row.checkpoint_id,
                    }
                }
            )
            if tuple_ is not None:
                yield tuple_

    async def adelete_thread(self, thread_id: str) -> None:
        async with session_scope() as session:
            from app.db.models import langgraph_checkpoints, langgraph_checkpoint_writes
            from sqlalchemy import delete

            await session.execute(
                delete(langgraph_checkpoint_writes).where(
                    langgraph_checkpoint_writes.c.thread_id == thread_id
                )
            )
            await session.execute(
                delete(langgraph_checkpoints).where(
                    langgraph_checkpoints.c.thread_id == thread_id
                )
            )

    # ------------------------------------------------------------------
    # 同步接口：本项目为全异步栈，显式不支持
    # ------------------------------------------------------------------

    def put(self, config, checkpoint, metadata, new_versions):
        raise NotImplementedError("MySQLCheckpointSaver 仅支持异步接口（aput）")

    def put_writes(self, config, writes, task_id, task_path=""):
        raise NotImplementedError("MySQLCheckpointSaver 仅支持异步接口（aput_writes）")

    def get_tuple(self, config):
        raise NotImplementedError("MySQLCheckpointSaver 仅支持异步接口（aget_tuple）")

    def list(self, config, *, filter=None, before=None, limit=None):
        raise NotImplementedError("MySQLCheckpointSaver 仅支持异步接口（alist）")


# 进程内单例（lifespan 校验 DB 可用后初始化；测试可自建实例）
def get_mysql_checkpointer() -> MySQLCheckpointSaver:
    if not has_db():
        raise RuntimeError("MySQL 不可用，无法创建 Checkpointer（人审断点恢复需要持久化）")
    return MySQLCheckpointSaver()
