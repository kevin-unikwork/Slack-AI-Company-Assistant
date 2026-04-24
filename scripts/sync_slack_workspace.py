import asyncio
import sys
import os

# Add project root to sys.path
sys.path.append(os.getcwd())

from app.db.session import AsyncSessionLocal
from app.services.slack_service import slack_service
from app.services.user_service import user_service
from app.schemas.user import UserCreate

async def sync_workspace():
    """
    Fetch all real users from the Slack workspace and sync them to the database.
    """
    print("Fetching users from Slack workspace...")
    try:
        slack_users = await slack_service.get_all_workspace_users()
        print(f"Found {len(slack_users)} real users in Slack.")
        
        new_users = []
        
        async with AsyncSessionLocal() as session:
            async with session.begin():
                for s_user in slack_users:
                    if s_user.get("deleted") or not s_user.get("id"):
                        continue
                        
                    slack_id = s_user["id"]
                    
                    # Check if new
                    from sqlalchemy import select
                    res = await session.execute(select(User).where(User.slack_id == slack_id))
                    if not res.scalar_one_or_none():
                        new_users.append(s_user)

                    username = s_user.get("name") or "Unknown"
                    full_name = s_user.get("real_name")
                    email = s_user.get("profile", {}).get("email")
                    
                    print(f"Synchronizing: {username} ({slack_id})")
                    
                    await user_service.create_or_update(
                        session,
                        UserCreate(
                            slack_id=slack_id,
                            slack_username=username,
                            full_name=full_name,
                            email=email
                        )
                    )
            
            # Notify HR about new users
            if new_users:
                res = await session.execute(select(User).where(User.is_hr_admin == True))
                hr_admins = res.scalars().all()
                
                for hr in hr_admins:
                    for nu in new_users:
                        uname = nu.get("real_name") or nu.get("name")
                        await slack_service.dm_user(
                            hr.slack_id,
                            f"🆕 *New User Detected*: {uname} (<@{nu['id']}>) has joined the workspace.\n"
                            f"Please use `/assign <@{nu['id']}> @ProjectManager` to set their manager."
                        )

        print(f"\nSuccess: Workspace synchronization complete. ({len(new_users)} new users detected)")
    except Exception as e:
        print(f"\nError during synchronization: {e}")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(sync_workspace())
