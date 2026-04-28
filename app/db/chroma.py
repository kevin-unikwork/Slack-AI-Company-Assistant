from typing import Any

try:
    import chromadb
    from chromadb import Collection
except ImportError:  # optional dependency in some deployments
    chromadb = None
    Collection = Any  # type: ignore[misc,assignment]

from langchain_openai import OpenAIEmbeddings

from app.config import settings
from app.utils.logger import get_logger

logger = get_logger(__name__)

_chroma_client: Any | None = None
_embeddings: OpenAIEmbeddings | None = None

POLICY_COLLECTION_NAME = "company_policies"


def get_chroma_client():
    """Return (or lazily create) the singleton ChromaDB persistent client."""
    if chromadb is None:
        raise RuntimeError(
            "ChromaDB dependency is missing. Install `chromadb` (and `langchain-chroma` if needed) to use policy vector storage."
        )

    global _chroma_client
    if _chroma_client is None:
        _chroma_client = chromadb.PersistentClient(path=settings.chroma_persist_dir)
        logger.info("ChromaDB client initialised", extra={"persist_dir": settings.chroma_persist_dir})
    return _chroma_client


def get_embeddings() -> OpenAIEmbeddings:
    """Return (or lazily create) the OpenAI embeddings instance."""
    global _embeddings
    if _embeddings is None:
        _embeddings = OpenAIEmbeddings(
            model="text-embedding-ada-002",
            openai_api_key=settings.openai_api_key,
        )
        logger.info("OpenAI embeddings initialised")
    return _embeddings


def get_policy_collection() -> Collection:
    """Return the ChromaDB collection for company policies (creates if absent)."""
    client = get_chroma_client()
    collection = client.get_or_create_collection(
        name=POLICY_COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )
    logger.debug("Policy collection fetched", extra={"collection": POLICY_COLLECTION_NAME})
    return collection


def close_chroma() -> None:
    """Explicitly close/reset the client reference (called on shutdown)."""
    global _chroma_client
    _chroma_client = None
    logger.info("ChromaDB client reference cleared")
