import asyncio
from sqlalchemy import select
from app.db.session import AsyncSessionLocal
from app.db.models.user import User

async def run():
    async with AsyncSessionLocal() as session:
        res = await session.execute(select(User.slack_username, User.is_active))
        print("Users:", res.all())

if __name__ == "__main__":
    asyncio.run(run())
