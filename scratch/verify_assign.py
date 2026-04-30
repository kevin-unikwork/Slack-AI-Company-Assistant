import asyncio
from unittest.mock import patch, AsyncMock
import sys
import os

# Set up mock for slack_service BEFORE importing slack routes
with patch("app.services.slack_service.slack_service.dm_user", new_callable=AsyncMock) as mock_dm:
    # We need to mock the Bolt app too because it might try to start
    with patch("slack_bolt.app.async_app.AsyncApp"), patch("app.api.routes.slack.bolt_app"):
        from app.api.routes.slack import _run_assign
        from app.db.session import AsyncSessionLocal
        from app.db.models.user import User
        from sqlalchemy import select

        async def verify():
            admin_id = "U0AV25Z3MRN"  # Known HR Admin
            employee_id = "U0AU7NK2UVC" # Kevin
            new_manager_id = "U0AVA7X1RB2" # Akhil
            
            print(f"--- Verification Test: /assign command ---")
            print(f"Admin: {admin_id}")
            print(f"Employee: {employee_id}")
            print(f"Target Manager: {new_manager_id}")
            
            # 1. Check initial state
            async with AsyncSessionLocal() as session:
                res = await session.execute(select(User).where(User.slack_id == employee_id))
                emp = res.scalars().first()
                old_manager = emp.manager_slack_id
                print(f"Initial Manager: {old_manager}")

            # 2. Run the assign logic
            await _run_assign(admin_id, employee_id, new_manager_id)
            
            # 3. Check final state
            async with AsyncSessionLocal() as session:
                res = await session.execute(select(User).where(User.slack_id == employee_id))
                emp = res.scalars().first()
                print(f"Updated Manager: {emp.manager_slack_id}")
                
                if emp.manager_slack_id == new_manager_id:
                    print("RESULT: PASS - Database updated successfully.")
                else:
                    print("RESULT: FAIL - Database was not updated.")

        if __name__ == "__main__":
            asyncio.run(verify())
