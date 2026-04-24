import asyncio
import sys
import os

# Add project root to sys.path
sys.path.append(os.getcwd())

from app.db.session import AsyncSessionLocal
from app.db.models.user import User
from app.services.user_service import user_service
from app.schemas.user import UserCreate
from sqlalchemy import update, select

async def setup_hierarchy():
    """
    Sets up the organizational hierarchy with HR Managers and Project Managers.
    """
    async with AsyncSessionLocal() as session:
        async with session.begin():
            # 1. Ensure HR Manager role (for the user)
            hr_admin_id = "U0AV25Z3MRN"
            res = await session.execute(select(User).where(User.slack_id == hr_admin_id))
            hr_admin = res.scalar_one_or_none()
            if hr_admin:
                hr_admin.is_hr_admin = True
                print(f"Verified HR Admin: {hr_admin.slack_username}")

            # 2. Add 2 New Project Managers
            pm1_id, pm1_name = "U0AU0G1TYB1", "Ishan"
            pm2_id, pm2_name = "U0AVA7X1RB2", "Akhil Patoliya"
            
            for pid, pname in [(pm1_id, pm1_name), (pm2_id, pm2_name)]:
                pm = await user_service.create_or_update(
                    session,
                    UserCreate(slack_id=pid, slack_username=pname)
                )
                pm.is_project_manager = True
                print(f"Verified PM Role: {pname} ({pid})")

            # 3. Assign Employees to PMs
            assignments = {
                "U0AU7NK2UVC": pm1_id,  # Kevin Webappdev -> Ishan
                "U0AU9Q78TM0": pm2_id,  # Test User 1 -> Akhil
                "U0AU3LU4BAP": pm1_id   # 24mca052 -> Ishan
            }
            
            for emp_id, mgr_id in assignments.items():
                await session.execute(
                    update(User)
                    .where(User.slack_id == emp_id)
                    .values(manager_slack_id=mgr_id)
                )
                print(f"Assigned Employee {emp_id} to Manager {mgr_id}")

            # 4. Cleanup Dummy Data
            from sqlalchemy import delete
            await session.execute(delete(User).where(User.slack_id.in_(['U_PM_ALPHA', 'U_PM_BETA'])))
            print("Cleaned up dummy PM records.")

    print("\nSuccess: Organizational hierarchy setup complete.")

if __name__ == "__main__":
    asyncio.run(setup_hierarchy())
