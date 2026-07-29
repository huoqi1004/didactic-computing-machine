# -*- coding: utf-8 -*-
"""质量Agent

负责质检记录管理、质量控制、改进措施管理与质量标准维护。
监听质检结果与生产完成事件，发布质量相关事件。
"""
from app.agents.quality.agent import QualityAgent

__all__ = ["QualityAgent"]
