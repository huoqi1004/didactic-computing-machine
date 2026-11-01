# -*- coding: utf-8 -*-
"""资金Agent

负责现金流预测、支付排程、资金调拨与短期投资建议。
监听采购付款与凭证过账事件，优化企业资金运转。
"""
from app.agents.treasury.agent import TreasuryAgent

__all__ = ["TreasuryAgent"]
