# -*- coding: utf-8 -*-
"""生产Agent

负责生产计划制定、产能管理、BOM管理与质量控制。
监听补货审批与库存低预警事件，发布生产订单事件。
"""
from app.agents.production.agent import ProductionAgent

__all__ = ["ProductionAgent"]
