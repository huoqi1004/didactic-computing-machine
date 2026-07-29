# -*- coding: utf-8 -*-
"""物流Agent

负责发货管理、物流追踪、路线优化与成本控制。
监听生产订单与采购订单事件，发布发货与交付事件。
"""
from app.agents.logistics.agent import LogisticsAgent

__all__ = ["LogisticsAgent"]
