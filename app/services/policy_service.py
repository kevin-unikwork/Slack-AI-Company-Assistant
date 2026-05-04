from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path

from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.document_loaders import PyPDFLoader, TextLoader
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.vectorstore import get_vectorstore, POLICY_COLLECTION_NAME
from app.db.models.policy import PolicyDocument
from app.utils.exceptions import DocumentNotFoundError, PolicyAgentError
from app.utils.logger import get_logger

logger = get_logger(__name__)

UPLOAD_DIR = Path("./uploads")
UPLOAD_DIR.mkdir(exist_ok=True)

_splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=200)


class PolicyService:
    """Handles policy document ingestion and pgvector indexing."""

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
        Save file to disk, load, chunk, embed, store in pgvector,
        then persist metadata in PostgreSQL.
        """
        safe_name = f"{uuid.uuid4().hex}_{original_filename}"
        temp_path = UPLOAD_DIR / safe_name
        temp_path.write_bytes(file_bytes)

        try:
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

            ts_str = datetime.now(timezone.utc).isoformat()
            for chunk in chunks:
                chunk.metadata.update(
                    {
                        "source": original_filename,
                        "uploaded_at": ts_str,
                        "doc_type": file_type,
                    }
                )

            vectorstore = get_vectorstore()
            vectorstore.add_documents(chunks)

            logger.info(
                "Policy document ingested into pgvector",
                extra={
                    "filename": original_filename,
                    "chunks": len(chunks),
                    "uploaded_by": uploaded_by_slack_id,
                },
            )

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
            logger.exception("Document ingestion failed", extra={"filename": original_filename})
            raise PolicyAgentError(f"Ingestion failed: {exc}") from exc
        finally:
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
        """Soft-delete: mark is_active=False and remove chunks from pgvector."""
        result = await session.execute(select(PolicyDocument).where(PolicyDocument.id == doc_id))
        doc = result.scalar_one_or_none()
        if not doc:
            raise DocumentNotFoundError(f"Policy document {doc_id} not found")

        try:
            vectorstore = get_vectorstore()
            # Delete all embeddings that match this source filename
            vectorstore.delete(filter={"source": doc.original_filename})
            logger.info(
                "pgvector chunks deleted",
                extra={"doc_id": doc_id, "filename": doc.original_filename},
            )
        except Exception as exc:
            logger.exception("Failed to remove chunks from pgvector", extra={"doc_id": doc_id})
            raise PolicyAgentError(f"pgvector deletion failed: {exc}") from exc

        doc.is_active = False
        await session.flush()

    def get_retriever(self):
        """Return a LangChain retriever (k=4 cosine nearest neighbours)."""
        vectorstore = get_vectorstore()
        return vectorstore.as_retriever(search_kwargs={"k": 4})


policy_service = PolicyService()
