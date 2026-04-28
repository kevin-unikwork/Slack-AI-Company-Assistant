from __future__ import annotations

import json
from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

import redis.asyncio as aioredis
from langchain_openai import ChatOpenAI
from langchain.schema import HumanMessage, SystemMessage
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models.standup import StandupResponse, StandupSummary
from app.db.models.user import User
from app.db.session import AsyncSessionLocal
from app.services.slack_service import slack_service
from app.utils.exceptions import StandupAgentError
from app.utils.logger import get_logger

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
# Redis singleton — one connection per process                         #
# ------------------------------------------------------------------ #

_redis_client: aioredis.Redis | None = None


def _get_redis() -> aioredis.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = aioredis.from_url(settings.redis_url, decode_responses=True)
    return _redis_client


# ------------------------------------------------------------------ #
# Date query helper                                                    #
# ------------------------------------------------------------------ #

def _today_range() -> tuple[datetime, datetime]:
    """Return (start_of_day, end_of_day) as UTC-aware datetimes for today in IST.

    Boundaries are computed in Asia/Kolkata so that a standup created at e.g.
    3:30 AM IST (= 22:00 UTC previous day) is still found when the user replies
    later that same IST morning.
    """
    today_ist = datetime.now(_IST).date()
    start = datetime.combine(today_ist, time.min, tzinfo=_IST).astimezone(timezone.utc)
    end = datetime.combine(today_ist, time.max, tzinfo=_IST).astimezone(timezone.utc)
    return start, end


# ------------------------------------------------------------------ #
# Trigger (called by Celery Beat)                                      #
# ------------------------------------------------------------------ #

async def trigger_standup_for_all() -> int:
    """
    DM every active user to kick off their standup.
    Creates a StandupResponse row for each user.
    Returns the count of users messaged.
    """
    async with AsyncSessionLocal() as session:
        async with session.begin():
            result = await session.execute(select(User).where(User.is_active == True))
            users = list(result.scalars().all())

    r = _get_redis()
    today_str = date.today().isoformat()
    count = 0

    for user in users:
        try:
            async with AsyncSessionLocal() as session:
                async with session.begin():
                    standup = StandupResponse(
                        user_slack_id=user.slack_id,
                        step=1,
                        date=datetime.now(timezone.utc),
                    )
                    session.add(standup)

            await r.setex(STANDUP_KEY_STEP.format(slack_id=user.slack_id), STANDUP_TTL, "1")
            await r.setex(STANDUP_KEY_DATE.format(slack_id=user.slack_id), STANDUP_TTL, today_str)

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
            logger.info("Standup triggered", extra={"slack_id": user.slack_id})

        except Exception:
            logger.exception(
                "Failed to trigger standup for user",
                extra={"slack_id": user.slack_id},
            )

    logger.info("Standup trigger complete", extra={"total_users": count})
    return count


# ------------------------------------------------------------------ #
# Response collection (called on each DM)                              #
# ------------------------------------------------------------------ #

async def handle_standup_response(slack_id: str, message: str) -> None:
    """Process an incoming DM as a standup response."""
    r = _get_redis()
    try:
        step_raw = await r.get(STANDUP_KEY_STEP.format(slack_id=slack_id))
        if not step_raw:
            await slack_service.dm_user(
                slack_id,
                "I don't have an active standup for you right now. "
                "Wait for the morning message or use `/standup` to start manually.",
            )
            return

        step = int(step_raw)

        start_of_day, end_of_day = _today_range()

        async with AsyncSessionLocal() as session:
            async with session.begin():
                result = await session.execute(
                    select(StandupResponse)
                    .where(
                        and_(
                            StandupResponse.user_slack_id == slack_id,
                            StandupResponse.date >= start_of_day,
                            StandupResponse.date <= end_of_day,
                        )
                    )
                    .order_by(StandupResponse.date.desc())
                )
                standup = result.scalars().first()

                if not standup:
                    # Stale Redis key with no matching DB row (e.g. row created on a
                    # previous UTC day before the IST-boundary fix, or DB was down
                    # during trigger). Clear state so the user is not permanently stuck.
                    await r.delete(STANDUP_KEY_STEP.format(slack_id=slack_id))
                    await r.delete(STANDUP_KEY_DATE.format(slack_id=slack_id))
                    await slack_service.dm_user(
                        slack_id,
                        ":wave: Your standup session wasn't found or has expired. "
                        "Use `/standup` to start a fresh one.",
                    )
                    return

                if step == 1:
                    standup.yesterday = message
                    standup.step = 2
                    await r.setex(STANDUP_KEY_STEP.format(slack_id=slack_id), STANDUP_TTL, "2")
                    await slack_service.dm_user(slack_id, ":memo: Got it! What's your plan for *today*?")

                elif step == 2:
                    standup.today = message
                    standup.step = 3
                    await r.setex(STANDUP_KEY_STEP.format(slack_id=slack_id), STANDUP_TTL, "3")
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
                    await r.delete(STANDUP_KEY_STEP.format(slack_id=slack_id))
                    await r.delete(STANDUP_KEY_DATE.format(slack_id=slack_id))
                    await slack_service.dm_user(
                        slack_id,
                        ":white_check_mark: *Thanks! Your standup has been recorded.* Have a great day!",
                    )
                    logger.info("Standup complete", extra={"slack_id": slack_id})

    except StandupAgentError:
        raise
    except Exception as exc:
        logger.exception("Standup response handling failed", extra={"slack_id": slack_id})
        await slack_service.dm_user(
            slack_id,
            ":x: Something went wrong recording your standup. Please try again.",
        )
        raise StandupAgentError(f"Standup handling failed: {exc}") from exc


# ------------------------------------------------------------------ #
# Summary generation (called by Celery Beat ~1 hr after trigger)       #
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

    ts = await slack_service.post_to_channel(
        settings.standup_channel,
        text=f"Team Standup Summary — {date_str}",
        blocks=blocks,
    )

    async with AsyncSessionLocal() as session:
        async with session.begin():
            summary = StandupSummary(
                summary_text=summary_text,
                channel_id=settings.standup_channel,
                responded_count=len(completed),
                total_count=total_count,
                posted_at=datetime.now(timezone.utc),
                date=datetime.now(timezone.utc),
            )
            session.add(summary)

    logger.info(
        "Standup summary posted",
        extra={"responded": len(completed), "total": total_count, "ts": ts},
    )


async def trigger_standup_for_user(slack_id: str) -> None:
    """Manually trigger standup for a single user (used by /standup command)."""
    r = _get_redis()
    today_str = date.today().isoformat()

    async with AsyncSessionLocal() as session:
        async with session.begin():
            standup = StandupResponse(
                user_slack_id=slack_id,
                step=1,
                date=datetime.now(timezone.utc),
            )
            session.add(standup)

    await r.setex(STANDUP_KEY_STEP.format(slack_id=slack_id), STANDUP_TTL, "1")
    await r.setex(STANDUP_KEY_DATE.format(slack_id=slack_id), STANDUP_TTL, today_str)

    blocks = [
        {"type": "section", "text": {"type": "mrkdwn", "text": ":wave: *Standup time!*"}},
        {"type": "section", "text": {"type": "mrkdwn", "text": "What did you work on *yesterday*?"}},
        {"type": "divider"},
    ]
    await slack_service.dm_user(
        slack_id,
        "Standup time! What did you work on yesterday?",
        blocks=blocks,
    )
    logger.info("Manual standup triggered", extra={"slack_id": slack_id})   