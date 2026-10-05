# -*- coding: utf-8 -*-
"""
文档产物目录（受控输出目录）
============================

裁决 D-C 要求文档产物落**受控目录**（不再写系统临时目录，临时目录被 OS
清理且与本仓其他数据目录混在一处不可治理）。四个子生成器与下载端点统一
经此模块解析路径，口径只有一处。
"""
from __future__ import annotations

import os
import re
from datetime import datetime

from app.core.config import settings


def output_dir() -> str:
    """受控产物目录（不存在则创建，返回绝对路径）。"""
    path = os.path.abspath(settings.document_output_dir)
    os.makedirs(path, exist_ok=True)
    return path


def resolve_output_path(filename: str) -> str:
    """产物写入路径：文件名安全化（去目录成分，防穿越）后落到受控目录。"""
    return os.path.join(output_dir(), os.path.basename(filename))


def safe_artifact_name(title: str, trace_id: str = "", ext: str = "docx") -> str:
    """
    产物文件名：标题净化 + 时间戳 + trace 短码（避免同名覆盖）。

    用白名单而不是黑名单——标题可能来自模型输出，黑名单正则挡不住 ▶/emoji/控制字符，
    而这些字符进了 Content-Disposition 会打挂下载（产物目录对外经下载端点暴露）。
    """
    stem = re.sub(r"[^\w一-鿿-]+", "", title or "")[:40] or f"ai-draft-{ext}"
    stamp = datetime.now().strftime("%Y%m%d%H%M%S")
    return f"{stem}-{stamp}-{(trace_id or 'na')[:6]}.{ext}"
