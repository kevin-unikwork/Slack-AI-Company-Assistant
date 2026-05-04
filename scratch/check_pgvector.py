import asyncio
from app.db.session import AsyncSessionLocal
from sqlalchemy import text

async def check():
    async with AsyncSessionLocal() as session:
        # Check if pgvector extension exists
        result = await session.execute(text("SELECT * FROM pg_extension WHERE extname = 'vector'"))
        row = result.fetchone()
        if row:
            print(f"pgvector extension is INSTALLED: {row}")
        else:
            print("pgvector NOT installed. Trying to create...")
            try:
                await session.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
                await session.commit()
                print("pgvector extension created successfully!")
            except Exception as e:
                print(f"Failed to create pgvector extension: {e}")

if __name__ == "__main__":
    asyncio.run(check())
