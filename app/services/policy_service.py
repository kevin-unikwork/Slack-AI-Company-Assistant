import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.document_loaders import PyPDFLoader, TextLoader
from langchain_postgres import PGVector
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.config import settings
from langchain_openai import OpenAIEmbeddings
from app.db.models.policy import PolicyDocument
from app.utils.logger import get_logger
from app.utils.exceptions import PolicyAgentError, DocumentNotFoundError

logger = get_logger(__name__)

UPLOAD_DIR = Path("./uploads")
UPLOAD_DIR.mkdir(exist_ok=True)

_splitter = RecursiveCharacterTextSplitter(chunk_size=1500, chunk_overlap=300)


POLICY_COLLECTION_NAME = "company_policies"
_embeddings = None
_vectorstore = None

def _get_embeddings() -> OpenAIEmbeddings:
    global _embeddings
    if _embeddings is None:
        _embeddings = OpenAIEmbeddings(
            model="text-embedding-ada-002",
            openai_api_key=settings.openai_api_key,
        )
    return _embeddings

def _get_vectorstore() -> PGVector:
    """Return a LangChain PGVector wrapper using the Aiven DB."""
    global _vectorstore
    if _vectorstore is not None:
        return _vectorstore

    # Convert asyncpg URL to standard postgresql+psycopg:// for PGVector
    sync_url = settings.database_url
    if sync_url.startswith("postgresql+asyncpg://"):
        sync_url = sync_url.replace("postgresql+asyncpg://", "postgresql+psycopg://", 1)
    elif sync_url.startswith("postgres://"):
        sync_url = sync_url.replace("postgres://", "postgresql+psycopg://", 1)
    elif sync_url.startswith("postgresql://"):
        sync_url = sync_url.replace("postgresql://", "postgresql+psycopg://", 1)

    _vectorstore = PGVector(
        embeddings=_get_embeddings(),
        collection_name=POLICY_COLLECTION_NAME,
        connection=sync_url,
        use_jsonb=True,
        async_mode=False,
    )
    return _vectorstore


class PolicyService:
    """Handles policy document ingestion and ChromaDB indexing."""

    async def ingest_document(
        self,
        session: AsyncSession,
        file_bytes: bytes,
        original_filename: str,
        file_type: str,
        uploaded_by_slack_id: str | None = None,
        description: str | None = None,
    ) -> PolicyDocument:
        """
        Save file to disk, load, chunk, embed, store in ChromaDB,
        then persist metadata in PostgreSQL.
        """
        # Save temp file
        safe_name = f"{uuid.uuid4().hex}_{original_filename}"
        temp_path = UPLOAD_DIR / safe_name
        temp_path.write_bytes(file_bytes)

        try:
            # Load document
            if file_type == "pdf":
                loader = PyPDFLoader(str(temp_path))
            elif file_type == "txt":
                loader = TextLoader(str(temp_path), encoding="utf-8")
            else:
                raise PolicyAgentError(f"Unsupported file type: {file_type}")

            raw_docs = loader.load()
            chunks = _splitter.split_documents(raw_docs)

            if not chunks:
                raise PolicyAgentError(f"No text content extracted from {original_filename}")

            # Add source metadata to every chunk
            ts_str = datetime.now(timezone.utc).isoformat()
            for chunk in chunks:
                chunk.metadata.update({
                    "source": original_filename,
                    "uploaded_at": ts_str,
                    "doc_type": file_type,
                })

            # Embed and store in PGVector
            vectorstore = _get_vectorstore()
            vectorstore.add_documents(chunks)

            logger.info(
                "Policy document ingested",
                extra={
                    "filename": original_filename,
                    "chunks": len(chunks),
                    "uploaded_by": uploaded_by_slack_id,
                },
            )

            # Persist metadata to PostgreSQL
            doc = PolicyDocument(
                filename=safe_name,
                original_filename=original_filename,
                file_type=file_type,
                chunk_count=len(chunks),
                uploaded_by_slack_id=uploaded_by_slack_id,
                description=description,
            )
            session.add(doc)
            await session.flush()
            return doc

        except PolicyAgentError:
            raise
        except Exception as exc:
            logger.exception(
                "Document ingestion failed",
                extra={"filename": original_filename},
            )
            raise PolicyAgentError(f"Ingestion failed: {exc}") from exc
        finally:
            # Always clean up temp file
            if temp_path.exists():
                temp_path.unlink()

    async def list_documents(self, session: AsyncSession) -> list[PolicyDocument]:
        result = await session.execute(
            select(PolicyDocument)
            .where(PolicyDocument.is_active == True)
            .order_by(PolicyDocument.uploaded_at.desc())
        )
        return list(result.scalars().all())

    async def delete_document(self, session: AsyncSession, doc_id: int) -> None:
        """Soft-delete: mark is_active=False and remove chunks from ChromaDB."""
        result = await session.execute(
            select(PolicyDocument).where(PolicyDocument.id == doc_id)
        )
        doc = result.scalar_one_or_none()
        if not doc:
            raise DocumentNotFoundError(f"Policy document {doc_id} not found")

        # Remove from PGVector by source metadata filter
        try:
            vectorstore = _get_vectorstore()
            # PGVector delete works by IDs or collection. 
            # In langchain-postgres, we can use delete(filter={"source": doc.original_filename})
            vectorstore.delete(filter={"source": doc.original_filename})
            logger.info(
                "PGVector chunks deleted",
                extra={"doc_id": doc_id, "filename": doc.original_filename},
            )
        except Exception as exc:
            logger.exception("Failed to remove chunks from PGVector", extra={"doc_id": doc_id})
            raise PolicyAgentError(f"PGVector deletion failed: {exc}") from exc

        doc.is_active = False
        await session.flush()

    def get_retriever(self):
        """Return a LangChain retriever (k=8 MMR for diversity)."""
        vectorstore = _get_vectorstore()
        return vectorstore.as_retriever(
            search_type="mmr",
            search_kwargs={"k": 8, "fetch_k": 20}
        )


policy_service = PolicyService()