import asyncio
from sqlalchemy import select
from app.db.session import AsyncSessionLocal
from app.db.models.user import User

async def run():
    async with AsyncSessionLocal() as session:
        res = await session.execute(select(User).where(User.is_hr_admin == True))
        admins = res.scalars().all()
        print(f"Admins: {[a.slack_username for a in admins]}")
        print(f"Admin IDs: {[a.slack_id for a in admins]}")

if __name__ == "__main__":
    asyncio.run(run())
