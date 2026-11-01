# -*- coding: utf-8 -*-
"""风险Agent

负责风险识别、风险评估、风险监控与风险缓解。
监听风险识别、安全警报、合规违规与内审警报事件，
维护企业风险登记册并生成风险矩阵。
"""
from app.agents.risk.agent import RiskAgent

__all__ = ["RiskAgent"]
