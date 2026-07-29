# -*- coding: utf-8 -*-
"""税务Agent

负责增值税计算、税务申报、税务筹划与风险预警。
监听凭证过账与采购审批事件，自动计算税额。
"""
from app.agents.tax.agent import TaxAgent

__all__ = ["TaxAgent"]
