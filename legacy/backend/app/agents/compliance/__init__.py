# -*- coding: utf-8 -*-
"""合规Agent

负责法规解析、合规检查、控制点管理与合规报告。
监听内审警报与所有业务事件，确保时时合规。
"""
from app.agents.compliance.agent import ComplianceAgent

__all__ = ["ComplianceAgent"]
