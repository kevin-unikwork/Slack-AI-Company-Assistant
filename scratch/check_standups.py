import asyncio
from sqlalchemy import select, func
from app.db.session import AsyncSessionLocal
from app.db.models.standup import StandupResponse

async def run():
    async with AsyncSessionLocal() as session:
        res = await session.execute(select(func.count(StandupResponse.id)))
        print("Total standups:", res.scalar())
        
        # Also let's see dates
        res_dates = await session.execute(select(StandupResponse.date))
        print("Dates:", res_dates.scalars().all())

if __name__ == "__main__":
    asyncio.run(run())
