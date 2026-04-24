import json
import asyncio
from datetime import datetime, timezone, date

from langchain_openai import ChatOpenAI
from langchain.schema import HumanMessage, SystemMessage
from sqlalchemy import select, and_, func

from app.config import settings
from app.db.session import AsyncSessionLocal
from app.db.models.standup import StandupResponse, StandupSummary
from app.db.models.user import User
from app.services.slack_service import slack_service
from app.utils.logger import get_logger
from app.utils.exceptions import StandupAgentError
from app.utils.state import state_manager

logger = get_logger(__name__)

_llm = ChatOpenAI(model="gpt-4o", temperature=0.3, openai_api_key=settings.openai_api_key)

STANDUP_KEY_STEP = "standup:{slack_id}:step"
STANDUP_KEY_DATE = "standup:{slack_id}:date"
STANDUP_TTL = 60 * 60 * 12  # 12 hours

SUMMARY_SYSTEM_PROMPT = """You are generating a daily standup summary for a software company.
Format the following individual standup responses into a clean, scannable team summary.
Group the updates by the mentioned PROJECT CHANNEL (e.g. #project-alpha).
If an update belongs to #general, put it in a General section.

IMPORTANT: 
- Group by Project Channel first. Format project headers exactly as: <#C12345678> (DO NOT put asterisks around the channel tag).
- List the team members under each project. Format usernames as plain text: Username (DO NOT put asterisks around the username).
- You MUST preserve exact Slack channel tags (e.g. `<#C12345678>`) in the project headers so Slack renders them correctly as clickable links. Do not strip the angle brackets.
- Include the exact Submission Time for each record.
- Make it concise and professional. Use bullet points.
- Highlight any active blockers clearly.
"""


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

    today_str = date.today().isoformat()
    count = 0

    for user in users:
        try:
            # Create DB record
            async with AsyncSessionLocal() as session:
                async with session.begin():
                    standup = StandupResponse(
                        user_slack_id=user.slack_id,
                        step=1,
                    )
                    session.add(standup)

            # Set shared state
            await state_manager.set_state(STANDUP_KEY_STEP.format(slack_id=user.slack_id), "1", STANDUP_TTL)
            await state_manager.set_state(STANDUP_KEY_DATE.format(slack_id=user.slack_id), today_str, STANDUP_TTL)

            # Send DM
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
                    "text": {"type": "mrkdwn", "text": "What did you work on *yesterday*? (Tip: Mention project channels like `#project-alpha` to link your updates!)"},
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

        except Exception as exc:
            logger.exception(
                "Failed to trigger standup for user",
                extra={"slack_id": user.slack_id, "error": str(exc)},
            )

    logger.info("Standup trigger complete", extra={"total_users": count})
    return count


# ------------------------------------------------------------------ #
# Response collection (called on each DM)                              #
# ------------------------------------------------------------------ #

async def handle_standup_response(slack_id: str, message: str) -> None:
    """
    Process an incoming DM as a standup response.
    Routes based on current state step.
    """
    try:
        step_raw = await state_manager.get_state(STANDUP_KEY_STEP.format(slack_id=slack_id))
        if not step_raw:
            await slack_service.dm_user(
                slack_id,
                "I don't have an active standup for you right now. "
                "Wait for the morning message or use `/standup` to start manually.",
            )
            return

        step = int(step_raw)

        async with AsyncSessionLocal() as session:
            async with session.begin():
                today = date.today()
                result = await session.execute(
                    select(StandupResponse)
                    .where(
                        and_(
                            StandupResponse.user_slack_id == slack_id,
                            func.date(StandupResponse.date) == today,
                        )
                    )
                    .order_by(StandupResponse.date.desc())
                )
                standup = result.scalars().first()

                if not standup:
                    raise StandupAgentError(f"No standup row found for {slack_id} today")

                if step == 1:
                    standup.yesterday = message
                    standup.step = 2
                    await state_manager.set_state(STANDUP_KEY_STEP.format(slack_id=slack_id), "2", STANDUP_TTL)
                    await slack_service.dm_user(
                        slack_id,
                        ":memo: Got it! What's your plan for *today*?",
                    )

                elif step == 2:
                    standup.today = message
                    standup.step = 3
                    await state_manager.set_state(STANDUP_KEY_STEP.format(slack_id=slack_id), "3", STANDUP_TTL)
                    await slack_service.dm_user(
                        slack_id,
                        ":warning: Any *blockers* or impediments? (type 'none' if clear)",
                    )

                elif step == 3:
                    standup.blockers = None if message.lower() in ("none", "no", "nope", "-") else message
                    standup.submitted_at = datetime.now(timezone.utc)
                    standup.date = datetime.now(timezone.utc)
                    standup.step = 0
                    standup.is_complete = True
                    
                    # Extract structured multi-project data
                    try:
                        from pydantic import BaseModel, Field
                        class ProjectUpdate(BaseModel):
                            channel_name: str = Field(description="The exact Slack channel tag (e.g. <#C12345678>). You MUST preserve the angle brackets.")
                            yesterday: str
                            today: str
                            blockers: str

                        class MultiProjectStandup(BaseModel):
                            updates: list[ProjectUpdate]

                        structured_llm = _llm.with_structured_output(MultiProjectStandup)
                        extraction_prompt = f"""Extract the project updates from the following standup response.
The user is working on one or multiple projects. Identify each project based on any channel mentions.
If no channel is mentioned, use '#general'. 
You MUST preserve the exact Slack channel tags (e.g. `<#C12345678|name>` or `<#C12345678>`) exactly as they appear in the text, so Slack can render them. Do NOT strip the angle brackets `< >`.
Yesterday: {standup.yesterday}
Today: {standup.today}
Blockers: {standup.blockers}"""
                        
                        extracted = await structured_llm.ainvoke(extraction_prompt)
                        if extracted and extracted.updates:
                            standup.projects_data = [u.model_dump() for u in extracted.updates]
                        else:
                            standup.projects_data = []
                    except Exception as ext_err:
                        logger.warning(f"Failed to extract structured standup data: {ext_err}")
                        standup.projects_data = []
                    # Clear shared state
                    await state_manager.delete_state(STANDUP_KEY_STEP.format(slack_id=slack_id))
                    await state_manager.delete_state(STANDUP_KEY_DATE.format(slack_id=slack_id))
                    await slack_service.dm_user(
                        slack_id,
                        ":white_check_mark: *Thanks! Your standup has been recorded.* Have a great day!",
                    )
                    logger.info("Standup complete", extra={"slack_id": slack_id})
                    
                    # NEW: Notify Project Manager immediately
                    asyncio.create_task(notify_pm_of_standup_completion(slack_id))

    except StandupAgentError:
        raise
    except Exception as exc:
        logger.exception("Standup response handling failed", extra={"slack_id": slack_id})
        await slack_service.dm_user(
            slack_id,
            ":x: Something went wrong recording your standup. Please try again or contact your manager.",
        )
        raise StandupAgentError(f"Standup handling failed: {exc}") from exc


async def notify_pm_of_standup_completion(slack_id: str) -> None:
    """
    Triggered immediately when a user finishes their standup.
    Generates an individual summary and DMs the Project Manager.
    """
    try:
        async with AsyncSessionLocal() as session:
            # 1. Fetch user and their manager
            res = await session.execute(select(User).where(User.slack_id == slack_id))
            user = res.scalar_one_or_none()
            if not user or not user.manager_slack_id:
                return

            # 2. Check if manager is a Project Manager
            mgr_res = await session.execute(select(User).where(User.slack_id == user.manager_slack_id))
            manager = mgr_res.scalar_one_or_none()
            if not manager or not manager.is_project_manager:
                return

            # 3. Fetch today's completed standup for this user
            today = date.today()
            standup_res = await session.execute(
                select(StandupResponse).where(
                    and_(
                        StandupResponse.user_slack_id == slack_id,
                        func.date(StandupResponse.date) == today,
                        StandupResponse.is_complete == True
                    )
                ).order_by(StandupResponse.id.desc())
            )
            standup = standup_res.scalars().first()
            if not standup:
                return

            # 4. Generate summary
            from datetime import timedelta
            submission_time = standup.submitted_at or standup.date
            ist_time = submission_time + timedelta(hours=5, minutes=30)
            formatted_time = ist_time.strftime("%I:%M %p (IST)")
            
            responses_json = json.dumps([{
                "user": user.slack_username,
                "projects_data": standup.projects_data,
                "yesterday": standup.yesterday,
                "today": standup.today,
                "blockers": standup.blockers or "None",
                "submitted_at": formatted_time
            }])

            summary_text = await _generate_ai_summary(responses_json, is_private=True)
            
            blocks = [
                {
                    "type": "header",
                    "text": {"type": "plain_text", "text": "🔔 New Work Record Received", "emoji": True},
                },
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": f"*Employee:* <@{slack_id}>\n*Time:* {formatted_time}"},
                },
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": summary_text},
                },
            ]

            await slack_service.dm_user(
                manager.slack_id,
                text=f"New standup record from {user.slack_username}",
                blocks=blocks
            )
            logger.info("PM notified of individual standup", extra={"pm": manager.slack_id, "user": slack_id})

    except Exception:
        logger.exception("Failed to notify PM of individual standup", extra={"user": slack_id})


# ------------------------------------------------------------------ #
# Summary generation (called by Celery Beat ~1 hr after trigger)       #
# ------------------------------------------------------------------ #

async def post_standup_summary() -> None:
    """
    Collect today's completed standups, generate AI summaries, and distribute:
    1. A global summary to the team channel.
    2. Private, group-specific summaries to each Project Manager for their direct reports.
    """
    async with AsyncSessionLocal() as session:
        today = date.today()
        # Join with User to get manager info
        result = await session.execute(
            select(StandupResponse, User)
            .join(User, StandupResponse.user_slack_id == User.slack_id)
            .where(
                and_(
                    func.date(StandupResponse.date) == today,
                    StandupResponse.is_complete == True,
                )
            )
        )
        completed_data = list(result.all())

        total_result = await session.execute(
            select(func.count()).select_from(StandupResponse).where(
                func.date(StandupResponse.date) == today
            )
        )
        total_count: int = total_result.scalar_one()

    if not completed_data:
        logger.warning("No completed standups to summarise today")
        await slack_service.post_to_channel(
            settings.standup_channel,
            ":information_source: No standup responses received today.",
        )
        return

    from datetime import timedelta
    global_responses_json = json.dumps([
        {
            "user": u.slack_username,
            "projects_data": r.projects_data,
            "yesterday": r.yesterday or "Not provided",
            "today": r.today or "Not provided",
            "blockers": r.blockers or "None",
            "submitted_at": ((r.submitted_at or r.date) + timedelta(hours=5, minutes=30)).strftime("%I:%M %p (IST)")
        }
        for r, u in completed_data
    ], indent=2)

    global_summary_text = await _generate_ai_summary(global_responses_json)
    
    date_str = today.strftime("%A, %d %B %Y")
    await _post_global_summary(global_summary_text, date_str, len(completed_data), total_count)

    # 2. Generate and Send Private PM Summaries
    # Group by manager_slack_id
    manager_groups: dict[str, list[tuple[StandupResponse, User]]] = {}
    for r, u in completed_data:
        mgr_id = u.manager_slack_id
        if mgr_id:
            if mgr_id not in manager_groups:
                manager_groups[mgr_id] = []
            manager_groups[mgr_id].append((r, u))

    for mgr_id, reports in manager_groups.items():
        # Check if the manager is actually a Project Manager
        async with AsyncSessionLocal() as session:
            res = await session.execute(select(User).where(User.slack_id == mgr_id))
            manager_user = res.scalar_one_or_none()
            
            if manager_user and manager_user.is_project_manager:
                logger.info(f"Generating private summary for PM: {manager_user.slack_username}")
                
                reports_json = json.dumps([
                    {
                        "user": u.slack_username,
                        "projects_data": r.projects_data,
                        "yesterday": r.yesterday or "Not provided",
                        "today": r.today or "Not provided",
                        "blockers": r.blockers or "None",
                        "submitted_at": ((r.submitted_at or r.date) + timedelta(hours=5, minutes=30)).strftime("%I:%M %p (IST)")
                    }
                    for r, u in reports
                ], indent=2)
                
                pm_summary = await _generate_ai_summary(reports_json, is_private=True)
                
                blocks = [
                    {
                        "type": "header",
                        "text": {"type": "plain_text", "text": f"📈 Project Team Update — {date_str}", "emoji": True},
                    },
                    {
                        "type": "section",
                        "text": {"type": "mrkdwn", "text": pm_summary},
                    },
                    {"type": "divider"},
                    {
                        "type": "context",
                        "elements": [
                            {
                                "type": "mrkdwn",
                                "text": f"_You have {len(reports)} reports included in this summary._",
                            }
                        ],
                    },
                ]
                
                await slack_service.dm_user(
                    mgr_id,
                    text=f"Project Team Standup Summary — {date_str}",
                    blocks=blocks
                )

    # Persist the global summary record
    async with AsyncSessionLocal() as session:
        async with session.begin():
            summary = StandupSummary(
                summary_text=global_summary_text,
                channel_id=settings.standup_channel,
                responded_count=len(completed_data),
                total_count=total_count,
                posted_at=datetime.now(timezone.utc),
            )
            session.add(summary)


async def _generate_ai_summary(responses_json: str, is_private: bool = False) -> str:
    """Helper to call LLM for summary generation."""
    system_prompt = SUMMARY_SYSTEM_PROMPT
    if is_private:
        system_prompt += "\nThis is a private report for a Project Manager. Focus on delivery risks and blockers."

    try:
        ai_response = await _llm.ainvoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content=f"Responses:\n{responses_json}"),
        ])
        # Force Slack markdown by removing double asterisks
        return ai_response.content.replace("**", "*")
    except Exception as exc:
        logger.exception("LLM summary generation failed")
        return "*(Summary generation failed — raw responses available in DB)*"


async def _post_global_summary(summary_text: str, date_str: str, responded: int, total: int):
    """Helper to post the public team summary."""
    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"📋 Team Standup — {date_str}", "emoji": True},
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": summary_text},
        },
        {"type": "divider"},
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f"_{responded}/{total} team members responded_",
                }
            ],
        },
    ]

    await slack_service.post_to_channel(
        settings.standup_channel,
        text=f"Team Standup Summary — {date_str}",
        blocks=blocks,
    )


async def trigger_standup_for_user(slack_id: str) -> None:
    """Manually trigger standup for a single user (used by /standup command)."""
    today_str = date.today().isoformat()

    async with AsyncSessionLocal() as session:
        async with session.begin():
            standup = StandupResponse(user_slack_id=slack_id, step=1)
            session.add(standup)

    await state_manager.set_state(STANDUP_KEY_STEP.format(slack_id=slack_id), "1", STANDUP_TTL)
    await state_manager.set_state(STANDUP_KEY_DATE.format(slack_id=slack_id), today_str, STANDUP_TTL)

    blocks = [
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": ":wave: *Standup time!*"},
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": "What did you work on *yesterday*? (Tip: Mention project channels like `#project-alpha` to link your updates!)"},
        },
        {"type": "divider"},
    ]
    await slack_service.dm_user(
        slack_id,
        "Standup time! What did you work on yesterday?",
        blocks=blocks,
    )
    logger.info("Manual standup triggered", extra={"slack_id": slack_id})