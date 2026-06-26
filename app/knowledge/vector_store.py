"""ChromaDB 向量存储封装."""

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

# 向量库持久化目录：可通过环境变量覆盖，便于 Docker 持久化
DEFAULT_PERSIST_DIR = Path(__file__).parent / "chroma_db"
PERSIST_DIR = Path(os.environ.get("CHROMA_PERSIST_DIR", DEFAULT_PERSIST_DIR))


class KnowledgeVectorStore:
    """基于 ChromaDB 的旅行知识向量库."""

    def __init__(
        self,
        collection_name: str = "travel_tips",
        persist_dir: str | None = None,
    ):
        # 延迟导入，避免在项目启动/测试时强制依赖 ChromaDB
        import chromadb

        from app.knowledge.embedder import LocalEmbedder

        self.persist_dir = str(persist_dir or PERSIST_DIR)
        self.client = chromadb.PersistentClient(path=self.persist_dir)
        self.collection = self.client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"},
        )
        self._embedder = LocalEmbedder()

    def add_documents(
        self,
        ids: list[str],
        texts: list[str],
        metadatas: list[dict],
    ) -> None:
        """添加文档到向量库."""
        if not texts:
            return
        embeddings = self._embedder.embed(texts)
        self.collection.add(
            ids=ids,
            documents=texts,
            metadatas=metadatas,
            embeddings=embeddings,
        )
        logger.info(f"[VectorStore] 已添加 {len(ids)} 篇文档")

    def query(
        self,
        query_text: str,
        n_results: int = 5,
        where: dict | None = None,
    ) -> dict:
        """
        语义检索.

        Returns:
            {
                "ids": [[...]],
                "documents": [[...]],
                "metadatas": [[...]],
                "distances": [[...]],
            }
        """
        embedding = self._embedder.embed_query(query_text)
        return self.collection.query(
            query_embeddings=[embedding],
            n_results=n_results,
            where=where,
            include=["documents", "metadatas", "distances"],
        )

    def count(self) -> int:
        """返回向量库中文档数量."""
        return self.collection.count()

    def clear(self) -> None:
        """清空集合."""
        self.client.delete_collection(self.collection.name)
        self.collection = self.client.get_or_create_collection(
            name=self.collection.name,
            metadata={"hnsw:space": "cosine"},
        )
