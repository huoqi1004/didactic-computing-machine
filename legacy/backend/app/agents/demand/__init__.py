# -*- coding: utf-8 -*-
"""需求先知Agent

负责需求预测、趋势分析与季节性检测。
基于历史销售数据预测未来需求，识别异常消耗模式。
"""
from app.agents.demand.agent import DemandForecastAgent

__all__ = ["DemandForecastAgent"]
