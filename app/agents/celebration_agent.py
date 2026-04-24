"""
Celebration Agent — Birthday & Work Anniversary automation.

Runs daily via Celery Beat to post celebration messages in #general.
HR admins can set dates via /setbirthday and /setanniversary slash commands.
"""
import logging
from datetime import datetime, timezone, timedelta, date

from sqlalchemy import select, extract
from langchain_openai import ChatOpenAI

from app.config import settings
from app.db.session import AsyncSessionLocal
from app.db.models.user import User
from app.services.slack_service import slack_service
from app.utils.logger import get_logger

logger = get_logger(__name__)

CELEBRATION_CHANNEL = settings.onboarding_welcome_channel  # #general
_llm = ChatOpenAI(model="gpt-4o", temperature=0.8, openai_api_key=settings.openai_api_key)

async def _generate_ai_greeting(celebration_type: str, full_name: str, years: int = None) -> str:
    """Generate a warm, personalized greeting using AI."""
    from langchain_core.messages import HumanMessage, SystemMessage
    
    if celebration_type == "birthday":
        prompt = f"Generate a very warm, professional yet friendly birthday greeting for {full_name}. Use emojis, make it feel personal and celebratory. Keep it under 200 characters."
    else:
        prompt = f"Generate a warm, appreciative work anniversary greeting for {full_name} who just completed {years} years with us. Mention their dedication and make them feel valued. Use emojis. Keep it under 250 characters."

    try:
        response = await _llm.ainvoke([
            SystemMessage(content="You are a friendly HR assistant that writes warm and energetic celebration messages for Slack."),
            HumanMessage(content=prompt)
        ])
        return response.content.strip()
    except Exception as exc:
        logger.error(f"Failed to generate AI greeting: {exc}")
        # Fallback
        if celebration_type == "birthday":
            return f"Happy Birthday {full_name}! 🎂 Wishing you a wonderful day filled with joy and celebration! ✨"
        else:
            return f"Congratulations {full_name} on completing {years} years with us! 🎊 Thank you for your incredible work and dedication! 🚀"


# ------------------------------------------------------------------ #
# Daily celebration check (called by Celery Beat)                     #
# ------------------------------------------------------------------ #

async def check_and_post_celebrations() -> int:
    """
    Check all users for birthdays and work anniversaries matching today.
    Posts celebration messages to #general.
    Returns count of celebrations posted.
    """
    today = date.today()
    today_month = today.month
    today_day = today.day
    posted = 0

    async with AsyncSessionLocal() as session:
        # ---- Birthdays ----
        birthday_result = await session.execute(
            select(User).where(
                User.is_active == True,
                User.birthday.isnot(None),
                extract("month", User.birthday) == today_month,
                extract("day", User.birthday) == today_day,
            )
        )
        birthday_users = birthday_result.scalars().all()

        for user in birthday_users:
            try:
                display_name = user.full_name or user.slack_username
                ai_message = await _generate_ai_greeting("birthday", display_name)
                
                blocks = [
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f":birthday: *Celebration Time!* :tada:\n\n{ai_message}\n\nJoin us in wishing <@{user.slack_id}> a fantastic day!",
                        },
                    },
                ]
                await slack_service.post_to_channel(
                    CELEBRATION_CHANNEL,
                    text=f"🎂 Happy Birthday {user.slack_username}!",
                    blocks=blocks,
                )
                posted += 1
                logger.info("Birthday celebration posted", extra={"user": user.slack_id})
            except Exception:
                logger.exception("Failed to post birthday celebration", extra={"user": user.slack_id})

        # ---- Work Anniversaries ----
        anniversary_result = await session.execute(
            select(User).where(
                User.is_active == True,
                User.joined_at.isnot(None),
                extract("month", User.joined_at) == today_month,
                extract("day", User.joined_at) == today_day,
            )
        )
        anniversary_users = anniversary_result.scalars().all()

        for user in anniversary_users:
            try:
                years = today.year - user.joined_at.year
                if years < 1:
                    continue  # Skip if less than 1 year

                display_name = user.full_name or user.slack_username
                ai_message = await _generate_ai_greeting("anniversary", display_name, years)

                blocks = [
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f":tada: *Work Anniversary!* :star2:\n\n{ai_message}\n\nCongratulations <@{user.slack_id}>! 🥳",
                        },
                    },
                ]
                await slack_service.post_to_channel(
                    CELEBRATION_CHANNEL,
                    text=f"🎉 {user.slack_username} completes {years} years with us today!",
                    blocks=blocks,
                )
                posted += 1
                logger.info("Anniversary celebration posted", extra={"user": user.slack_id, "years": years})
            except Exception:
                logger.exception("Failed to post anniversary celebration", extra={"user": user.slack_id})

    if posted == 0:
        logger.info("No celebrations today")
    else:
        logger.info(f"Posted {posted} celebration(s) today")

    return posted


# ------------------------------------------------------------------ #
# HR Commands: set birthday / anniversary                             #
# ------------------------------------------------------------------ #

async def set_user_birthday(hr_slack_id: str, target_slack_id: str, birthday_str: str) -> str:
    """
    HR admin sets a user's birthday.
    birthday_str format: YYYY-MM-DD
    Returns a confirmation message.
    """
    try:
        birthday_date = datetime.strptime(birthday_str, "%Y-%m-%d").date()
    except ValueError:
        return ":x: Invalid date format. Use `YYYY-MM-DD` (e.g., `1995-06-15`)."

    async with AsyncSessionLocal() as session:
        async with session.begin():
            # Verify HR admin
            hr_res = await session.execute(select(User).where(User.slack_id == hr_slack_id))
            hr_user = hr_res.scalar_one_or_none()
            if not hr_user or not hr_user.is_hr_admin:
                return ":no_entry: Only HR admins can set birthdays."

            # Find target user by ID or username
            from sqlalchemy import or_
            target_res = await session.execute(
                select(User).where(
                    or_(User.slack_id == target_slack_id, User.slack_username == target_slack_id)
                )
            )
            target_user = target_res.scalar_one_or_none()
            if not target_user:
                return f":x: User `{target_slack_id}` not found in the system."

            target_user.birthday = birthday_date
    
    formatted = birthday_date.strftime("%B %d, %Y")
    return f":white_check_mark: Birthday for `{target_slack_id}` set to *{formatted}*."


async def set_user_anniversary(hr_slack_id: str, target_slack_id: str, date_str: str) -> str:
    """
    HR admin sets a user's join date (work anniversary).
    date_str format: YYYY-MM-DD
    Returns a confirmation message.
    """
    try:
        join_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        return ":x: Invalid date format. Use `YYYY-MM-DD` (e.g., `2023-01-10`)."

    async with AsyncSessionLocal() as session:
        async with session.begin():
            # Verify HR admin
            hr_res = await session.execute(select(User).where(User.slack_id == hr_slack_id))
            hr_user = hr_res.scalar_one_or_none()
            if not hr_user or not hr_user.is_hr_admin:
                return ":no_entry: Only HR admins can set anniversary dates."

            # Find target user by ID or username
            from sqlalchemy import or_
            target_res = await session.execute(
                select(User).where(
                    or_(User.slack_id == target_slack_id, User.slack_username == target_slack_id)
                )
            )
            target_user = target_res.scalar_one_or_none()
            if not target_user:
                return f":x: User `{target_slack_id}` not found in the system."

            target_user.joined_at = join_date

    formatted = join_date.strftime("%B %d, %Y")
    return f":white_check_mark: Join date for `{target_slack_id}` set to *{formatted}*."
