"""
文档摄入管道 (Document Ingestion Pipeline)
==========================================

设计目标
--------
- 支持多格式文档解析：PDF / Word / Excel / PPT / 纯文本 / Markdown
- 文档分块：滑动窗口 + 重叠，保留语义完整性
- 零依赖启动：未安装解析库时降级到纯文本提取
- 元数据规范化：统一 source / category / tags / permission_level / department 字段

分块策略
--------
1. 按段落预切分（双换行符）
2. 段落超过 chunk_size 时按句子切分
3. 滑动窗口组装 chunk，保留 overlap 个 token 的重叠
4. 每个 chunk 携带 parent_doc_id 和 chunk_index 元数据

格式支持
--------
- .txt / .md:    原生支持，无外部依赖
- .pdf:          优先使用 PyPDF2 / pdfplumber，降级到纯文本
- .docx:         优先使用 python-docx，降级提示
- .xlsx:         优先使用 openpyxl，降级提示
- .pptx:         优先使用 python-pptx，降级提示
- 其他:          按 UTF-8 文本处理
"""
import os
import re
import structlog
from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

from app.core.knowledge.rag import Document, vector_store

logger = structlog.get_logger()


# =============================================================================
# 常量与配置
# =============================================================================

DEFAULT_CHUNK_SIZE = 500      # 每个 chunk 的目标字符数
DEFAULT_CHUNK_OVERLAP = 100   # chunk 之间的重叠字符数
MIN_CHUNK_SIZE = 50           # 最小 chunk 字符数
MAX_CHUNK_SIZE = 2000         # 最大 chunk 字符数

# 支持的文件格式
SUPPORTED_FORMATS = {
    ".txt": "text",
    ".md": "markdown",
    ".pdf": "pdf",
    ".docx": "word",
    ".doc": "word_legacy",
    ".xlsx": "excel",
    ".xls": "excel_legacy",
    ".pptx": "ppt",
    ".ppt": "ppt_legacy",
    ".csv": "csv",
    ".json": "json",
    ".html": "html",
    ".htm": "html",
}

# 权限级别层级
PERMISSION_LEVELS = {
    "public": 0,      # 公开
    "internal": 1,    # 内部
    "confidential": 2, # 机密
    "restricted": 3,   # 限制级
    "top_secret": 4,   # 绝密
}


# =============================================================================
# 格式解析器
# =============================================================================

def _extract_text_plain(content: bytes, encoding: str = "utf-8") -> str:
    """纯文本提取"""
    try:
        return content.decode(encoding)
    except (UnicodeDecodeError, LookupError):
        # 尝试常见编码
        for enc in ("utf-8", "gbk", "gb2312", "latin-1"):
            try:
                return content.decode(enc)
            except (UnicodeDecodeError, LookupError):
                continue
        return content.decode("utf-8", errors="replace")


def _extract_text_markdown(content: bytes, encoding: str = "utf-8") -> str:
    """Markdown 提取（保留文本，移除图片/链接标记）"""
    text = _extract_text_plain(content, encoding)
    # 移除图片语法 ![alt](url)
    text = re.sub(r"!\[([^\]]*)\]\([^\)]+\)", r"\1", text)
    # 移除链接语法 [text](url) → text
    text = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", text)
    # 移除 HTML 标签
    text = re.sub(r"<[^>]+>", "", text)
    return text


def _extract_text_pdf(content: bytes) -> str:
    """PDF 文本提取

    优先使用 pdfplumber（表格/布局感知），降级到 PyPDF2，最终降级到空字符串。
    """
    # 尝试 pdfplumber
    try:
        import io
        import pdfplumber  # type: ignore
        text_parts: list[str] = []
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text() or ""
                text_parts.append(page_text)
        return "\n\n".join(text_parts)
    except ImportError:
        pass
    except Exception as e:
        logger.warning("ingestion.pdfplumber_failed", error=str(e))

    # 尝试 PyPDF2
    try:
        import io
        from PyPDF2 import PdfReader  # type: ignore
        reader = PdfReader(io.BytesIO(content))
        text_parts = []
        for page in reader.pages:
            page_text = page.extract_text() or ""
            text_parts.append(page_text)
        return "\n\n".join(text_parts)
    except ImportError:
        pass
    except Exception as e:
        logger.warning("ingestion.pypdf2_failed", error=str(e))

    logger.warning("ingestion.pdf_no_library", message="Install pdfplumber or PyPDF2 for PDF support")
    return ""


def _extract_text_docx(content: bytes) -> str:
    """Word (.docx) 文本提取"""
    try:
        import io
        from docx import Document as DocxDocument  # type: ignore
        doc = DocxDocument(io.BytesIO(content))
        paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
        # 提取表格内容
        for table in doc.tables:
            for row in table.rows:
                cells = [cell.text.strip() for cell in row.cells]
                paragraphs.append(" | ".join(cells))
        return "\n\n".join(paragraphs)
    except ImportError:
        logger.warning("ingestion.docx_no_library", message="Install python-docx for Word support")
        return ""
    except Exception as e:
        logger.warning("ingestion.docx_failed", error=str(e))
        return ""


def _extract_text_xlsx(content: bytes) -> str:
    """Excel (.xlsx) 文本提取"""
    try:
        import io
        from openpyxl import load_workbook  # type: ignore
        wb = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        text_parts: list[str] = []
        for sheet_name in wb.sheetnames:
            ws = wb[sheet_name]
            text_parts.append(f"[工作表: {sheet_name}]")
            for row in ws.iter_rows(values_only=True):
                cells = [str(c) for c in row if c is not None]
                if cells:
                    text_parts.append(" | ".join(cells))
        wb.close()
        return "\n".join(text_parts)
    except ImportError:
        logger.warning("ingestion.xlsx_no_library", message="Install openpyxl for Excel support")
        return ""
    except Exception as e:
        logger.warning("ingestion.xlsx_failed", error=str(e))
        return ""


def _extract_text_pptx(content: bytes) -> str:
    """PowerPoint (.pptx) 文本提取"""
    try:
        import io
        from pptx import Presentation  # type: ignore
        prs = Presentation(io.BytesIO(content))
        text_parts: list[str] = []
        for i, slide in enumerate(prs.slides, 1):
            slide_texts: list[str] = []
            for shape in slide.shapes:
                if shape.has_text_frame:
                    for para in shape.text_frame.paragraphs:
                        text = para.text.strip()
                        if text:
                            slide_texts.append(text)
                if shape.has_table:
                    for row in shape.table.rows:
                        cells = [cell.text.strip() for cell in row.cells]
                        slide_texts.append(" | ".join(cells))
            if slide_texts:
                text_parts.append(f"[幻灯片 {i}]\n" + "\n".join(slide_texts))
        return "\n\n".join(text_parts)
    except ImportError:
        logger.warning("ingestion.pptx_no_library", message="Install python-pptx for PPT support")
        return ""
    except Exception as e:
        logger.warning("ingestion.pptx_failed", error=str(e))
        return ""


def _extract_text_html(content: bytes, encoding: str = "utf-8") -> str:
    """HTML 文本提取（移除标签）"""
    text = _extract_text_plain(content, encoding)
    # 移除 script 和 style 块
    text = re.sub(r"<script[^>]*>.*?</script>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
    # 移除所有 HTML 标签
    text = re.sub(r"<[^>]+>", " ", text)
    # 压缩空白
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _extract_text_csv(content: bytes, encoding: str = "utf-8") -> str:
    """CSV 文本提取（按行保留，添加表头提示）"""
    text = _extract_text_plain(content, encoding)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if lines:
        return f"[CSV数据]\n" + "\n".join(lines)
    return ""


def _extract_text_json(content: bytes, encoding: str = "utf-8") -> str:
    """JSON 文本提取（扁平化展示）"""
    import json
    text = _extract_text_plain(content, encoding)
    try:
        data = json.loads(text)
        return json.dumps(data, ensure_ascii=False, indent=2)
    except json.JSONDecodeError:
        return text


# =============================================================================
# 格式分发器
# =============================================================================

def extract_text(
    content: bytes,
    filename: str = "",
    file_format: Optional[str] = None,
) -> tuple[str, str]:
    """根据文件格式提取纯文本

    Args:
        content: 文件二进制内容
        filename: 原始文件名（用于推断格式）
        file_format: 显式指定的格式（优先于文件名推断）

    Returns:
        (extracted_text, format_name) 元组
    """
    # 确定格式
    ext = ""
    if filename:
        _, ext = os.path.splitext(filename.lower())
    fmt = file_format or SUPPORTED_FORMATS.get(ext, "text")

    # 分发到对应的解析器
    extractors = {
        "text": lambda: _extract_text_plain(content),
        "markdown": lambda: _extract_text_markdown(content),
        "pdf": lambda: _extract_text_pdf(content),
        "word": lambda: _extract_text_docx(content),
        "word_legacy": lambda: _extract_text_plain(content),  # .doc 降级
        "excel": lambda: _extract_text_xlsx(content),
        "excel_legacy": lambda: _extract_text_plain(content),  # .xls 降级
        "ppt": lambda: _extract_text_pptx(content),
        "ppt_legacy": lambda: _extract_text_plain(content),  # .ppt 降级
        "csv": lambda: _extract_text_csv(content),
        "json": lambda: _extract_text_json(content),
        "html": lambda: _extract_text_html(content),
    }

    extractor = extractors.get(fmt, extractors["text"])
    text = extractor()

    if not text:
        logger.warning("ingestion.empty_extraction", filename=filename, format=fmt)

    return text, fmt


# =============================================================================
# 文档分块器
# =============================================================================

async def _vectorize_and_persist(parent_doc_id: str, documents: list) -> None:
    """向量化（EmbeddingProvider）+ knowledge_chunks 落库 + 语义索引注入。

    失败降级：仅 BM25/TF-IDF 通道可用（语义通道跳过），不影响检索可用性。
    """
    try:
        from app.core.knowledge.embeddings import get_embedding_provider
        from app.core.knowledge.hybrid import hybrid_engine
        from app.core.knowledge.persistence import save_chunks

        # 1) 内存检索索引立即重建（TF-IDF 向量库 + BM25，先于向量化——
        #    保证语义通道失败时 BM25/TF-IDF 仍可用）
        await vector_store.add_documents(documents)
        hybrid_engine.sync_bm25_index()

        # 2) 向量化（失败降级：仅落库无向量，语义通道跳过）
        provider = get_embedding_provider()
        embeddings = None
        if provider.name != "none":
            embeddings = await provider.embed([d.content for d in documents])
            for d, v in zip(documents, embeddings):
                d.embedding = v
                hybrid_engine.semantic_vectors[d.doc_id] = v
        model_name = provider.name if embeddings else ""
        await save_chunks(parent_doc_id, documents, embeddings,
                          embedding_model=model_name)
    except Exception as e:  # noqa: BLE001
        logger.warning("ingestion.vectorize_failed", parent=parent_doc_id, error=str(e))
        try:
            from app.core.knowledge.persistence import save_chunks

            await save_chunks(parent_doc_id, documents)  # 无向量落库
        except Exception:  # noqa: BLE001
            pass


def _schedule_vectorize_and_persist(parent_doc_id: str, documents: list) -> None:
    """在可用事件循环上调度流水线（无循环时同步执行——测试/脚本场景）。"""
    import asyncio as _asyncio

    coro = _vectorize_and_persist(parent_doc_id, documents)
    try:
        loop = _asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is not None:
        _asyncio.ensure_future(coro, loop=loop)
    else:
        _asyncio.run(coro)


def chunk_text(
    text: str,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> list[str]:
    """将长文本切分为重叠的块

    分块策略：
    1. 按段落（双换行）预切分
    2. 超长段落按句子边界切分
    3. 滑动窗口组装最终 chunk，保留 overlap 重叠

    Args:
        text: 原始文本
        chunk_size: 每个 chunk 的目标字符数
        chunk_overlap: chunk 之间的重叠字符数

    Returns:
        文本块列表
    """
    if not text or not text.strip():
        return []

    # 参数约束
    chunk_size = max(MIN_CHUNK_SIZE, min(MAX_CHUNK_SIZE, chunk_size))
    chunk_overlap = max(0, min(chunk_overlap, chunk_size // 2))

    # Step 1: 按段落预切分
    paragraphs = re.split(r"\n\s*\n", text)
    paragraphs = [p.strip() for p in paragraphs if p.strip()]

    # Step 2: 超长段落按句子切分
    sentences: list[str] = []
    # 中文句号、英文句号、问号、感叹号、分号
    sentence_pattern = re.compile(r"[。！？.!?；;]\s*")

    for para in paragraphs:
        if len(para) <= chunk_size:
            sentences.append(para)
        else:
            # 按句子切分
            parts = sentence_pattern.split(para)
            current = ""
            for part in parts:
                if not part:
                    continue
                if len(current) + len(part) + 1 <= chunk_size:
                    current = (current + " " + part).strip() if current else part
                else:
                    if current:
                        sentences.append(current)
                    current = part
            if current:
                sentences.append(current)

    # Step 3: 滑动窗口组装 chunk
    chunks: list[str] = []
    current_chunk = ""
    for sentence in sentences:
        # 如果当前 chunk 为空，直接添加
        if not current_chunk:
            current_chunk = sentence
        # 如果添加后不超过 chunk_size，则添加
        elif len(current_chunk) + len(sentence) + 1 <= chunk_size:
            current_chunk = current_chunk + "\n" + sentence
        else:
            # 当前 chunk 已满，保存
            chunks.append(current_chunk)
            # 开始新 chunk，携带 overlap
            if chunk_overlap > 0 and len(current_chunk) > chunk_overlap:
                overlap_text = current_chunk[-chunk_overlap:]
                current_chunk = overlap_text + "\n" + sentence
            else:
                current_chunk = sentence

    # 最后一个 chunk
    if current_chunk:
        chunks.append(current_chunk)

    return chunks


# =============================================================================
# 文档摄入器
# =============================================================================

class DocumentIngester:
    """文档摄入器

    将原始文件内容转化为结构化 Document 对象列表，
    完成文本提取、分块、元数据规范化。

    使用方式：
        ingester = DocumentIngester()
        docs = ingester.ingest(
            content=file_bytes,
            filename="manual.pdf",
            metadata={"category": "operation", "department": "warehouse"},
        )
        await vector_store.add_documents(docs)
    """

    def __init__(
        self,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
    ):
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

    def ingest(
        self,
        content: bytes,
        filename: str = "",
        file_format: Optional[str] = None,
        metadata: Optional[dict] = None,
        permission_level: str = "internal",
        department: Optional[str] = None,
        category: Optional[str] = None,
        tags: Optional[list[str]] = None,
        uploaded_by: Optional[str] = None,
    ) -> list[Document]:
        """摄入一个文档，返回分块后的 Document 列表

        Args:
            content: 文件二进制内容
            filename: 原始文件名
            file_format: 显式格式
            metadata: 额外元数据
            permission_level: 权限级别
            department: 归属部门
            category: 分类
            tags: 标签列表
            uploaded_by: 上传者 ID

        Returns:
            分块后的 Document 列表（至少 1 个元素；提取失败时返回空列表）
        """
        # 1. 提取文本
        text, fmt = extract_text(content, filename, file_format)
        if not text.strip():
            logger.warning(
                "ingestion.no_text_extracted",
                filename=filename,
                format=fmt,
            )
            return []

        # 2. 分块
        chunks = chunk_text(text, self.chunk_size, self.chunk_overlap)
        if not chunks:
            chunks = [text[:self.chunk_size]]

        # 3. 生成 parent_doc_id
        parent_doc_id = f"doc_{uuid4().hex[:16]}"

        # 4. 规范化元数据
        base_metadata = {
            "parent_doc_id": parent_doc_id,
            "filename": filename,
            "file_format": fmt,
            "permission_level": permission_level,
            "permission_rank": PERMISSION_LEVELS.get(permission_level, 1),
            "department": department or "general",
            "category": category or "uncategorized",
            "tags": tags or [],
            "uploaded_by": uploaded_by or "system",
            "total_chunks": len(chunks),
            "ingested_at": datetime.now(timezone.utc).isoformat(),
            "char_count": len(text),
        }
        # 合并额外元数据
        if metadata:
            for key, val in metadata.items():
                if key not in base_metadata:
                    base_metadata[key] = val

        # 5. 创建 Document 对象
        documents: list[Document] = []
        for i, chunk in enumerate(chunks):
            doc = Document(
                content=chunk,
                metadata={
                    **base_metadata,
                    "chunk_index": i,
                    "chunk_total": len(chunks),
                },
            )
            documents.append(doc)

        logger.info(
            "ingestion.document_ingested",
            parent_doc_id=parent_doc_id,
            filename=filename,
            format=fmt,
            chunks=len(documents),
            char_count=len(text),
            permission_level=permission_level,
        )

        _schedule_vectorize_and_persist(parent_doc_id, documents)

        return documents

    def ingest_text(
        self,
        text: str,
        metadata: Optional[dict] = None,
        permission_level: str = "internal",
        department: Optional[str] = None,
        category: Optional[str] = None,
        tags: Optional[list[str]] = None,
        uploaded_by: Optional[str] = None,
    ) -> list[Document]:
        """直接摄入纯文本（跳过格式解析）

        Args:
            text: 纯文本内容
            其余参数同 ingest()

        Returns:
            分块后的 Document 列表
        """
        if not text or not text.strip():
            return []

        chunks = chunk_text(text, self.chunk_size, self.chunk_overlap)
        if not chunks:
            chunks = [text[:self.chunk_size]]

        parent_doc_id = f"doc_{uuid4().hex[:16]}"

        base_metadata = {
            "parent_doc_id": parent_doc_id,
            "filename": "",
            "file_format": "text",
            "permission_level": permission_level,
            "permission_rank": PERMISSION_LEVELS.get(permission_level, 1),
            "department": department or "general",
            "category": category or "uncategorized",
            "tags": tags or [],
            "uploaded_by": uploaded_by or "system",
            "total_chunks": len(chunks),
            "ingested_at": datetime.now(timezone.utc).isoformat(),
            "char_count": len(text),
        }
        if metadata:
            for key, val in metadata.items():
                if key not in base_metadata:
                    base_metadata[key] = val

        documents = []
        for i, chunk in enumerate(chunks):
            doc = Document(
                content=chunk,
                metadata={
                    **base_metadata,
                    "chunk_index": i,
                    "chunk_total": len(chunks),
                },
            )
            documents.append(doc)

        logger.info(
            "ingestion.text_ingested",
            parent_doc_id=parent_doc_id,
            chunks=len(documents),
            char_count=len(text),
        )
        _schedule_vectorize_and_persist(parent_doc_id, documents)
        return documents


# 全局单例
document_ingester = DocumentIngester()
