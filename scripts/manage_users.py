import asyncio
import sys
import os
import argparse
from typing import Optional

# Add the project root to sys.path
sys.path.append(os.getcwd())

from app.db.session import AsyncSessionLocal
from app.db.models.user import User
from app.services.user_service import user_service
from app.schemas.user import UserCreate, UserUpdate
from sqlalchemy import select

async def list_users():
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).order_by(User.id))
        users = result.scalars().all()
        
        print("\n--- Current Users ---")
        print(f"{'ID':<4} {'Slack ID':<15} {'Username':<15} {'HR':<4} {'PM':<4} {'Manager':<15}")
        print("-" * 65)
        for u in users:
            hr_str = "YES" if u.is_hr_admin else "no"
            pm_str = "YES" if u.is_project_manager else "no"
            manager = u.manager_slack_id or "root"
            print(f"{u.id:<4} {u.slack_id:<15} {u.slack_username:<15} {hr_str:<4} {pm_str:<4} {manager:<15}")
        print("-" * 65 + "\n")

async def create_mock_user(slack_id: str, username: str, manager_id: Optional[str] = None, is_pm: bool = False):
    async with AsyncSessionLocal() as session:
        async with session.begin():
            user = await user_service.create_or_update(
                session,
                UserCreate(
                    slack_id=slack_id,
                    slack_username=username,
                    manager_slack_id=manager_id
                )
            )
            user.is_project_manager = is_pm
            print(f"Mock user '{username}' ({slack_id}) created/updated. PM Role: {is_pm}")
            if manager_id:
                print(f"Assigned manager: {manager_id}")

async def set_admin(slack_id: str, status: bool):
    async with AsyncSessionLocal() as session:
        async with session.begin():
            try:
                await user_service.set_admin(session, slack_id, status)
                print(f"HR Admin status for {slack_id} set to {status}.")
            except Exception as e:
                print(f"Error: {e}")

async def set_pm(slack_id: str, status: bool):
    async with AsyncSessionLocal() as session:
        async with session.begin():
            try:
                result = await session.execute(select(User).where(User.slack_id == slack_id))
                user = result.scalar_one_or_none()
                if not user:
                    print(f"User {slack_id} not found.")
                    return
                user.is_project_manager = status
                print(f"Project Manager status for {slack_id} set to {status}.")
            except Exception as e:
                print(f"Error: {e}")

async def set_password(slack_id: str, password: str):
    async with AsyncSessionLocal() as session:
        async with session.begin():
            try:
                await user_service.set_password(session, slack_id, password)
                print(f"Password updated for {slack_id}.")
            except Exception as e:
                print(f"Error: {e}")

def main():
    parser = argparse.ArgumentParser(description="Manage Slack AI Bot Users")
    subparsers = parser.add_subparsers(dest="command")

    # List
    subparsers.add_parser("list", help="List all users")

    # Add Mock
    add_parser = subparsers.add_parser("add", help="Add a mock user")
    add_parser.add_argument("slack_id", help="Slack ID of the mock user")
    add_parser.add_argument("username", help="Username for display")
    add_parser.add_argument("--manager", help="Slack ID of the manager")
    add_parser.add_argument("--pm", action="store_true", help="Set as Project Manager")

    # Promote
    admin_parser = subparsers.add_parser("admin", help="Set Admin/Role status")
    admin_parser.add_argument("slack_id", help="Slack ID of the user")
    admin_parser.add_argument("--grant", action="store_true", help="Grant HR admin access")
    admin_parser.add_argument("--revoke", action="store_true", help="Revoke HR admin access")
    admin_parser.add_argument("--pm", action="store_true", help="Grant Project Manager access")
    admin_parser.add_argument("--no-pm", action="store_true", help="Revoke Project Manager access")

    # Password
    pwd_parser = subparsers.add_parser("passwd", help="Set user password")
    pwd_parser.add_argument("slack_id", help="Slack ID of the user")
    pwd_parser.add_argument("password", help="New password")

    args = parser.parse_args()

    if args.command == "list":
        asyncio.run(list_users())
    elif args.command == "add":
        asyncio.run(create_mock_user(args.slack_id, args.username, args.manager, args.pm))
    elif args.command == "admin":
        if args.pm or args.no_pm:
            status = True if args.pm else False
            asyncio.run(set_pm(args.slack_id, status))
        else:
            status = True if args.grant else False
            asyncio.run(set_admin(args.slack_id, status))
    elif args.command == "passwd":
        asyncio.run(set_password(args.slack_id, args.password))
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
