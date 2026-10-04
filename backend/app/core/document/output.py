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

from app.core.config import settings


def output_dir() -> str:
    """受控产物目录（不存在则创建，返回绝对路径）。"""
    path = os.path.abspath(settings.document_output_dir)
    os.makedirs(path, exist_ok=True)
    return path


def resolve_output_path(filename: str) -> str:
    """产物写入路径：文件名安全化（去目录成分，防穿越）后落到受控目录。"""
    return os.path.join(output_dir(), os.path.basename(filename))
