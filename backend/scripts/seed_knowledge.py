"""知识库内容建设（M8）——AI/Agent 行业白皮书与开源技术书籍条目种子。

精选 20 个高质量公开资料（白皮书/开源书籍/经典论文/官方指南），
每条含核心要点摘要 + 来源链接，写入 knowledge_entries
（scope=company, is_public=True, owner=system，分段幂等）。

用法：python scripts/seed_knowledge.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# (标题, 分类, 核心要点（摘要）, 来源)
ENTRIES: list[tuple[str, str, str, str]] = [
    ("《动手学深度学习》（Dive into Deep Learning）", "开源书籍",
     "面向中文读者的深度学习开源教材：1) 从线性回归到 Transformer 全覆盖，代码可运行；"
     "2) 每章配套数学推导与 PyTorch 实现；3) 多 GPU 训练、注意力机制、优化算法章节适合工程实践；"
     "4) 被全球 500+ 所大学采用作教材。适合作为团队 AI 基础培训教材。",
     "https://zh.d2l.ai/"),
    ("Anthropic《Building Effective Agents》", "官方指南",
     "Agent 构建方法论白皮书：1) 区分 workflow（预定义流程）与 agent（自主决策循环）；"
     "2) 五种模式：提示链、路由、并行化、编排者-执行者、评估者-优化者；"
     "3) 核心原则：简单优先、透明的过程、充分的工具定义（ACI）；"
     "4) 建议：能 workflow 不 agent，agent 只用在需要开放性决策的场景。",
     "https://www.anthropic.com/engineering/building-effective-agents"),
    ("OpenAI《A Practical Guide to Building Agents》", "官方白皮书",
     "OpenAI 企业 Agent 实战指南：1) Agent 三要素：模型、工具、指令编排；"
     "2) 单智能体优先，复杂度上升再拆多智能体（管理型/去中心型编排）；"
     "3) Guardrails 分层：输入过滤、工具级防护、输出审查、人工接管；"
     "4) 强调评估驱动：先建 eval 再迭代 agent。",
     "https://cdn.openai.com/business-guides-and-resources/a-practical-guide-to-building-agents.pdf"),
    ("Model Context Protocol（MCP）官方规范", "协议规范",
     "Anthropic 开放的模型上下文协议：1) 统一 LLM 与数据源/工具的连接标准（client-server 架构）；"
     "2) 三类原语：tools（可调用）、resources（可读资源）、prompts（提示模板）；"
     "3) 传输层支持 stdio 与 Streamable HTTP；4) 已成事实标准，主流 IDE/Agent 框架均支持。"
     "接入要点：能力协商、权限最小化、超时与重试。",
     "https://modelcontextprotocol.io/"),
    ("Lilian Weng《LLM Powered Autonomous Agents》", "经典综述",
     "Agent 系统奠基性博客：1) Agent = 规划 + 记忆 + 工具使用；"
     "2) 规划：任务分解（CoT/ToT）与反思（Reflexion/Chain of Hindsight）；"
     "3) 记忆：短期=上下文学习，长期=向量库检索（MVP：外部存储+相似度召回）；"
     "4) 工具：Function Calling、代码执行、检索增强（MRKL/Toolformer）。",
     "https://lilianweng.github.io/posts/2023-06-23-agent/"),
    ("Hugging Face Agents Course（开源课程）", "开源课程",
     "HF 官方 Agent 免费课程：1) 单 Agent 基础：ReAct 循环、工具定义、 observability；"
     "2) 多 Agent 协作：LangGraph 状态图、消息传递、角色分工；"
     "3) 用例：RAG Agent、代码 Agent、浏览器操作 Agent；"
     "4) 结业基准：GAIA 通用助手基准。适合工程师入门实操。",
     "https://huggingface.co/learn/agents-course/unit0/introduction"),
    ("ReAct：推理与行动协同", "经典论文",
     "Agent 推理范式开山论文：1) 交替生成思考（Thought）与行动（Action）；"
     "2) 行动结果回填上下文，形成观察-思考-行动循环；"
     "3) 相比纯 CoT 减少幻觉（ grounded on 真实工具返回）；"
     "4) 是绝大多数 Agent 框架的默认执行循环。",
     "https://arxiv.org/abs/2210.03629"),
    ("Reflexion：语言强化学习式自我反思", "经典论文",
     "Agent 自我改进机制：1) 失败后生成语言化反思（verbal RL），存入情景记忆；"
     "2) 下次尝试将反思注入上下文，显著提升任务成功率（HumanEval 91%）；"
     "3) 无需梯度更新，纯上下文学习实现类强化学习效果；"
     "4) 项目监理类角色的理论基础：复盘→改进指令→再执行。",
     "https://arxiv.org/abs/2303.11366"),
    ("Toolformer：让模型学会用工具", "经典论文",
     "Meta 工具使用研究：1) 自监督挖掘 API 调用插入点（哪些位置调用工具能降低困惑度）；"
     "2) 模型自主学会使用计算器/日历/检索/翻译等 API；"
     "3) 证明小模型+工具可超越大模型零工具表现；"
     "4) 思想延续：现代 Function Calling 与工具微调。",
     "https://arxiv.org/abs/2302.04761"),
    ("The Rise and Potential of LLM-based Agents（综述）", "学术综述",
     "人大 WANG Lei 团队 Agent 领域权威综述：1) 从概念框架：大脑（LLM）、感知、行动三模块；"
     "2) 统一回顾了知识、记忆、规划、协作机制；"
     "3) 应用谱系：社会科学、自然科学、工程各方向；"
     "4) 评估方法：人工评估、指标基准、多智能体对弈。",
     "https://arxiv.org/abs/2309.07864"),
    ("Multi-Agent Collaboration 综述（LLM-MA 系统）", "学术综述",
     "多智能体系统全景：1) 协作拓扑：集中式（编排者）、去中心化（消息总线）、竞争式（辩论）；"
     "2) 通信协议：结构化消息、共享黑板（Blackboard）、消息池（MsgHub 思想源流）；"
     "3) 关键发现：角色专业化（role-play）比单纯堆数量更有效；"
     "4) 失败模式：多数投票漂移、上下文互相污染。",
     "https://arxiv.org/abs/2402.01680"),
    ("Prompt Engineering Guide（DAIR.AI 开源）", "开源指南",
     "社区维护的提示工程大全：1) 零样本/少样本/CoT/自洽性/ReAct 技术谱系；"
     "2) RAG、工具使用、Agent 提示模板；"
     "3) 对抗攻击与防御（提示注入、越狱）专题；"
     "4) 多语言版本文档，工程团队提示词规范的参考底本。",
     "https://www.promptingguide.ai/"),
    ("OWASP Top 10 for LLM Applications 2025", "安全白皮书",
     "LLM 应用十大风险（OWASP 官方）：1) 提示注入（直接/间接）居首；"
     "2) 敏感信息泄露、供应链漏洞、不安全输出处理；"
     "3) 过度代理（Excessive Agency）：给 Agent 过高权限是常见事故源；"
     "4) 防护清单：最小权限、沙箱、人审高危动作、输出编码。本系统 intent_guard 参照此设计。",
     "https://genai.owasp.org/llm-top-10/"),
    ("Unit42：AI Agent 技能市场供应链风险分析", "安全分析",
     "Palo Alto Unit42 对 Agent 技能生态的风险研究：1) 恶意 skill 常见手法：提示注入载荷、"
     "伪装工具描述、安装钩子执行远端脚本；2) 数据外传：隐蔽 webhook、环境变量回传；"
     "3) 防控建议：上架前内容扫描、权限声明审查、行为沙箱验证。本项目技能爬取筛查规则参照此设计。",
     "https://unit42.paloaltonetworks.com/openclaw-ai-supply-chain-risk/"),
    ("Retrieval-Augmented Generation for LLM（RAG 综述）", "学术综述",
     "RAG 系统综述：1) Naive RAG → Advanced RAG（预检索优化/混合检索）→ Modular RAG；"
     "2) 关键组件：查询改写、重排序（rerank）、上下文压缩；"
     "3) 评估：忠实度（faithfulness）、答案相关性、上下文精度（RAGAS 三件套）；"
     "4) 失效模式与缓解：检索噪声、丢失中间内容、推理超载。",
     "https://arxiv.org/abs/2312.10997"),
    ("Stanford AI Index Report 2025", "行业白皮书",
     "斯坦福 HAI 年度 AI 索引：1) 推理成本骤降（GPT-3.5 级成本年降 280 倍）；"
     "2) 企业采用：78% 组织已使用 AI，Agent 类应用为增长最快方向；"
     "3) 责任 AI：事故报告创新高，治理框架加速落地；"
     "4) 区域格局：中美模型性能差距显著收窄。董事会数字化战略参考底稿。",
     "https://hai.stanford.edu/ai-index/2025-ai-index-report"),
    ("AgentScope 官方文档与论文", "框架文档",
     "阿里开源多智能体框架：1) 消息驱动（Msg/Pipeline/MsgHub）的灵活编程范式；"
     "2) 长期记忆（LongTermMemory）接口：外部存储+检索注入；"
     "3) 分布式可扩展（AgentServer 池化）；4) 内置 tracing 与 Studio 可视化。"
     "本项目记忆与编排机制参照其接口语义实现。",
     "https://github.com/modelscope/agentscope"),
    ("MetaGPT：软件公司式多智能体协作", "经典论文",
     "SOP 驱动的多智能体框架：1) 把人类公司 SOP 编码进多 Agent 流水线（产品→架构→工程→QA）；"
     "2) 结构化输出（图/接口/代码）替代纯对话，减少级联幻觉；"
     "3) 一行需求生成完整代码仓库的范式；4) 证明：组织知识注入是 MAS 提效关键。",
     "https://arxiv.org/abs/2308.00352"),
    ("Chain-of-Thought 与思维树（ToT）", "经典论文",
     "推理增强技术谱系：1) CoT：中间步骤显著提升复杂推理（GSM8K 18%→57%）；"
     "2) Self-Consistency：多路采样投票；3) ToT：树状探索+回溯+评估器剪枝；"
     "4) 工程启示：复杂规划任务应允许模型'先想后做、多路验证'。",
     "https://arxiv.org/abs/2201.11903"),
    ("Agent Design Pattern 汇编（Microsoft AI Agents for Beginners）", "开源课程",
     "微软开源 Agent 入门课：1) 设计模式：工具使用、RAG、规划、多 Agent、元认知；"
     "2) Agentic RAG：检索作为工具被 Agent 主动调度；"
     "3) 可观测与评估（AI Tracing/Evals）独立成章；"
     "4) 每课配 Azure/AzureOpenAI 代码样例，理念框架无关。",
     "https://github.com/microsoft/ai-agents-for-beginners"),
]


async def seed() -> None:
    from sqlalchemy import select

    from app.core import database
    from app.core.database import get_session
    from app.models import KnowledgeEntry

    await database.init_db()
    session = get_session()
    added = 0
    async with session as s:
        have = set((await s.execute(
            select(KnowledgeEntry.title).where(KnowledgeEntry.owner_id == "system")
        )).scalars().all())
        for title, category, summary, source in ENTRIES:
            if title in have:
                continue
            content = f"{summary}\n\n📎 来源：{source}"
            session.add(KnowledgeEntry(
                title=f"[{category}] {title}", content=content,
                scope="company", owner_id="system", department="IT部",
                tags=f"AI,Agent,{category}", is_public=True))
            added += 1
        await s.commit()
    print(f"知识库条目新增 {added} 条（共 {len(ENTRIES)} 条精选）")


if __name__ == "__main__":
    asyncio.run(seed())
