# -*- coding: utf-8 -*-
"""项目Agent

负责项目规划、进度跟踪、成本管理与里程碑管理。
监听项目创建、里程碑达成、项目完成与销售订单确认事件，发布项目相关事件。
"""
from app.agents.project.agent import ProjectAgent

__all__ = ["ProjectAgent"]
