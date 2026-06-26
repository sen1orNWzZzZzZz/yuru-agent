"""旅行知识库模块.

提供 RAG 检索和 Checklist 生成能力.
"""

import importlib.util

from app.knowledge.checklist_generator import ChecklistGenerator
from app.knowledge.ingest import ensure_ingested, ingest_documents, load_knowledge_template
from app.knowledge.retriever import TipsRetriever

# RAG 为可选能力：chromadb + sentence-transformers 未安装时也能启动项目
RAG_AVAILABLE = (
    importlib.util.find_spec("chromadb") is not None
    and importlib.util.find_spec("sentence_transformers") is not None
)

__all__ = [
    "ChecklistGenerator",
    "TipsRetriever",
    "ingest_documents",
    "ensure_ingested",
    "load_knowledge_template",
    "RAG_AVAILABLE",
]
