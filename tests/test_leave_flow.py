import asyncio
import sys
import os
from datetime import datetime, timedelta

# Add project root to sys.path
sys.path.append(os.getcwd())

from app.db.session import AsyncSessionLocal
from app.db.models.user import User
from app.db.models.leave import LeaveRequest
from app.services.slack_service import slack_service
from app.agents.leave_agent import _leave_request_blocks
from sqlalchemy import select

async def simulate_leave_request(employee_slack_id: str):
    """
    Simulates an employee submitting a leave request.
    Triggers a notification to their manager.
    """
    async with AsyncSessionLocal() as session:
        async with session.begin():
            # 1. Fetch employee and manager
            result = await session.execute(
                select(User).where(User.slack_id == employee_slack_id)
            )
            employee = result.scalar_one_or_none()
            
            if not employee:
                print(f"Error: Employee {employee_slack_id} not found.")
                return
            
            manager_slack_id = employee.manager_slack_id
            if not manager_slack_id:
                print(f"Error: Employee {employee_slack_id} has no manager assigned.")
                return
            
            # 2. Create Leave Request in DB
            start = datetime.now(timezone.utc) + timedelta(days=7)
            end = start + timedelta(days=2)
            
            leave = LeaveRequest(
                user_slack_id=employee_slack_id,
                manager_slack_id=manager_slack_id,
                start_date=start,
                end_date=end,
                reason="Simulated test leave",
                status="pending"
            )
            session.add(leave)
            await session.flush()
            leave_id = leave.id
            
            print(f"Created Leave Request #{leave_id} for {employee.slack_username}")

    # 3. Notify Manager via Slack
    print(f"Notifying manager {manager_slack_id}...")
    days = (end.date() - start.date()).days + 1
    blocks = _leave_request_blocks(
        slack_id=employee_slack_id,
        leave_id=leave_id,
        start=start,
        end=end,
        days=days,
        reason="Simulated test leave"
    )
    
    try:
        # Open DM with manager and post the message
        im_resp = await slack_service._client.conversations_open(users=[manager_slack_id])
        channel_id = im_resp["channel"]["id"]
        
        msg_resp = await slack_service._client.chat_postMessage(
            channel=channel_id,
            text=f"Leave request from {employee.slack_username}",
            blocks=blocks
        )
        
        # Update DB with Slack message details
        async with AsyncSessionLocal() as session:
            async with session.begin():
                result = await session.execute(
                    select(LeaveRequest).where(LeaveRequest.id == leave_id)
                )
                leave_row = result.scalar_one()
                leave_row.manager_message_ts = msg_resp["ts"]
                leave_row.manager_channel = channel_id
        
        print(f"Success! Notification sent to manager {manager_slack_id}")
        print("Check your Slack for the approval buttons.")
        
    except Exception as e:
        print(f"Error notifying manager: {e}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python tests/test_leave_flow.py <employee_slack_id>")
        sys.exit(1)
    
    emp_id = sys.argv[1]
    asyncio.run(simulate_leave_request(emp_id))
