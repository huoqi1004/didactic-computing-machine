# -*- coding: utf-8 -*-
"""内审Agent

负责持续交易监控、舞弊检测、异常识别与合规审计。
监听所有业务事件，识别异常模式并冻结高风险流程。
"""
from app.agents.audit.agent import InternalAuditAgent

__all__ = ["InternalAuditAgent"]
