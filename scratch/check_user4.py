import asyncio
from sqlalchemy import select
from app.db.session import AsyncSessionLocal
from app.db.models.standup import StandupResponse

async def run():
    async with AsyncSessionLocal() as session:
        res = await session.execute(
            select(StandupResponse)
            .where(StandupResponse.user_slack_id == 'U0AU1BJ1B3R')
            .order_by(StandupResponse.id.desc())
            .limit(1)
        )
        st = res.scalars().first()
        if st:
            print(f"Y: {st.yesterday}")
            print(f"T: {st.today}")
            print(f"B: {st.blockers}")
        else:
            print("No standup found for U0AU1BJ1B3R")

if __name__ == "__main__":
    asyncio.run(run())
