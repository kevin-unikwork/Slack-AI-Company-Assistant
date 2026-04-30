import asyncio
from sqlalchemy import select, and_
from app.db.session import AsyncSessionLocal
from app.db.models.standup import StandupResponse
from app.agents.standup_agent import _today_range

async def run():
    async with AsyncSessionLocal() as session:
        start, end = _today_range()
        res = await session.execute(
            select(StandupResponse).where(
                and_(
                    StandupResponse.date >= start,
                    StandupResponse.date <= end
                )
            ).order_by(StandupResponse.id.desc())
        )
        standups = res.scalars().all()
        for s in standups:
            print(f"ID={s.id}, User={s.user_slack_id}, Step={s.step}, Complete={s.is_complete}, Date={s.date}")
            print(f" Yesterday: {s.yesterday}, Today: {s.today}")

if __name__ == "__main__":
    asyncio.run(run())
