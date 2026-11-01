# -*- coding: utf-8 -*-
"""
招聘Agent测试
验证职位管理、候选人管理、面试安排、Offer发放、事件处理与决策功能
"""
import pytest

from app.core.event.models import Event, EventType
from app.core.security.did import did_registry
from app.agents.recruitment.agent import RecruitmentAgent


# ========== 辅助函数 ==========

async def _setup_agent() -> RecruitmentAgent:
    """创建并初始化招聘Agent（不启动主循环，仅注册身份和订阅）"""
    agent = RecruitmentAgent()
    agent.identity = did_registry.register_agent(
        name=agent.name,
        domain=agent.domain,
        capabilities=agent.capabilities,
        security_clearance=agent.security_clearance,
    )
    await agent.initialize()
    return agent


def _make_candidate_created_event(
    candidate_id: str = "CAND-EVT-001",
    name: str = "张三",
    position_id: str = "POS0001",
    source: str = "boss",
    resume_summary: str = "5年Java开发经验",
) -> Event:
    """创建候选人创建事件"""
    return Event(
        event_type=EventType.CANDIDATE_CREATED,
        aggregate_id=candidate_id,
        aggregate_type="candidate",
        event_data={
            "candidate_id": candidate_id,
            "name": name,
            "position_id": position_id,
            "source": source,
            "resume_summary": resume_summary,
            "status": "new",
        },
    )


def _make_interview_scheduled_event(
    candidate_id: str = "CAND-EVT-001",
    position_id: str = "POS0001",
    interview_id: str = "IV-EVT-001",
    interview_date: str = "2026-09-01T10:00:00Z",
    interviewer: str = "李面试官",
    round_num: int = 1,
) -> Event:
    """创建面试安排事件"""
    return Event(
        event_type=EventType.INTERVIEW_SCHEDULED,
        aggregate_id=candidate_id,
        aggregate_type="interview",
        event_data={
            "candidate_id": candidate_id,
            "position_id": position_id,
            "interview_id": interview_id,
            "interview_date": interview_date,
            "interviewer": interviewer,
            "round_num": round_num,
            "status": "scheduled",
        },
    )


def _make_offer_extended_event(
    candidate_id: str = "CAND-EVT-001",
    position_id: str = "POS0001",
    offer_id: str = "OFR-EVT-001",
    salary: float = 25000,
    position: str = "Java工程师",
    start_date: str = "2026-10-01",
) -> Event:
    """创建Offer发放事件"""
    return Event(
        event_type=EventType.OFFER_EXTENDED,
        aggregate_id=candidate_id,
        aggregate_type="offer",
        event_data={
            "candidate_id": candidate_id,
            "position_id": position_id,
            "offer_id": offer_id,
            "salary": salary,
            "position": position,
            "start_date": start_date,
            "status": "pending",
        },
    )


# ========== test_agent_initialization ==========

async def test_agent_initialization():
    """Agent初始化与身份注册"""
    agent = await _setup_agent()

    assert agent.name == "招聘Agent"
    assert agent.domain == "recruitment"
    assert agent.security_clearance == "confidential"
    assert "candidate_management" in agent.capabilities
    assert "interview_scheduling" in agent.capabilities
    assert "offer_management" in agent.capabilities
    assert "job_posting" in agent.capabilities
    assert "general" in agent.capabilities
    assert agent.identity is not None
    assert agent.identity.did is not None


# ========== test_seed_data ==========

async def test_seed_data():
    """种子数据加载3个预置职位"""
    agent = await _setup_agent()

    positions = agent.get_positions()
    assert len(positions) == 3

    position_ids = [p["position_id"] for p in positions]
    assert "POS0001" in position_ids
    assert "POS0002" in position_ids
    assert "POS0003" in position_ids

    # 验证职位详情
    pos1 = agent._positions["POS0001"]
    assert pos1["title"] == "Java工程师"
    assert pos1["department"] == "技术部"
    assert pos1["status"] == "open"
    assert pos1["salary_range"] == {"min": 15000, "max": 30000}

    # 验证编号计数器
    assert agent._position_seq == 3


# ========== test_create_position ==========

async def test_create_position():
    """创建新职位"""
    agent = await _setup_agent()

    position = agent.create_position(
        title="测试工程师",
        department="质量部",
        requirements=["3年测试经验", "熟悉自动化测试"],
        headcount=2,
        salary_range={"min": 12000, "max": 25000},
    )

    assert position["position_id"] == "POS0004"
    assert position["title"] == "测试工程师"
    assert position["department"] == "质量部"
    assert position["headcount"] == 2
    assert position["status"] == "open"
    assert position["salary_range"] == {"min": 12000, "max": 25000}

    # 验证已加入列表
    all_positions = agent.get_positions()
    assert len(all_positions) == 4
    assert agent._position_seq == 4


# ========== test_add_candidate ==========

async def test_add_candidate():
    """新增候选人"""
    agent = await _setup_agent()

    candidate = agent.add_candidate(
        name="王五",
        position_id="POS0001",
        source="internal",
        resume_summary="3年Java开发经验",
        phone="13800000000",
        email="wangwu@example.com",
    )

    assert candidate["candidate_id"] == "CAND0001"
    assert candidate["name"] == "王五"
    assert candidate["position_id"] == "POS0001"
    assert candidate["source"] == "internal"
    assert candidate["status"] == RecruitmentAgent.STATUS_NEW
    assert candidate["source_type"] == "manual"

    # 验证已加入列表
    all_candidates = agent.get_candidates()
    assert len(all_candidates) == 1
    assert agent._candidate_seq == 1


# ========== test_update_candidate_status ==========

async def test_update_candidate_status():
    """更新候选人状态"""
    agent = await _setup_agent()

    candidate = agent.add_candidate(
        name="赵六",
        position_id="POS0001",
        source="referral",
        resume_summary="5年产品经验",
    )
    candidate_id = candidate["candidate_id"]

    updated = agent.update_candidate_status(
        candidate_id, RecruitmentAgent.STATUS_SCREENING
    )

    assert updated["status"] == RecruitmentAgent.STATUS_SCREENING
    assert "updated_at" in updated

    # 验证不存在的候选人
    result = agent.update_candidate_status("NONEXIST", RecruitmentAgent.STATUS_NEW)
    assert "error" in result


# ========== test_schedule_interview ==========

async def test_schedule_interview():
    """安排面试并更新候选人状态"""
    agent = await _setup_agent()

    candidate = agent.add_candidate(
        name="钱七",
        position_id="POS0001",
        source="boss",
        resume_summary="4年Java开发经验",
    )
    candidate_id = candidate["candidate_id"]

    interview = agent.schedule_interview(
        candidate_id=candidate_id,
        interview_date="2026-09-01T14:00:00Z",
        interviewer="张面试官",
        round_num=1,
    )

    assert interview["candidate_id"] == candidate_id
    assert interview["position_id"] == "POS0001"
    assert interview["interviewer"] == "张面试官"
    assert interview["round_num"] == 1
    assert interview["status"] == RecruitmentAgent.INTERVIEW_SCHEDULED
    assert interview["source"] == "manual"

    # 验证候选人状态已更新为面试中
    assert agent._candidates[candidate_id]["status"] == \
        RecruitmentAgent.STATUS_INTERVIEWING

    # 验证面试记录已加入列表
    interviews = agent.get_interviews()
    assert len(interviews) == 1


# ========== test_extend_offer ==========

async def test_extend_offer():
    """发放Offer并更新候选人状态"""
    agent = await _setup_agent()

    candidate = agent.add_candidate(
        name="孙八",
        position_id="POS0001",
        source="linkedin",
        resume_summary="6年Java开发经验",
    )
    candidate_id = candidate["candidate_id"]

    offer = agent.extend_offer(
        candidate_id=candidate_id,
        salary=28000,
        position="Java工程师",
        start_date="2026-10-01",
    )

    assert offer["candidate_id"] == candidate_id
    assert offer["position_id"] == "POS0001"
    assert offer["salary"] == 28000
    assert offer["position"] == "Java工程师"
    assert offer["start_date"] == "2026-10-01"
    assert offer["status"] == RecruitmentAgent.OFFER_PENDING
    assert offer["source"] == "manual"

    # 验证候选人状态已更新为已发Offer
    assert agent._candidates[candidate_id]["status"] == \
        RecruitmentAgent.STATUS_OFFERED

    # 验证Offer记录已加入列表
    offers = agent.get_offers()
    assert len(offers) == 1


# ========== test_candidate_created_event ==========

async def test_candidate_created_event():
    """候选人创建事件同步候选人数据"""
    agent = await _setup_agent()

    event = _make_candidate_created_event(
        candidate_id="CAND-EVT-001",
        name="周九",
        position_id="POS0001",
    )
    await agent.process_event(event)

    assert "CAND-EVT-001" in agent._candidates
    candidate = agent._candidates["CAND-EVT-001"]
    assert candidate["name"] == "周九"
    assert candidate["position_id"] == "POS0001"
    assert candidate["source_type"] == "event"
    assert candidate["status"] == RecruitmentAgent.STATUS_NEW


# ========== test_interview_scheduled_event ==========

async def test_interview_scheduled_event():
    """面试安排事件同步面试记录并更新候选人状态"""
    agent = await _setup_agent()

    # 先通过事件创建候选人
    await agent.process_event(
        _make_candidate_created_event(candidate_id="CAND-EVT-001")
    )

    # 发送面试安排事件
    await agent.process_event(
        _make_interview_scheduled_event(candidate_id="CAND-EVT-001")
    )

    # 验证面试记录已添加
    interviews = agent.get_interviews()
    assert len(interviews) == 1
    assert interviews[0]["candidate_id"] == "CAND-EVT-001"
    assert interviews[0]["source"] == "event"

    # 验证候选人状态已更新为面试中
    assert agent._candidates["CAND-EVT-001"]["status"] == \
        RecruitmentAgent.STATUS_INTERVIEWING


# ========== test_offer_extended_event ==========

async def test_offer_extended_event():
    """Offer发放事件同步Offer记录并更新候选人状态"""
    agent = await _setup_agent()

    # 先通过事件创建候选人
    await agent.process_event(
        _make_candidate_created_event(candidate_id="CAND-EVT-001")
    )

    # 发送Offer发放事件
    await agent.process_event(
        _make_offer_extended_event(candidate_id="CAND-EVT-001", salary=25000)
    )

    # 验证Offer记录已添加
    offers = agent.get_offers()
    assert len(offers) == 1
    assert offers[0]["candidate_id"] == "CAND-EVT-001"
    assert offers[0]["salary"] == 25000
    assert offers[0]["source"] == "event"

    # 验证候选人状态已更新为已发Offer
    assert agent._candidates["CAND-EVT-001"]["status"] == \
        RecruitmentAgent.STATUS_OFFERED


# ========== test_make_decision_schedule_interview ==========

async def test_make_decision_schedule_interview():
    """面试安排决策：候选人符合要求"""
    agent = await _setup_agent()

    candidate = agent.add_candidate(
        name="吴十",
        position_id="POS0001",
        source="boss",
        resume_summary="4年Java开发经验",
    )

    decision = await agent.make_decision({
        "action": "schedule_interview",
        "candidate_id": candidate["candidate_id"],
    })

    assert decision.decision_type == "interview_scheduling"
    assert decision.confidence == 0.85
    assert decision.requires_human_approval is False
    assert "安排面试" in decision.action


# ========== test_make_decision_offer_within_range ==========

async def test_make_decision_offer_within_range():
    """Offer发放决策：薪资在范围内，仍需人工审批"""
    agent = await _setup_agent()

    candidate = agent.add_candidate(
        name="郑十一",
        position_id="POS0001",
        source="boss",
        resume_summary="5年Java开发经验",
    )

    decision = await agent.make_decision({
        "action": "extend_offer",
        "candidate_id": candidate["candidate_id"],
        "salary": 25000,  # POS0001 max=30000，在范围内
    })

    assert decision.decision_type == "offer_extension"
    assert decision.confidence == 0.9
    assert decision.requires_human_approval is True
    assert "发放Offer" in decision.action


# ========== test_make_decision_offer_exceeds_max ==========

async def test_make_decision_offer_exceeds_max():
    """Offer发放决策：薪资超出职位上限，转人工审批"""
    agent = await _setup_agent()

    candidate = agent.add_candidate(
        name="王十二",
        position_id="POS0001",
        source="boss",
        resume_summary="8年Java开发经验",
    )

    decision = await agent.make_decision({
        "action": "extend_offer",
        "candidate_id": candidate["candidate_id"],
        "salary": 35000,  # POS0001 max=30000，超出上限
    })

    assert decision.decision_type == "offer_extension"
    assert decision.confidence == 0.8
    assert decision.requires_human_approval is True
    assert "转人工审批" in decision.action


# ========== test_make_decision_reject ==========

async def test_make_decision_reject():
    """候选人淘汰决策"""
    agent = await _setup_agent()

    candidate = agent.add_candidate(
        name="李十三",
        position_id="POS0001",
        source="boss",
        resume_summary="1年经验",
    )

    decision = await agent.make_decision({
        "action": "reject_candidate",
        "candidate_id": candidate["candidate_id"],
    })

    assert decision.decision_type == "candidate_rejection"
    assert decision.confidence == 0.85
    assert decision.requires_human_approval is False
    assert "已拒绝" in decision.action


# ========== test_make_decision_unknown ==========

async def test_make_decision_unknown():
    """未知操作返回默认决策"""
    agent = await _setup_agent()

    decision = await agent.make_decision({"action": "unknown_action"})

    assert decision.decision_type == "recruitment_unknown"
    assert decision.confidence == 0.0
    assert decision.requires_human_approval is False


# ========== test_get_positions_filter ==========

async def test_get_positions_filter():
    """职位列表按状态过滤"""
    agent = await _setup_agent()

    # 种子数据3个职位均为open，再创建一个closed职位
    agent.create_position(
        title="已关闭职位",
        department="测试部",
        requirements=["测试"],
        headcount=1,
        status="closed",
    )

    all_positions = agent.get_positions()
    assert len(all_positions) == 4

    open_positions = agent.get_positions(status="open")
    assert len(open_positions) == 3
    assert all(p["status"] == "open" for p in open_positions)

    closed_positions = agent.get_positions(status="closed")
    assert len(closed_positions) == 1
    assert closed_positions[0]["title"] == "已关闭职位"


# ========== test_get_candidates_filter ==========

async def test_get_candidates_filter():
    """候选人列表按职位和状态过滤"""
    agent = await _setup_agent()

    # 添加不同职位的候选人
    c1 = agent.add_candidate("张A", "POS0001", "boss", "简历A")
    c2 = agent.add_candidate("张B", "POS0001", "boss", "简历B")
    c3 = agent.add_candidate("张C", "POS0002", "boss", "简历C")

    # 更新c2状态为筛选中
    agent.update_candidate_status(c2["candidate_id"],
                                  RecruitmentAgent.STATUS_SCREENING)

    # 全部候选人
    all_candidates = agent.get_candidates()
    assert len(all_candidates) == 3

    # 按职位过滤
    pos1_candidates = agent.get_candidates(position_id="POS0001")
    assert len(pos1_candidates) == 2
    assert all(c["position_id"] == "POS0001" for c in pos1_candidates)

    pos2_candidates = agent.get_candidates(position_id="POS0002")
    assert len(pos2_candidates) == 1
    assert pos2_candidates[0]["name"] == "张C"

    # 按状态过滤
    new_candidates = agent.get_candidates(status=RecruitmentAgent.STATUS_NEW)
    assert len(new_candidates) == 2

    screening_candidates = agent.get_candidates(
        status=RecruitmentAgent.STATUS_SCREENING
    )
    assert len(screening_candidates) == 1
    assert screening_candidates[0]["name"] == "张B"

    # 组合过滤
    pos1_new = agent.get_candidates(
        position_id="POS0001", status=RecruitmentAgent.STATUS_NEW
    )
    assert len(pos1_new) == 1
    assert pos1_new[0]["name"] == "张A"


# ========== test_get_interviews_filter ==========

async def test_get_interviews_filter():
    """面试记录按候选人和状态过滤"""
    agent = await _setup_agent()

    c1 = agent.add_candidate("李A", "POS0001", "boss", "简历A")
    c2 = agent.add_candidate("李B", "POS0002", "boss", "简历B")

    # 为两个候选人各安排一场面试
    agent.schedule_interview(c1["candidate_id"], "2026-09-01T10:00:00Z", "面试官A")
    agent.schedule_interview(c2["candidate_id"], "2026-09-02T10:00:00Z", "面试官B")

    # 手动将第一场面试状态改为已完成
    agent._interviews[0]["status"] = RecruitmentAgent.INTERVIEW_COMPLETED

    # 全部面试
    all_interviews = agent.get_interviews()
    assert len(all_interviews) == 2

    # 按候选人过滤
    c1_interviews = agent.get_interviews(candidate_id=c1["candidate_id"])
    assert len(c1_interviews) == 1
    assert c1_interviews[0]["candidate_id"] == c1["candidate_id"]

    # 按状态过滤
    scheduled = agent.get_interviews(status=RecruitmentAgent.INTERVIEW_SCHEDULED)
    assert len(scheduled) == 1
    assert scheduled[0]["candidate_id"] == c2["candidate_id"]

    completed = agent.get_interviews(status=RecruitmentAgent.INTERVIEW_COMPLETED)
    assert len(completed) == 1
    assert completed[0]["candidate_id"] == c1["candidate_id"]


# ========== test_get_offers_filter ==========

async def test_get_offers_filter():
    """Offer记录按状态过滤"""
    agent = await _setup_agent()

    c1 = agent.add_candidate("赵A", "POS0001", "boss", "简历A")
    c2 = agent.add_candidate("赵B", "POS0002", "boss", "简历B")

    # 为两个候选人各发放一个Offer（默认pending）
    agent.extend_offer(c1["candidate_id"], 25000, "Java工程师", "2026-10-01")
    agent.extend_offer(c2["candidate_id"], 30000, "产品经理", "2026-10-01")

    # 手动将第一个Offer状态改为已接受
    agent._offers[0]["status"] = RecruitmentAgent.OFFER_ACCEPTED

    # 全部Offer
    all_offers = agent.get_offers()
    assert len(all_offers) == 2

    # 按状态过滤
    pending_offers = agent.get_offers(status=RecruitmentAgent.OFFER_PENDING)
    assert len(pending_offers) == 1
    assert pending_offers[0]["candidate_id"] == c2["candidate_id"]

    accepted_offers = agent.get_offers(status=RecruitmentAgent.OFFER_ACCEPTED)
    assert len(accepted_offers) == 1
    assert accepted_offers[0]["candidate_id"] == c1["candidate_id"]


# ========== test_get_stats ==========

async def test_get_stats():
    """招聘统计信息"""
    agent = await _setup_agent()

    # 添加候选人和面试、Offer
    c1 = agent.add_candidate("钱A", "POS0001", "boss", "简历A")
    c2 = agent.add_candidate("钱B", "POS0001", "boss", "简历B")

    agent.schedule_interview(c1["candidate_id"], "2026-09-01T10:00:00Z", "面试官A")
    agent.extend_offer(c1["candidate_id"], 25000, "Java工程师", "2026-10-01")

    # 手动将Offer改为已接受
    agent._offers[0]["status"] = RecruitmentAgent.OFFER_ACCEPTED

    stats = agent.get_stats()

    assert stats["total_positions"] == 3
    assert stats["open_positions"] == 3
    assert stats["total_candidates"] == 2
    assert stats["total_interviews"] == 1
    assert stats["total_offers"] == 1
    assert stats["accepted_offers"] == 1
    assert stats["offer_acceptance_rate"] == 1.0
    assert RecruitmentAgent.STATUS_NEW in stats["candidates_by_status"]
