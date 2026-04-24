"""
Reminder Agent — Natural language reminder parsing and delivery.

Uses GPT to extract time and message from natural language input.
Celery Beat checks every minute for due reminders and fires DMs.
"""
import json
import logging
from datetime import datetime, timezone, timedelta

from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage
from sqlalchemy import select, and_

from app.config import settings
from app.db.session import AsyncSessionLocal
from app.db.models.reminder import Reminder
from app.services.slack_service import slack_service
from app.utils.logger import get_logger

logger = get_logger(__name__)

_llm = ChatOpenAI(model="gpt-4o", temperature=0, openai_api_key=settings.openai_api_key)

PARSE_SYSTEM_PROMPT = """You are a reminder time parser. The user will give you a natural language reminder request.
Extract two things:
1. "delay_minutes": the number of minutes from NOW until the reminder should fire. 
   - "in 2 hours" = 120
   - "in 30 minutes" = 30
   - "tomorrow at 10am" = calculate minutes from the current time to tomorrow 10:00 AM in the user's timezone
   - "at 3pm" = calculate minutes from current time to 3:00 PM today (or tomorrow if 3pm has passed)
2. "message": the actual reminder text (what to remind about)

The current time is: {current_time} (IST, UTC+5:30)

Respond ONLY with a JSON object: {{"delay_minutes": <int>, "message": "<string>"}}
Do NOT include any other text. Only the JSON object.

Examples:
- Input: "me in 2 hours to review the PR" → {{"delay_minutes": 120, "message": "review the PR"}}
- Input: "me at 3pm to join the standup call" → {{"delay_minutes": <calculated>, "message": "join the standup call"}}
- Input: "me tomorrow at 10am to submit the report" → {{"delay_minutes": <calculated>, "message": "submit the report"}}
- Input: "check emails in 15 minutes" → {{"delay_minutes": 15, "message": "check emails"}}
"""


async def parse_and_create_reminder(slack_id: str, text: str) -> str:
    """
    Parse a natural language reminder and create a DB record.
    Returns a confirmation message for the user.
    """
    try:
        # Get current IST time for context
        now_utc = datetime.now(timezone.utc)
        now_ist = now_utc + timedelta(hours=5, minutes=30)
        current_time_str = now_ist.strftime("%Y-%m-%d %I:%M %p")

        prompt = PARSE_SYSTEM_PROMPT.format(current_time=current_time_str)

        # Use Pydantic structured output for reliable parsing
        from pydantic import BaseModel, Field

        class ReminderParsed(BaseModel):
            delay_minutes: int = Field(description="Number of minutes from now until the reminder fires")
            message: str = Field(description="The reminder message text")

        structured_llm = _llm.with_structured_output(ReminderParsed)
        parsed = await structured_llm.ainvoke(
            f"{prompt}\n\nUser input: {text}"
        )

        if not parsed:
            return ":x: I couldn't understand that reminder. Try: `/reminder me in 2 hours to review the PR`"

        delay_minutes = parsed.delay_minutes
        message = parsed.message

        if delay_minutes < 1:
            return ":x: I can't set a reminder in the past. Please specify a future time."

        if delay_minutes > 43200:  # 30 days
            return ":x: Reminders can be set for a maximum of 30 days in the future."

        # Calculate the exact reminder time (UTC)
        remind_at_utc = now_utc + timedelta(minutes=delay_minutes)
        remind_at_ist = remind_at_utc + timedelta(hours=5, minutes=30)

        # Save to database
        async with AsyncSessionLocal() as session:
            async with session.begin():
                reminder = Reminder(
                    user_slack_id=slack_id,
                    message=message,
                    remind_at=remind_at_utc,
                )
                session.add(reminder)

        # Format confirmation
        if delay_minutes < 60:
            time_desc = f"{delay_minutes} minute{'s' if delay_minutes != 1 else ''}"
        elif delay_minutes < 1440:
            hours = delay_minutes // 60
            mins = delay_minutes % 60
            time_desc = f"{hours} hour{'s' if hours != 1 else ''}"
            if mins > 0:
                time_desc += f" {mins} min"
        else:
            days = delay_minutes // 1440
            time_desc = f"{days} day{'s' if days != 1 else ''}"

        formatted_time = remind_at_ist.strftime("%I:%M %p (IST)")

        return (
            f":white_check_mark: *Reminder set!*\n"
            f"• *What:* {message}\n"
            f"• *When:* {formatted_time} (in {time_desc})\n"
            f"_I'll DM you when it's time!_"
        )

    except json.JSONDecodeError:
        logger.exception("Failed to parse reminder JSON from LLM")
        return ":x: I couldn't understand that reminder. Try: `/remind me in 2 hours to review the PR`"
    except Exception as exc:
        logger.exception("Reminder creation failed", extra={"slack_id": slack_id})
        return f":x: Something went wrong creating your reminder: `{exc}`"


async def check_and_fire_reminders() -> int:
    """
    Check for due reminders and send DMs.
    Called every minute by Celery Beat.
    Returns count of reminders fired.
    """
    now_utc = datetime.now(timezone.utc)
    fired = 0

    async with AsyncSessionLocal() as session:
        async with session.begin():
            result = await session.execute(
                select(Reminder).where(
                    and_(
                        Reminder.is_sent == False,
                        Reminder.remind_at <= now_utc,
                    )
                )
            )
            due_reminders = result.scalars().all()

            for reminder in due_reminders:
                try:
                    # Calculate how long ago it was set
                    created_at = reminder.created_at
                    logger.debug(f"DEBUG TIME: now_utc={now_utc} ({now_utc.tzinfo}), created_at={created_at} ({created_at.tzinfo})")
                    if created_at.tzinfo is None:
                        created_at = created_at.replace(tzinfo=timezone.utc)
                    
                    elapsed = now_utc - created_at
                    logger.debug(f"DEBUG ELAPSED: {elapsed.total_seconds()} seconds")
                    if elapsed.total_seconds() < 60:
                        ago_text = "just now"
                    elif elapsed.total_seconds() < 3600:
                        mins = int(elapsed.total_seconds() // 60)
                        ago_text = f"{mins} minute{'s' if mins != 1 else ''} ago"
                    elif elapsed.total_seconds() < 86400:
                        hours = int(elapsed.total_seconds() // 3600)
                        ago_text = f"{hours} hour{'s' if hours != 1 else ''} ago"
                    else:
                        days = int(elapsed.total_seconds() // 86400)
                        ago_text = f"{days} day{'s' if days != 1 else ''} ago"

                    blocks = [
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": f":alarm_clock: *Reminder:* {reminder.message}",
                            },
                        },
                        {
                            "type": "context",
                            "elements": [
                                {
                                    "type": "mrkdwn",
                                    "text": f"_Set {ago_text}_",
                                }
                            ],
                        },
                    ]

                    await slack_service.dm_user(
                        reminder.user_slack_id,
                        text=f"⏰ Reminder: {reminder.message}",
                        blocks=blocks,
                    )
                    reminder.is_sent = True
                    fired += 1
                    logger.info(
                        "Reminder fired",
                        extra={"reminder_id": reminder.id, "user": reminder.user_slack_id},
                    )
                except Exception:
                    logger.exception(
                        "Failed to fire reminder",
                        extra={"reminder_id": reminder.id, "user": reminder.user_slack_id},
                    )

    if fired > 0:
        logger.info(f"Fired {fired} reminder(s)")
    return fired
