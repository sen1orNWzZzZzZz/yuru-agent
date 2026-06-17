"""本地 Embedding 模型封装.

使用 sentence-transformers 加载轻量级中文模型，首次使用时会自动下载。
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)


class LocalEmbedder:
    """基于 sentence-transformers 的本地 Embedding 服务."""

    _instance: "LocalEmbedder | None" = None
    _model: Any | None = None

    def __new__(cls, model_name: str = "BAAI/bge-small-zh-v1.5") -> "LocalEmbedder":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._model_name = model_name
        return cls._instance

    @property
    def model(self) -> Any:
        """延迟加载模型，避免启动时阻塞."""
        if self._model is None:
            logger.info(f"[Embedder] 正在加载本地模型: {self._model_name}")
            # 延迟导入，避免在项目启动时就需要 heavy dependency
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(self._model_name)
            logger.info("[Embedder] 模型加载完成")
        return self._model

    def embed(self, texts: list[str]) -> list[list[float]]:
        """将文本列表编码为向量."""
        if not texts:
            return []
        embeddings = self.model.encode(texts, normalize_embeddings=True)
        return embeddings.tolist()

    def embed_query(self, text: str) -> list[float]:
        """将单个查询文本编码为向量."""
        return self.embed([text])[0]
