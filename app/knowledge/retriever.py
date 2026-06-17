"""旅行知识检索器."""

import logging

from app.knowledge.vector_store import KnowledgeVectorStore

logger = logging.getLogger(__name__)


class TipsRetriever:
    """基于向量相似度的旅行 Tips 检索器."""

    def __init__(self, store: KnowledgeVectorStore | None = None):
        self.store = store or KnowledgeVectorStore()

    def retrieve(
        self,
        query: str,
        n_results: int = 5,
        destination: str | None = None,
        audience: str | None = None,
        season: str | None = None,
    ) -> dict:
        """
        检索与 query 相关的旅行知识.

        Args:
            query: 查询文本
            n_results: 返回结果数量
            destination: 按目的地过滤（可选）
            audience: 按人群过滤（可选）
            season: 按季节过滤（可选）
        """
        where = {}
        if destination:
            where["destination"] = destination
        if audience:
            where["audience"] = audience
        if season:
            where["season"] = season

        filters = where if where else None
        return self.store.query(query, n_results=n_results, where=filters)

    def get_document(self, doc_id: str) -> dict | None:
        """按 ID 获取单篇文档."""
        try:
            result = self.store.collection.get(ids=[doc_id])
            if result and result["ids"]:
                return {
                    "id": result["ids"][0],
                    "text": result["documents"][0],
                    "metadata": result["metadatas"][0],
                }
        except Exception as e:
            logger.warning(f"[Retriever] 获取文档失败: {e}")
        return None
