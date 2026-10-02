"""
知识库 API 路由
================

提供知识库管理、文档检索、文档摄入、审批等 HTTP API 端点。

端点分组：
- /knowledge/search:        知识检索
- /knowledge/documents:     文档管理（CRUD）
- /knowledge/ingest:        文档摄入
- /knowledge/categories:    分类管理
- /knowledge/review:        审批管理
- /knowledge/graph:         知识图谱查询
- /knowledge/rules:         业务规则评估
- /knowledge/stats:         统计信息
"""
import structlog
from fastapi import APIRouter, Depends, UploadFile, File, HTTPException, Query
from pydantic import BaseModel, Field
from typing import Optional

from app.api.middleware.auth import get_current_user, require_permission
from app.core.knowledge.manager import (
    knowledge_manager,
    DOC_STATUS_DRAFT,
    DOC_STATUS_PENDING,
    DOC_STATUS_PUBLISHED,
    DOC_STATUS_REJECTED,
    DOC_STATUS_ARCHIVED,
)
from app.core.knowledge.hybrid import hybrid_engine
from app.core.knowledge.graph import knowledge_graph
from app.core.knowledge.rules import rule_engine

logger = structlog.get_logger()

router = APIRouter(prefix="/api/v1/knowledge", tags=["知识库"])


async def _log_knowledge_op(current_user: dict, action: str, record_id: str,
                            details: dict, result: str = "success") -> None:
    """G2.1 统一操作日志：知识库变更面接入（审计链 + 操作日志双层）。"""
    try:
        from app.core.observability.operation_log import log_operation

        await log_operation(
            actor=current_user.get("user_id", "system"),
            action=action,
            resource=f"knowledge:{record_id}" if record_id else "knowledge",
            result=result,
            details=details,
            risk_level="medium" if action.startswith("knowledge.review") else "low",
            source="http",
            to_audit_chain=True,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("knowledge.operation_log_failed", action=action, error=str(e))


# =============================================================================
# 请求模型
# =============================================================================

class SearchRequest(BaseModel):
    """知识检索请求"""
    query: str = Field(..., description="查询文本", min_length=1, max_length=500)
    top_k: int = Field(5, description="返回结果数上限", ge=1, le=50)
    category: Optional[str] = Field(None, description="分类过滤")
    tags: Optional[list[str]] = Field(None, description="标签过滤")


class IngestTextRequest(BaseModel):
    """文本摄入请求"""
    text: str = Field(..., description="纯文本内容", min_length=1)
    filename: str = Field("", description="文件名")
    category: str = Field("uncategorized", description="分类")
    department: str = Field("general", description="归属部门")
    permission_level: str = Field("internal", description="权限级别")
    tags: Optional[list[str]] = Field(None, description="标签列表")
    auto_publish: bool = Field(True, description="是否自动发布")


class CreateCategoryRequest(BaseModel):
    """创建分类请求"""
    name: str = Field(..., description="分类名称", min_length=1, max_length=50)
    description: str = Field("", description="分类描述")
    department: str = Field("general", description="归属部门")


class SubmitReviewRequest(BaseModel):
    """提交审批请求"""
    record_id: str = Field(..., description="文档记录ID")


class ApproveRequest(BaseModel):
    """审批通过请求"""
    comment: str = Field("", description="审批意见")


class RejectRequest(BaseModel):
    """审批拒绝请求"""
    comment: str = Field("", description="拒绝原因")


class GraphQueryRequest(BaseModel):
    """知识图谱查询请求"""
    query: str = Field(..., description="查询字符串")


class RuleEvalRequest(BaseModel):
    """规则评估请求"""
    context: dict = Field(..., description="评估上下文")


# =============================================================================
# 检索端点
# =============================================================================

@router.post("/search", summary="知识检索")
async def search_knowledge(
    req: SearchRequest,
    current_user: dict = Depends(get_current_user),
):
    """混合检索知识库（向量+关键词+图谱三路融合）"""
    user_dept = current_user.get("department")
    # 从用户角色推断权限级别
    user_perm = "internal"
    roles = current_user.get("role_codes", [])
    if any(r in ("chairman", "ceo", "cio") for r in roles):
        user_perm = "restricted"
    elif any(r in ("cto", "cfo", "coo", "cmo") for r in roles):
        user_perm = "confidential"

    results = await knowledge_manager.search(
        query=req.query,
        top_k=req.top_k,
        user_permission_level=user_perm,
        user_department=user_dept,
        category=req.category,
        tags=req.tags,
    )

    return {
        "query": req.query,
        "total": len(results),
        "results": results,
    }


# =============================================================================
# 文档摄入端点
# =============================================================================

@router.post("/ingest/text", summary="摄入纯文本")
async def ingest_text(
    req: IngestTextRequest,
    current_user: dict = Depends(require_permission("knowledge", "create")),
):
    """摄入纯文本文档到知识库"""
    try:
        record = knowledge_manager.upload_text(
            text=req.text,
            filename=req.filename,
            category=req.category,
            department=req.department,
            permission_level=req.permission_level,
            tags=req.tags,
            uploaded_by=current_user.get("user_id", "system"),
            auto_publish=req.auto_publish,
        )
        await _log_knowledge_op(current_user, "knowledge.ingest_text",
                                getattr(record, "record_id", ""),
                                {"filename": req.filename, "category": req.category})
        return {"message": "文档摄入成功", "record": record.to_dict()}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/ingest/file", summary="上传文件")
async def ingest_file(
    file: UploadFile = File(...),
    category: str = Query("uncategorized"),
    department: str = Query("general"),
    permission_level: str = Query("internal"),
    tags: Optional[str] = Query(None),
    auto_publish: bool = Query(True),
    current_user: dict = Depends(require_permission("knowledge", "create")),
):
    """上传文件到知识库（支持 PDF/Word/Excel/PPT/TXT/MD 等格式）"""
    content = await file.read()
    tag_list = tags.split(",") if tags else None

    try:
        record = knowledge_manager.upload_document(
            content=content,
            filename=file.filename or "upload",
            category=category,
            department=department,
            permission_level=permission_level,
            tags=tag_list,
            uploaded_by=current_user.get("user_id", "system"),
            auto_publish=auto_publish,
        )
        await _log_knowledge_op(current_user, "knowledge.ingest_file",
                                getattr(record, "record_id", ""),
                                {"filename": file.filename, "category": category})
        return {"message": "文件上传成功", "record": record.to_dict()}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("knowledge.ingest_file_failed", error=str(e))
        raise HTTPException(status_code=500, detail=f"文件处理失败: {str(e)}")


# =============================================================================
# 文档管理端点
# =============================================================================

@router.get("/documents", summary="文档列表")
async def list_documents(
    status: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    department: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    current_user: dict = Depends(get_current_user),
):
    """列出文档记录"""
    records = knowledge_manager.list_records(
        status=status,
        category=category,
        department=department,
        limit=limit,
        offset=offset,
    )
    return {
        "total": len(records),
        "documents": [r.to_dict() for r in records],
    }


@router.get("/documents/{record_id}", summary="文档详情")
async def get_document(
    record_id: str,
    current_user: dict = Depends(get_current_user),
):
    """获取文档记录详情"""
    record = knowledge_manager.get_record(record_id)
    if record is None:
        raise HTTPException(status_code=404, detail="文档不存在")
    return record.to_dict()


@router.get("/documents/{record_id}/versions", summary="文档版本历史")
async def get_document_versions(
    record_id: str,
    current_user: dict = Depends(get_current_user),
):
    """获取文档的版本历史"""
    record = knowledge_manager.get_record(record_id)
    if record is None:
        raise HTTPException(status_code=404, detail="文档不存在")

    versions = knowledge_manager.get_version_history(record.doc_id)
    return {
        "doc_id": record.doc_id,
        "versions": [v.to_dict() for v in versions],
    }


@router.delete("/documents/{record_id}", summary="删除文档")
async def delete_document(
    record_id: str,
    current_user: dict = Depends(require_permission("knowledge", "delete")),
):
    """删除文档（从向量库中移除）"""
    success = await knowledge_manager.delete_document(record_id)
    if not success:
        raise HTTPException(status_code=404, detail="文档不存在")
    await _log_knowledge_op(current_user, "knowledge.delete_document", record_id, {})
    return {"message": "文档已删除"}


# =============================================================================
# 审批管理端点
# =============================================================================

@router.post("/review/submit", summary="提交审批")
async def submit_for_review(
    req: SubmitReviewRequest,
    current_user: dict = Depends(get_current_user),
):
    """提交文档审批"""
    try:
        approval = knowledge_manager.submit_for_review(req.record_id)
        return {"message": "已提交审批", "approval": approval}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/review/pending", summary="待审批列表")
async def list_pending_reviews(
    department: Optional[str] = Query(None),
    current_user: dict = Depends(require_permission("knowledge", "approve")),
):
    """列出待审批文档"""
    pending = knowledge_manager.list_pending_reviews(department=department)
    return {"total": len(pending), "pending": pending}


@router.post("/review/{record_id}/approve", summary="审批通过")
async def approve_document(
    record_id: str,
    req: ApproveRequest,
    current_user: dict = Depends(require_permission("knowledge", "approve")),
):
    """审批通过并发布文档"""
    try:
        record = await knowledge_manager.approve_and_publish(
            record_id=record_id,
            approved_by=current_user.get("user_id", "system"),
            comment=req.comment,
        )
        await _log_knowledge_op(current_user, "knowledge.review.approve", record_id,
                                {"comment": req.comment})
        return {"message": "文档已审批通过并发布", "record": record.to_dict()}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/review/{record_id}/reject", summary="审批拒绝")
async def reject_document(
    record_id: str,
    req: RejectRequest,
    current_user: dict = Depends(require_permission("knowledge", "approve")),
):
    """拒绝文档审批"""
    try:
        record = knowledge_manager.reject_document(
            record_id=record_id,
            rejected_by=current_user.get("user_id", "system"),
            comment=req.comment,
        )
        await _log_knowledge_op(current_user, "knowledge.review.reject", record_id,
                                {"comment": req.comment})
        return {"message": "文档已拒绝", "record": record.to_dict()}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# =============================================================================
# 分类管理端点
# =============================================================================

@router.get("/categories", summary="分类列表")
async def list_categories(
    current_user: dict = Depends(get_current_user),
):
    """列出所有文档分类"""
    cats = knowledge_manager.list_categories()
    return {"total": len(cats), "categories": [c.to_dict() for c in cats]}


@router.post("/categories", summary="创建分类")
async def create_category(
    req: CreateCategoryRequest,
    current_user: dict = Depends(require_permission("knowledge", "manage")),
):
    """创建新文档分类"""
    try:
        cat = knowledge_manager.create_category(
            name=req.name,
            description=req.description,
            department=req.department,
        )
        return {"message": "分类创建成功", "category": cat.to_dict()}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/categories/{name}", summary="删除分类")
async def delete_category(
    name: str,
    current_user: dict = Depends(require_permission("knowledge", "manage")),
):
    """删除文档分类（仅允许删除空分类）"""
    try:
        success = knowledge_manager.delete_category(name)
        if not success:
            raise HTTPException(status_code=404, detail="分类不存在")
        return {"message": "分类已删除"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# =============================================================================
# 知识图谱端点
# =============================================================================

@router.post("/graph/query", summary="知识图谱查询")
async def query_graph(
    req: GraphQueryRequest,
    current_user: dict = Depends(get_current_user),
):
    """知识图谱查询

    支持语法：
    - node_type:product  按类型查询
    - node_type:product[category=电子元件]  类型+属性过滤
    - [name=某仓库]  仅属性过滤
    """
    results = knowledge_graph.query(req.query)
    return {"query": req.query, "total": len(results), "nodes": results}


@router.get("/graph/stats", summary="知识图谱统计")
async def graph_stats(
    current_user: dict = Depends(get_current_user),
):
    """获取知识图谱统计信息"""
    return knowledge_graph.get_stats()


@router.get("/graph/neighbors/{node_id}", summary="获取节点邻居")
async def get_neighbors(
    node_id: str,
    edge_type: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """获取知识图谱节点的邻居"""
    neighbors = knowledge_graph.get_neighbors(node_id, edge_type)
    return {
        "node_id": node_id,
        "total": len(neighbors),
        "neighbors": [
            {"edge": e.to_dict(), "node": n.to_dict()}
            for e, n in neighbors
        ],
    }


# =============================================================================
# 业务规则端点
# =============================================================================

@router.post("/rules/evaluate", summary="规则评估")
async def evaluate_rules(
    req: RuleEvalRequest,
    current_user: dict = Depends(get_current_user),
):
    """评估业务规则，返回匹配的规则列表"""
    matched = await rule_engine.evaluate(req.context)
    return {
        "context": req.context,
        "matched_count": len(matched),
        "rules": [
            {
                "rule_id": r.rule_id,
                "name": r.name,
                "condition": r.condition,
                "action": r.action,
                "priority": r.priority,
                "description": r.description,
            }
            for r in matched
        ],
    }


@router.post("/rules/execute", summary="规则执行")
async def execute_rules(
    req: RuleEvalRequest,
    current_user: dict = Depends(get_current_user),
):
    """执行匹配规则的动作"""
    results = await rule_engine.execute(req.context)
    return {
        "context": req.context,
        "triggered_count": len(results),
        "results": results,
    }


@router.get("/rules", summary="规则列表")
async def list_rules(
    only_enabled: bool = Query(False),
    current_user: dict = Depends(get_current_user),
):
    """列出所有业务规则"""
    rules = rule_engine.list_rules(only_enabled=only_enabled)
    return {
        "total": len(rules),
        "rules": [r.to_dict() for r in rules],
    }


# =============================================================================
# 统计端点
# =============================================================================

@router.get("/stats", summary="知识库统计")
async def get_stats(
    current_user: dict = Depends(get_current_user),
):
    """获取知识库综合统计信息"""
    manager_stats = knowledge_manager.get_stats()
    hybrid_stats = hybrid_engine.get_stats()
    graph_stats = knowledge_graph.get_stats()
    rule_stats = rule_engine.get_stats()

    return {
        "knowledge_manager": manager_stats,
        "hybrid_retrieval": hybrid_stats,
        "knowledge_graph": graph_stats,
        "rule_engine": rule_stats,
    }
