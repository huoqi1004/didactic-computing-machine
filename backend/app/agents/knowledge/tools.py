"""
知识助手场景工具（知识库改进：Agent 问答赋能）
================================================

把知识库检索注册为治理面工具——**所有业务 Agent 与 Supervisor** 都可以
在编排中调用知识检索（WeKnora 的 ReAct 模式：检索增强回答 + 引用溯源）：

- ``knowledge_search``：混合检索（BM25+语义+图谱四路融合），返回
  分块内容与来源（文件名/分块序号/相关度），供 Agent 引用作答；
- ``knowledge_answer``：检索 + 材料拼装（引用溯源格式），零额外
  LLM 成本（综合由调用方 Agent 完成）。

权限：knowledge:read（沿用知识库权限模型，检索内部自带
permission_level/department 行级过滤）。
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from app.core.agent.tools_v2 import ToolContext, ToolRisk, erp_tool


def _get_agent():
    from app.core.agent.orchestrator import orchestrator

    agent = orchestrator.get_agent_by_name("知识Agent")
    if agent is None:
        raise RuntimeError("知识Agent 未启动")
    return agent


class SearchArgs(BaseModel):
    query: str = Field(..., min_length=1, description="检索问题或关键词")
    top_k: int = Field(5, ge=1, le=20, description="返回条数")


@erp_tool(
    name="knowledge_search",
    description="检索企业知识库（混合检索：语义+关键词+图谱），返回相关分块与来源。回答用户关于公司制度/流程/文档的问题前先调用本工具。",
    args_model=SearchArgs,
    risk_level=ToolRisk.READONLY, resource="knowledge", action="read",
    agent_domain="knowledge",
)
async def knowledge_search(ctx: ToolContext, query: str, top_k: int = 5):
    agent = _get_agent()
    results = await agent.retrieve(
        query=query, top_k=top_k,
        caller_permission_level="internal",
    )
    items = [
        {
            "snippet": r.get("content", "")[:600],
            "source": (r.get("metadata", {}) or {}).get("filename", "")
                      or (r.get("metadata", {}) or {}).get("title", ""),
            "relevance": r.get("score", 0.0),
        }
        for r in results
    ]
    return {"query": query, "total": len(items), "results": items}


class AnswerArgs(BaseModel):
    question: str = Field(..., min_length=1, description="用户问题")
    top_k: int = Field(5, ge=1, le=10, description="引用材料条数")


@erp_tool(
    name="knowledge_answer",
    description="检索知识库并组装带引用的材料（[来源] 分块内容），供回答企业制度/流程问题使用",
    args_model=AnswerArgs,
    risk_level=ToolRisk.READONLY, resource="knowledge", action="read",
    agent_domain="knowledge",
)
async def knowledge_answer(ctx: ToolContext, question: str, top_k: int = 5):
    agent = _get_agent()
    results = await agent.retrieve(
        query=question, top_k=top_k, caller_permission_level="internal",
    )
    if not results:
        return {
            "question": question,
            "answer_material": "知识库中未找到相关内容，请告知用户并建议补充知识文档。",
            "citations": [],
        }
    citations = []
    material_lines = []
    for i, r in enumerate(results, 1):
        meta = r.get("metadata", {}) or {}
        source = meta.get("filename") or meta.get("title") or "未知来源"
        citations.append({"index": i, "source": source,
                          "relevance": r.get("score", 0.0)})
        material_lines.append(f"[{i}] 来源：{source}\n{r.get('content', '')[:600]}")
    return {
        "question": question,
        "answer_material": "\n\n".join(material_lines),
        "citations": citations,
        "note": "请基于以上材料回答，并引用编号 [n]；材料不足以回答时如实说明。",
    }
