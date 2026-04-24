import asyncio
import sys
import os

# Add the project root to sys.path
sys.path.append(os.getcwd())

from app.db.session import AsyncSessionLocal
from app.db.models.user import User
from app.services.user_service import user_service
from app.schemas.user import UserCreate
from sqlalchemy import select

async def initialize_admin(slack_id: str, username: str, password: str = None):
    async with AsyncSessionLocal() as session:
        async with session.begin():
            # 1. Ensure user exists
            user = await user_service.get_by_slack_id(session, slack_id)
            if not user:
                print(f"User {slack_id} not found. Creating new user...")
                user = await user_service.create_or_update(
                    session, 
                    UserCreate(slack_id=slack_id, slack_username=username)
                )
            
            # 2. Set as admin
            user.is_hr_admin = True
            print(f"User {user.slack_username} ({user.slack_id}) promoted to HR Admin.")
            
            # 3. Set password if provided
            if password:
                await user_service.set_password(session, slack_id, password)
                print("Password set successfully.")
            else:
                print("Warning: No password set. This admin will not be able to log in to the HR portal.")

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python scripts/promote_admin.py <slack_id> <username> [password]")
        sys.exit(1)
    
    target_id = sys.argv[1]
    target_username = sys.argv[2]
    target_password = sys.argv[3] if len(sys.argv) > 3 else None
    
    asyncio.run(initialize_admin(target_id, target_username, target_password))
