"""
LLM认知引擎模块
为Agent提供大语言模型驱动的AI智能决策能力

包含组件：
- exceptions: LLM异常定义（LLMUnavailableError, LLMTimeoutError, LLMRateLimitError, LLMParseError）
- client: LLM客户端，对接OpenAI兼容API（Qwen-72B/DeepSeek/Llama4私有化部署）
- prompts: Agent决策prompt模板系统（库存/凭证/采购/风险/通用推理）
- decision_engine: LLM增强的决策引擎，含安全护栏与降级策略
"""
from app.core.llm.exceptions import (
    LLMError,
    LLMUnavailableError,
    LLMTimeoutError,
    LLMRateLimitError,
    LLMParseError,
)
from app.core.llm.client import LLMClient, llm_client
from app.core.llm.prompts import PromptManager, prompt_manager
from app.core.llm.decision_engine import LLMDecisionEngine, llm_decision_engine

__all__ = [
    # 异常
    "LLMError",
    "LLMUnavailableError",
    "LLMTimeoutError",
    "LLMRateLimitError",
    "LLMParseError",
    # 客户端
    "LLMClient",
    "llm_client",
    # 提示词
    "PromptManager",
    "prompt_manager",
    # 决策引擎
    "LLMDecisionEngine",
    "llm_decision_engine",
]
