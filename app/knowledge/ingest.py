"""Markdown 文档加载与向量化入库."""

import logging
from pathlib import Path

import yaml

from app.knowledge.vector_store import KnowledgeVectorStore

logger = logging.getLogger(__name__)

DEFAULT_DOC_DIR = Path(__file__).parent / "documents"


def _parse_frontmatter(content: str) -> tuple[dict, str]:
    """解析 Markdown 文件的 YAML frontmatter."""
    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            try:
                metadata = yaml.safe_load(parts[1]) or {}
            except yaml.YAMLError:
                metadata = {}
            body = parts[2].strip()
            return metadata, body
    return {}, content.strip()


def load_markdown_documents(doc_dir: str | None = None) -> list[dict]:
    """
    加载指定目录下的所有 Markdown 文档.

    Returns:
        [
            {
                "id": 文件名（不含扩展名）,
                "text": 正文内容,
                "metadata": {category, destination, season, audience, tags, source}
            },
            ...
        ]
    """
    doc_dir = Path(doc_dir or DEFAULT_DOC_DIR)
    if not doc_dir.exists():
        logger.warning(f"[Knowledge] 文档目录不存在: {doc_dir}")
        return []

    docs = []
    for path in sorted(doc_dir.glob("*.md")):
        content = path.read_text(encoding="utf-8")
        metadata, body = _parse_frontmatter(content)
        metadata["source"] = path.name

        # 把 frontmatter 中的 tags 等字段扁平化，便于 ChromaDB 过滤
        if isinstance(metadata.get("tags"), list):
            metadata["tags"] = ",".join(metadata["tags"])

        docs.append(
            {
                "id": path.stem,
                "text": body,
                "metadata": metadata,
            }
        )

    logger.info(f"[Knowledge] 加载了 {len(docs)} 篇 Markdown 文档")
    return docs


def ingest_documents(doc_dir: str | None = None, force: bool = False) -> int:
    """
    将 Markdown 文档导入向量库.

    Args:
        doc_dir: 文档目录
        force: 是否强制重新导入
    """
    store = KnowledgeVectorStore()

    if force and store.count() > 0:
        store.clear()
        logger.info("[Knowledge] 已清空旧向量库")

    docs = load_markdown_documents(doc_dir)
    if not docs:
        return 0

    # 避免重复导入：如果文档数量相同且非 force，则跳过
    if not force and store.count() >= len(docs):
        logger.info("[Knowledge] 向量库已有数据，跳过导入")
        return store.count()

    store.add_documents(
        ids=[d["id"] for d in docs],
        texts=[d["text"] for d in docs],
        metadatas=[d["metadata"] for d in docs],
    )
    return store.count()


def ensure_ingested() -> None:
    """启动时确保知识库已导入."""
    count = ingest_documents()
    if count:
        logger.info(f"[Knowledge] 知识库就绪，共 {count} 篇文档")
