import os
import asyncio
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from .env
load_dotenv()

from app.services.policy_service import policy_service
from app.db.session import init_db, AsyncSessionLocal

async def main():
    print("Starting fresh manual ingestion of 'all_policy.pdf'...")
    
    file_path = Path("all_policy.pdf")
    if not file_path.exists():
        print("Error: all_policy.pdf not found in the current directory.")
        return

    # Initialize DB (creates extension if needed)
    try:
        await init_db()
        print("Database initialized.")
    except Exception as e:
        print(f"Warning during DB init: {e}")

    # Read file bytes
    file_bytes = file_path.read_bytes()

    # Ingest the file using the standard service method
    print("Reading and embedding all_policy.pdf...")
    try:
        async with AsyncSessionLocal() as session:
            async with session.begin():
                from sqlalchemy import select
                from app.db.models.policy import PolicyDocument
                
                res = await session.execute(select(PolicyDocument).where(PolicyDocument.original_filename == "all_policy.pdf"))
                existing_docs = res.scalars().all()
                
                for doc_record in existing_docs:
                    print(f"Removing old version (ID: {doc_record.id}) to apply new chunking settings...")
                    await policy_service.delete_document(session, doc_record.id)
                
                # The service method handles everything: DB entry + Vector Store
                doc = await policy_service.ingest_document(
                    session=session,
                    file_bytes=file_bytes,
                    original_filename="all_policy.pdf",
                    file_type="pdf",
                    uploaded_by_slack_id="manual_ingest"
                )
                print(f"Successfully ingested 'all_policy.pdf' with NEW settings! Doc ID: {doc.id}")
        
    except Exception as e:
        print(f"Ingestion failed: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(main())
