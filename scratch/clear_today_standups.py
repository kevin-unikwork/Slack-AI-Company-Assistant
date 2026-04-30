import asyncio
from sqlalchemy import delete
from app.db.session import AsyncSessionLocal
from app.db.models.standup import StandupResponse, StandupSummary
from app.agents.standup_agent import _today_range

async def run():
    print("Clearing today's standup responses and summaries...")
    async with AsyncSessionLocal() as session:
        async with session.begin():
            start, end = _today_range()
            
            # 1. Delete all Standup Summaries for today
            summaries_deleted = await session.execute(
                delete(StandupSummary).where(StandupSummary.date >= start).where(StandupSummary.date <= end)
            )
            
            # 2. Delete all Standup Responses for today
            responses_deleted = await session.execute(
                delete(StandupResponse).where(StandupResponse.date >= start).where(StandupResponse.date <= end)
            )
            
            print(f"Success! Cleared {summaries_deleted.rowcount} summaries and {responses_deleted.rowcount} responses for today.")

if __name__ == "__main__":
    asyncio.run(run())
