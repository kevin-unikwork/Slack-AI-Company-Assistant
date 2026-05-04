"""
Singleton PGVector vector store backed by the Aiven PostgreSQL instance.
Replaces the old ChromaDB file-based store so embeddings survive Railway redeploys.
"""
from __future__ import annotations

import re
from typing import Any

from langchain_openai import OpenAIEmbeddings
from langchain_postgres import PGVector

from app.config import settings
from app.utils.logger import get_logger

logger = get_logger(__name__)

_vectorstore: PGVector | None = None
_embeddings: OpenAIEmbeddings | None = None

POLICY_COLLECTION_NAME = "company_policies"


def _get_sync_connection_string() -> str:
    """
    Derive a synchronous psycopg connection string from the async DATABASE_URL.
    langchain-postgres PGVector requires a synchronous driver (psycopg, not asyncpg).
    """
    url = settings.database_url
    # Handle all possible prefixes
    url = re.sub(r"^postgresql\+asyncpg://", "postgresql+psycopg://", url)
    url = re.sub(r"^postgres://", "postgresql+psycopg://", url)
    return url


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


def get_vectorstore() -> PGVector:
    """Return (or lazily create) the singleton PGVector store."""
    global _vectorstore
    if _vectorstore is None:
        conn_str = _get_sync_connection_string()
        _vectorstore = PGVector(
            embeddings=get_embeddings(),
            collection_name=POLICY_COLLECTION_NAME,
            connection=conn_str,
            use_jsonb=True,
        )
        logger.info(
            "PGVector store initialised",
            extra={"collection": POLICY_COLLECTION_NAME},
        )
    return _vectorstore
