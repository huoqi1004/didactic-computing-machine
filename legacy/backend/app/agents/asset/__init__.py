# -*- coding: utf-8 -*-
"""资产Agent

负责固定资产登记、折旧计算（直线法）、资产盘点与处置管理。
监听资产登记、折旧、处置与采购审批事件，发布资产相关事件。
"""
from app.agents.asset.agent import AssetAgent

__all__ = ["AssetAgent"]
