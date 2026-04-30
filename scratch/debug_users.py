import asyncio
from sqlalchemy import select
from app.db.session import AsyncSessionLocal
from app.db.models.user import User

async def run():
    async with AsyncSessionLocal() as session:
        res = await session.execute(select(User))
        users = res.scalars().all()
        for u in users:
            print(f"User: {u.slack_username} | slack_id: {u.slack_id} | manager_slack_id: {u.manager_slack_id}")

if __name__ == "__main__":
    asyncio.run(run())
