import asyncio
from sqlalchemy import select, and_
from app.db.session import AsyncSessionLocal
from app.db.models.standup import StandupResponse
from app.agents.standup_agent import _today_range

async def run():
    try:
        start, end = _today_range()
        print(f"Start: {start}, End: {end}")
        async with AsyncSessionLocal() as session:
            res = await session.execute(
                select(StandupResponse).where(
                    and_(
                        StandupResponse.date >= start,
                        StandupResponse.date <= end
                    )
                )
            )
            print("Query succeeded!", len(res.scalars().all()))
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(run())
