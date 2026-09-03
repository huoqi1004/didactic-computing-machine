"""
WebSocket 连接管理器
====================

支持用户级定向推送的 WebSocket 连接管理。

特性：
- 按 user_id 维护连接池，支持同一用户多连接
- 定向推送（send_to_user）和频道广播（broadcast_to_channel）
- 自动清理断开连接
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any, Dict, Set

import structlog
from fastapi import WebSocket

logger = structlog.get_logger()


class WSConnectionManager:
    """用户级 WebSocket 连接管理器"""

    def __init__(self) -> None:
        # user_id -> set[WebSocket]（同一用户可能多端在线）
        self._user_connections: Dict[str, Set[WebSocket]] = {}
        # channel_id -> set[user_id]（频道成员在线状态）
        self._channel_users: Dict[str, Set[str]] = {}
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket, user_id: str) -> None:
        """接受连接并注册到用户连接池"""
        await websocket.accept()
        async with self._lock:
            if user_id not in self._user_connections:
                self._user_connections[user_id] = set()
            self._user_connections[user_id].add(websocket)
        logger.info("ws.connected", user_id=user_id, total=len(self._user_connections.get(user_id, set())))

    async def disconnect(self, websocket: WebSocket, user_id: str) -> None:
        """移除断开连接"""
        async with self._lock:
            conns = self._user_connections.get(user_id)
            if conns:
                conns.discard(websocket)
                if not conns:
                    del self._user_connections[user_id]
                    # 从所有频道中移除
                    for members in self._channel_users.values():
                        members.discard(user_id)
        logger.info("ws.disconnected", user_id=user_id)

    def join_channel(self, channel_id: str, user_id: str) -> None:
        """用户加入频道"""
        if channel_id not in self._channel_users:
            self._channel_users[channel_id] = set()
        self._channel_users[channel_id].add(user_id)

    def leave_channel(self, channel_id: str, user_id: str) -> None:
        """用户离开频道"""
        members = self._channel_users.get(channel_id)
        if members:
            members.discard(user_id)
            if not members:
                del self._channel_users[channel_id]

    async def send_to_user(self, user_id: str, message: dict) -> None:
        """定向推送给指定用户的所有连接"""
        conns = self._user_connections.get(user_id, set()).copy()
        dead = []
        for ws in conns:
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        if dead:
            async with self._lock:
                for ws in dead:
                    self._user_connections.get(user_id, set()).discard(ws)

    async def broadcast_to_channel(self, channel_id: str, message: dict, exclude_user: str | None = None) -> None:
        """广播给频道内所有在线成员"""
        members = self._channel_users.get(channel_id, set()).copy()
        for user_id in members:
            if exclude_user and user_id == exclude_user:
                continue
            await self.send_to_user(user_id, message)

    async def broadcast(self, message: dict) -> None:
        """全局广播"""
        all_conns: set[WebSocket] = set()
        for conns in self._user_connections.values():
            all_conns.update(conns)
        dead = []
        for ws in all_conns:
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        # 清理死连接省略，在 disconnect 中处理

    def get_online_users(self) -> list[str]:
        """获取所有在线用户ID"""
        return list(self._user_connections.keys())

    def is_online(self, user_id: str) -> bool:
        """检查用户是否在线"""
        return user_id in self._user_connections and len(self._user_connections[user_id]) > 0


# 全局单例
ws_connection_manager = WSConnectionManager()
