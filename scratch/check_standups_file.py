import asyncio
from sqlalchemy import select, func
from app.db.session import AsyncSessionLocal
from app.db.models.standup import StandupResponse

async def run():
    async with AsyncSessionLocal() as session:
        res = await session.execute(select(func.count(StandupResponse.id)))
        count = res.scalar()
        
        with open("scratch/standups.txt", "w") as f:
            f.write(f"Total: {count}\n")
            res_dates = await session.execute(select(StandupResponse.date))
            dates = res_dates.scalars().all()
            for d in dates:
                f.write(f"{d}\n")

if __name__ == "__main__":
    asyncio.run(run())
