import asyncio
import sys
import os

# Add project root to sys.path
sys.path.append(os.getcwd())

from app.db.session import AsyncSessionLocal
from app.services.policy_service import policy_service
from app.agents import policy_agent

async def upload_sample_policy(file_path: str):
    """
    Ingests a document into the Policy RAG system (ChromaDB + Postgres).
    """
    if not os.path.exists(file_path):
        print(f"Error: File {file_path} not found.")
        return

    filename = os.path.basename(file_path)
    ext = filename.rsplit(".", 1)[-1].lower()
    
    if ext not in ("pdf", "txt"):
        print("Error: Only .pdf and .txt files are supported for policy ingestion.")
        return

    with open(file_path, "rb") as f:
        file_bytes = f.read()

    print(f"Ingesting {filename}...")
    async with AsyncSessionLocal() as session:
        async with session.begin():
            doc = await policy_service.ingest_document(
                session=session,
                file_bytes=file_bytes,
                original_filename=filename,
                file_type=ext,
                uploaded_by_slack_id="SYSTEM",
                description="Sample policy for testing"
            )
            
    # Reset the LLM chain to recognize the new document
    policy_agent.reset_chain()
    print(f"Success: Document '{filename}' ingested with ID {doc.id}.")
    print("You can now ask the Slack bot questions about this policy.")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/upload_policy.py <path_to_file>")
        sys.exit(1)
        
    path = sys.argv[1]
    asyncio.run(upload_sample_policy(path))
