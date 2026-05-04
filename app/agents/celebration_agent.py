"""
Celebration Agent — Birthday & Work Anniversary automation.

Runs daily via scheduler to post celebration messages in #general.
HR admins can set dates via /setbirthday and /setanniversary slash commands.
HR admins can set custom message templates via /setmessage command.
"""
import logging
from datetime import datetime, timezone, timedelta, date
from zoneinfo import ZoneInfo

from sqlalchemy import select, extract, and_
from langchain_openai import ChatOpenAI

from app.config import settings
from app.db.session import AsyncSessionLocal
from app.db.models.user import User
from app.db.models.celebration_template import CelebrationTemplate
from app.services.slack_service import slack_service
from app.utils.logger import get_logger

logger = get_logger(__name__)

CELEBRATION_CHANNEL = settings.onboarding_welcome_channel  # #general
_llm = ChatOpenAI(model="gpt-4o", temperature=0.8, openai_api_key=settings.openai_api_key)


# ------------------------------------------------------------------ #
# Custom Template Logic                                               #
# ------------------------------------------------------------------ #

async def _get_custom_template(template_type: str, target_slack_id: str) -> str | None:
    """
    Look up a custom celebration template.
    Priority: per-user template > global template > None (fallback to AI).
    """
    async with AsyncSessionLocal() as session:
        # 1. Check for per-user template
        result = await session.execute(
            select(CelebrationTemplate).where(
                CelebrationTemplate.template_type == template_type,
                CelebrationTemplate.target_slack_id == target_slack_id,
                CelebrationTemplate.is_active == True,
            )
        )
        user_template = result.scalar_one_or_none()
        if user_template:
            return user_template.message_template

        # 2. Check for global template
        result = await session.execute(
            select(CelebrationTemplate).where(
                CelebrationTemplate.template_type == template_type,
                CelebrationTemplate.target_slack_id.is_(None),
                CelebrationTemplate.is_active == True,
            )
        )
        global_template = result.scalar_one_or_none()
        if global_template:
            return global_template.message_template

    return None


def _render_template(template: str, name: str, years: int = None) -> str:
    """Replace {name}, {years}, {date} placeholders in the template."""
    today = datetime.now(ZoneInfo("Asia/Kolkata")).date()
    rendered = template.replace("{name}", name)
    rendered = rendered.replace("{date}", today.strftime("%d %B %Y"))
    if years is not None:
        rendered = rendered.replace("{years}", str(years))
    return rendered


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


async def _get_celebration_message(celebration_type: str, slack_id: str, name: str, years: int = None) -> str:
    """
    Get the celebration message — uses custom template if set, otherwise AI.
    """
    custom_template = await _get_custom_template(celebration_type, slack_id)
    if custom_template:
        logger.info(f"Using custom {celebration_type} template for {slack_id}")
        return _render_template(custom_template, name, years)
    else:
        logger.info(f"No custom template found for {celebration_type}, using AI generation")
        return await _generate_ai_greeting(celebration_type, name, years)


# ------------------------------------------------------------------ #
# Daily celebration check (called by scheduler)                       #
# ------------------------------------------------------------------ #

async def check_and_post_celebrations() -> int:
    """
    Check all users for birthdays and work anniversaries matching today.
    Posts celebration messages to #general.
    Returns count of celebrations posted.
    """
    # Use IST explicitly so daily checks align with scheduler timezone.
    today = datetime.now(ZoneInfo("Asia/Kolkata")).date()
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
                ai_message = await _get_celebration_message("birthday", user.slack_id, display_name)
                
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
                ai_message = await _get_celebration_message("anniversary", user.slack_id, display_name, years)

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
# HR Commands: set / view / edit / reset message templates            #
# ------------------------------------------------------------------ #

async def set_celebration_message(hr_slack_id: str, template_type: str, message: str, target_slack_id: str = None) -> str:
    """HR admin sets (or updates) a celebration message template."""
    if template_type not in ("birthday", "anniversary"):
        return ":x: Type must be `birthday` or `anniversary`."

    async with AsyncSessionLocal() as session:
        async with session.begin():
            # Verify HR admin
            hr_res = await session.execute(select(User).where(User.slack_id == hr_slack_id))
            hr_user = hr_res.scalar_one_or_none()
            if not hr_user or not hr_user.is_hr_admin:
                return ":no_entry: Only HR admins can set celebration messages."

            # Check if template already exists
            query = select(CelebrationTemplate).where(
                CelebrationTemplate.template_type == template_type,
                CelebrationTemplate.is_active == True,
            )
            if target_slack_id:
                query = query.where(CelebrationTemplate.target_slack_id == target_slack_id)
            else:
                query = query.where(CelebrationTemplate.target_slack_id.is_(None))

            result = await session.execute(query)
            existing = result.scalar_one_or_none()

            if existing:
                existing.message_template = message
                existing.created_by_slack_id = hr_slack_id
                action = "Updated"
            else:
                new_template = CelebrationTemplate(
                    template_type=template_type,
                    target_slack_id=target_slack_id,
                    message_template=message,
                    created_by_slack_id=hr_slack_id,
                )
                session.add(new_template)
                action = "Created"

    scope = f"for <@{target_slack_id}>" if target_slack_id else "(global)"
    return f":white_check_mark: {action} *{template_type}* message template {scope}.\n\n*Preview:*\n{_render_template(message, 'John Doe', 3)}"


async def view_celebration_message(hr_slack_id: str, template_type: str) -> str:
    """View the current celebration message template."""
    if template_type not in ("birthday", "anniversary"):
        return ":x: Type must be `birthday` or `anniversary`."

    async with AsyncSessionLocal() as session:
        # Verify HR admin
        hr_res = await session.execute(select(User).where(User.slack_id == hr_slack_id))
        hr_user = hr_res.scalar_one_or_none()
        if not hr_user or not hr_user.is_hr_admin:
            return ":no_entry: Only HR admins can view celebration templates."

        # Get global template
        result = await session.execute(
            select(CelebrationTemplate).where(
                CelebrationTemplate.template_type == template_type,
                CelebrationTemplate.target_slack_id.is_(None),
                CelebrationTemplate.is_active == True,
            )
        )
        global_tmpl = result.scalar_one_or_none()

        # Get all per-user templates
        result = await session.execute(
            select(CelebrationTemplate).where(
                CelebrationTemplate.template_type == template_type,
                CelebrationTemplate.target_slack_id.isnot(None),
                CelebrationTemplate.is_active == True,
            )
        )
        user_templates = result.scalars().all()

    lines = [f":scroll: *{template_type.capitalize()} Message Templates:*\n"]

    if global_tmpl:
        preview = _render_template(global_tmpl.message_template, "John Doe", 3)
        lines.append(f"*Global Template:*\n```{global_tmpl.message_template}```\n*Preview:*\n{preview}\n")
    else:
        lines.append("*Global Template:* _Not set (using AI-generated messages)_\n")

    if user_templates:
        lines.append("*Per-User Templates:*")
        for tmpl in user_templates:
            lines.append(f"• <@{tmpl.target_slack_id}>: `{tmpl.message_template[:60]}...`")

    lines.append(f"\n_Available variables:_ `{{name}}`, `{{years}}`, `{{date}}`")
    return "\n".join(lines)


async def reset_celebration_message(hr_slack_id: str, template_type: str, target_slack_id: str = None) -> str:
    """Delete a celebration message template (reverts to AI generation)."""
    if template_type not in ("birthday", "anniversary"):
        return ":x: Type must be `birthday` or `anniversary`."

    async with AsyncSessionLocal() as session:
        async with session.begin():
            # Verify HR admin
            hr_res = await session.execute(select(User).where(User.slack_id == hr_slack_id))
            hr_user = hr_res.scalar_one_or_none()
            if not hr_user or not hr_user.is_hr_admin:
                return ":no_entry: Only HR admins can reset celebration templates."

            query = select(CelebrationTemplate).where(
                CelebrationTemplate.template_type == template_type,
                CelebrationTemplate.is_active == True,
            )
            if target_slack_id:
                query = query.where(CelebrationTemplate.target_slack_id == target_slack_id)
            else:
                query = query.where(CelebrationTemplate.target_slack_id.is_(None))

            result = await session.execute(query)
            existing = result.scalar_one_or_none()

            if existing:
                existing.is_active = False
                scope = f"for <@{target_slack_id}>" if target_slack_id else "(global)"
                return f":recycle: *{template_type.capitalize()}* template {scope} has been reset. AI-generated messages will be used."
            else:
                return f":information_source: No custom *{template_type}* template found to reset."


# ------------------------------------------------------------------ #
# HR Commands: set birthday / anniversary dates                       #
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
    display_name = target_user.full_name or target_user.slack_username or target_slack_id
    return f":white_check_mark: Birthday for *{display_name}* set to *{formatted}*."


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
    display_name = target_user.full_name or target_user.slack_username or target_slack_id
    return f":white_check_mark: Join date for *{display_name}* set to *{formatted}*."
