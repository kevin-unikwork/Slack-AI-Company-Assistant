import asyncio
from sqlalchemy import select
from app.db.session import AsyncSessionLocal
from app.db.models.standup import StandupResponse

async def run():
    async with AsyncSessionLocal() as session:
        res = await session.execute(
            select(StandupResponse)
            .order_by(StandupResponse.id.desc())
            .limit(1)
        )
        s = res.scalars().first()
        if s:
            print("Yesterday:", repr(s.yesterday))
            print("Today:", repr(s.today))
            print("Blockers:", repr(s.blockers))
        else:
            print("No standup found.")

if __name__ == "__main__":
    asyncio.run(run())
