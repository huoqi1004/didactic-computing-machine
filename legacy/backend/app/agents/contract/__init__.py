# -*- coding: utf-8 -*-
"""合同Agent

负责合同管理、合同执行、合同归档与模板管理。
监听合同创建、审批、到期及商机赢单、采购审批事件，
管理合同全生命周期。
"""
from app.agents.contract.agent import ContractAgent

__all__ = ["ContractAgent"]
