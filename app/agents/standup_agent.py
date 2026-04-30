from __future__ import annotations

import json
from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage
from sqlalchemy import and_, func, select

from app.config import settings
from app.db.models.standup import StandupResponse, StandupSummary
from app.db.models.user import User
from app.db.session import AsyncSessionLocal
from app.services.slack_service import slack_service
from app.utils.exceptions import StandupAgentError
from app.utils.logger import get_logger
from app.utils.state import state_manager

logger = get_logger(__name__)

_llm = ChatOpenAI(model="gpt-4o", temperature=0.3, openai_api_key=settings.openai_api_key)

STANDUP_KEY_STEP = "standup:{slack_id}:step"
STANDUP_KEY_DATE = "standup:{slack_id}:date"
STANDUP_TTL = 60 * 60 * 12  # 12 hours

_IST = ZoneInfo("Asia/Kolkata")

SUMMARY_SYSTEM_PROMPT = """You are generating a daily standup summary for a software company.
Format the following individual standup responses into a clean, scannable team summary.
Group by: Yesterday's progress, Today's plans, Active blockers.
Make it concise and professional. Use bullet points.
If there are no blockers, say "No blockers reported."
"""

# ------------------------------------------------------------------ #
# Date query helper                                                    #
# ------------------------------------------------------------------ #

def _today_range() -> tuple[datetime, datetime]:
    """Return (start_of_day, end_of_day) as UTC-naive datetimes for today in IST."""
    today_ist = datetime.now(_IST).date()
    start = datetime.combine(today_ist, time.min, tzinfo=_IST).astimezone(timezone.utc).replace(tzinfo=None)
    end = datetime.combine(today_ist, time.max, tzinfo=_IST).astimezone(timezone.utc).replace(tzinfo=None)
    return start, end


# ------------------------------------------------------------------ #
# Trigger (called by Scheduler)                                        #
# ------------------------------------------------------------------ #

async def trigger_standup_for_all() -> int:
    """
    DM every active user to kick off their standup.
    Creates a StandupResponse row for each user.
    """
    logger.info("Triggering standup for all active users")
    today_ist = datetime.now(_IST).date()
    today_str = today_ist.isoformat()
    count = 0

    async with AsyncSessionLocal() as session:
        async with session.begin():
            # 1. Fetch all active users
            result = await session.execute(select(User).where(User.is_active == True))
            users = list(result.scalars().all())
            
            if not users:
                logger.warning("No active users found to trigger standup")
                return 0

            # 2. Process each user
            for user in users:
                try:
                    # Check if they already have a standup today to avoid duplicates
                    start, end = _today_range()
                    existing_res = await session.execute(
                        select(StandupResponse).where(
                            and_(
                                StandupResponse.user_slack_id == user.slack_id,
                                StandupResponse.date >= start,
                                StandupResponse.date <= end
                            )
                        )
                    )
                    if existing_res.scalars().first():
                        logger.info(f"User {user.slack_id} already has a standup today, skipping trigger")
                        continue

                    # Create DB record
                    standup = StandupResponse(
                        user_slack_id=user.slack_id,
                        step=1,
                        date=datetime.now(timezone.utc),
                    )
                    session.add(standup)

                    # Update Cache State (non-blocking fallback)
                    await state_manager.set_state(STANDUP_KEY_STEP.format(slack_id=user.slack_id), "1", STANDUP_TTL)
                    await state_manager.set_state(STANDUP_KEY_DATE.format(slack_id=user.slack_id), today_str, STANDUP_TTL)

                    # Send Slack DM
                    blocks = [
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": ":wave: *Good morning! Time for your daily standup.*",
                            },
                        },
                        {
                            "type": "section",
                            "text": {"type": "mrkdwn", "text": "What did you work on *yesterday*?"},
                        },
                        {"type": "divider"},
                    ]
                    await slack_service.dm_user(
                        user.slack_id,
                        text="Good morning! Time for your daily standup. What did you work on yesterday?",
                        blocks=blocks,
                    )
                    count += 1
                    logger.info("Standup triggered successfully", extra={"slack_id": user.slack_id})

                except Exception:
                    logger.exception(f"Failed to trigger standup for user {user.slack_id}")

    logger.info(f"Standup trigger complete. Messaged {count} users.")
    return count


async def trigger_standup_for_user(slack_id: str) -> bool:
    """
    DM a specific user to kick off their standup manually via the /standup command.
    """
    logger.info(f"Triggering standup manually for user {slack_id}")
    today_ist = datetime.now(_IST).date()
    today_str = today_ist.isoformat()

    async with AsyncSessionLocal() as session:
        async with session.begin():
            try:
                start, end = _today_range()
                existing_res = await session.execute(
                    select(StandupResponse).where(
                        and_(
                            StandupResponse.user_slack_id == slack_id,
                            StandupResponse.date >= start,
                            StandupResponse.date <= end
                        )
                    )
                )
                
                standup = existing_res.scalars().first()
                if standup:
                    if standup.is_complete:
                        await slack_service.dm_user(
                            slack_id, 
                            ":white_check_mark: You have already completed your standup today!"
                        )
                        return False
                    else:
                        # Reset the step to 1 to restart the current day's standup
                        standup.step = 1
                else:
                    standup = StandupResponse(
                        user_slack_id=slack_id,
                        step=1,
                        date=datetime.now(timezone.utc),
                    )
                    session.add(standup)

                await state_manager.set_state(STANDUP_KEY_STEP.format(slack_id=slack_id), "1", STANDUP_TTL)
                await state_manager.set_state(STANDUP_KEY_DATE.format(slack_id=slack_id), today_str, STANDUP_TTL)

                blocks = [
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": ":wave: *Time for your daily standup.*",
                        },
                    },
                    {
                        "type": "section",
                        "text": {"type": "mrkdwn", "text": "What did you work on *yesterday*?"},
                    },
                    {"type": "divider"},
                ]
                await slack_service.dm_user(
                    slack_id,
                    text="Time for your daily standup. What did you work on yesterday?",
                    blocks=blocks,
                )
                logger.info("Manual standup triggered successfully", extra={"slack_id": slack_id})
                return True

            except Exception:
                logger.exception(f"Failed to manually trigger standup for user {slack_id}")
                await slack_service.dm_user(slack_id, ":x: Failed to start standup. Please try again.")
                return False


# ------------------------------------------------------------------ #
# Response collection (called on each DM)                              #
# ------------------------------------------------------------------ #

async def handle_standup_response(slack_id: str, message: str) -> None:
    """Process an incoming DM as a standup response."""
    try:
        # 1. Get state from Cache
        step_raw = await state_manager.get_state(STANDUP_KEY_STEP.format(slack_id=slack_id))
        
        start_of_day, end_of_day = _today_range()

        async with AsyncSessionLocal() as session:
            async with session.begin():
                # 2. Fetch the current active standup from DB
                result = await session.execute(
                    select(StandupResponse)
                    .where(
                        and_(
                            StandupResponse.user_slack_id == slack_id,
                            StandupResponse.date >= start_of_day,
                            StandupResponse.date <= end_of_day,
                            StandupResponse.is_complete == False
                        )
                    )
                    .order_by(StandupResponse.date.desc())
                )
                standup = result.scalars().first()

                # Recovery: If no cache but we have a DB row, recover state
                if not step_raw and standup:
                    step_raw = str(standup.step)
                
                if not step_raw or not standup:
                    await slack_service.dm_user(
                        slack_id,
                        "I don't have an active standup for you right now. "
                        "Wait for the morning message or use `/standup` to start manually.",
                    )
                    return

                step = int(step_raw)

                if step == 1:
                    standup.yesterday = message
                    standup.step = 2
                    await state_manager.set_state(STANDUP_KEY_STEP.format(slack_id=slack_id), "2", STANDUP_TTL)
                    await slack_service.dm_user(slack_id, ":memo: Got it! What's your plan for *today*?")
                elif step == 2:
                    standup.today = message
                    standup.step = 3
                    await state_manager.set_state(STANDUP_KEY_STEP.format(slack_id=slack_id), "3", STANDUP_TTL)
                    await slack_service.dm_user(
                        slack_id,
                        ":warning: Any *blockers* or impediments? (type 'none' if clear)",
                    )
                elif step == 3:
                    standup.blockers = (
                        None if message.lower() in ("none", "no", "nope", "-") else message
                    )
                    standup.step = 0
                    standup.is_complete = True
                    await state_manager.delete_state(STANDUP_KEY_STEP.format(slack_id=slack_id))
                    await state_manager.delete_state(STANDUP_KEY_DATE.format(slack_id=slack_id))
                    await slack_service.dm_user(
                        slack_id,
                        ":white_check_mark: *Thanks! Your standup has been recorded.* Have a great day!",
                    )
                    logger.info("Standup complete", extra={"slack_id": slack_id})

    except Exception as exc:
        logger.exception("Standup response handling failed", extra={"slack_id": slack_id})
        await slack_service.dm_user(
            slack_id,
            ":x: Something went wrong recording your standup. Please try again.",
        )
        raise StandupAgentError(f"Standup handling failed: {exc}") from exc


# ------------------------------------------------------------------ #
# Summary generation (called by Scheduler)                             #
# ------------------------------------------------------------------ #

async def post_standup_summary() -> None:
    """Collect today's completed standups, generate AI summary, post to channel."""
    start_of_day, end_of_day = _today_range()

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(StandupResponse).where(
                and_(
                    StandupResponse.date >= start_of_day,
                    StandupResponse.date <= end_of_day,
                    StandupResponse.is_complete == True,
                )
            )
        )
        completed = list(result.scalars().all())

        total_result = await session.execute(
            select(func.count())
            .select_from(StandupResponse)
            .where(
                and_(
                    StandupResponse.date >= start_of_day,
                    StandupResponse.date <= end_of_day,
                )
            )
        )
        total_count: int = total_result.scalar_one()

    if not completed:
        logger.warning("No completed standups to summarise today")
        await slack_service.post_to_channel(
            settings.standup_channel,
            ":information_source: No standup responses received today.",
        )
        return

    responses_json = json.dumps(
        [
            {
                "user": r.user_slack_id,
                "yesterday": r.yesterday or "Not provided",
                "today": r.today or "Not provided",
                "blockers": r.blockers or "None",
            }
            for r in completed
        ],
        indent=2,
    )

    try:
        ai_response = await _llm.ainvoke(
            [
                SystemMessage(content=SUMMARY_SYSTEM_PROMPT),
                HumanMessage(content=f"Responses:\n{responses_json}"),
            ]
        )
        summary_text: str = ai_response.content
    except Exception:
        logger.exception("LLM summary generation failed")
        summary_text = "*(Summary generation failed — raw responses available in DB)*"

    date_str = date.today().strftime("%A, %d %B %Y")
    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"Team Standup — {date_str}", "emoji": True},
        },
        {"type": "section", "text": {"type": "mrkdwn", "text": summary_text}},
        {"type": "divider"},
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f"_{len(completed)}/{total_count} team members responded_",
                }
            ],
        },
    ]

    await slack_service.post_to_channel(
        settings.standup_channel,
        text=f"Team Standup Summary — {date_str}",
        blocks=blocks,
    )
    
    async with AsyncSessionLocal() as session:
        async with session.begin():
            summary = StandupSummary(
                date=datetime.now(timezone.utc),
                summary_text=summary_text,
                channel_id=settings.standup_channel,
                responded_count=len(completed),
                total_count=total_count,
            )
            session.add(summary)
    
    logger.info("Standup summary posted", extra={"responded": len(completed)})
