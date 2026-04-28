import asyncio
import logging
import time
import re

import redis.asyncio as aioredis
from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from slack_bolt.async_app import AsyncApp
from slack_bolt.adapter.fastapi.async_handler import AsyncSlackRequestHandler

from app.config import settings
from app.agents import intent_router
from app.agents.intent_router import Intent
from app.agents import standup_agent, policy_agent, leave_agent, onboarding_agent, general_chat_agent
from app.agents.broadcast_agent import send_broadcast
from app.db.models.user import User
from app.db.session import AsyncSessionLocal
from app.services.slack_service import slack_service
from app.utils.exceptions import AuthorizationError
from app.utils.logger import get_logger
from sqlalchemy import select

logger = get_logger(__name__)

# ------------------------------------------------------------------ #
# Slack Bolt App                                                       #
# NOTE: Do NOT set process_before_response=True when handlers call    #
# await ack() explicitly — it causes double-ack errors.               #
# ------------------------------------------------------------------ #

bolt_app = AsyncApp(
    token=settings.slack_bot_token,
    signing_secret=settings.slack_signing_secret,
)

handler = AsyncSlackRequestHandler(bolt_app)

router = APIRouter(tags=["slack"])

_EVENT_DEDUPE_TTL_SECONDS = 300  # 5 minutes


def _get_redis() -> aioredis.Redis:
    return aioredis.from_url(settings.redis_url, decode_responses=True)


async def _event_already_seen(event_id: str) -> bool:
    """Redis-backed event deduplication. Falls back to allow-all on Redis error."""
    try:
        r = _get_redis()
        key = f"slack_event:{event_id}"
        result = await r.set(key, "1", ex=_EVENT_DEDUPE_TTL_SECONDS, nx=True)
        # nx=True means set only if Not eXists; returns True if set, None if already existed
        return result is None
    except Exception:
        logger.warning("Redis dedup check failed, allowing event through", extra={"event_id": event_id})
        return False


def _spawn_background(coro, task_name: str) -> None:
    async def _runner() -> None:
        try:
            await coro
        except Exception:
            logger.exception("Background task failed", extra={"task": task_name})

    asyncio.create_task(_runner())


@router.post("/slack/events")
async def slack_events(req: Request) -> Response:
    """Single endpoint for all Slack Events API and interaction payloads."""
    # Slack retries timed-out events; return 200 immediately so Slack stops retrying.
    if req.headers.get("x-slack-retry-num"):
        logger.warning(
            "Ignoring Slack retry delivery",
            extra={
                "retry_num": req.headers.get("x-slack-retry-num"),
                "retry_reason": req.headers.get("x-slack-retry-reason"),
            },
        )
        return JSONResponse(content={"ok": True})

    try:
        body = await req.json()
        if isinstance(body, dict):
            # URL verification handshake — respond with challenge immediately
            if body.get("type") == "url_verification":
                return JSONResponse(content={"challenge": body.get("challenge", "")})

            # Idempotency guard — use Redis so it survives restarts
            if body.get("type") == "event_callback":
                event_id = body.get("event_id")
                if isinstance(event_id, str) and event_id:
                    if await _event_already_seen(event_id):
                        logger.warning("Duplicate Slack event ignored", extra={"event_id": event_id})
                        return JSONResponse(content={"ok": True})
    except Exception:
        # Non-JSON body (slash commands, interactive payloads) — pass to Bolt
        pass

    return await handler.handle(req)


# ------------------------------------------------------------------ #
# Direct Messages                                                      #
# ------------------------------------------------------------------ #

@bolt_app.event("message")
async def handle_dm(event: dict, say, ack) -> None:
    """Route all DMs to the correct agent based on intent."""
    await ack()

    # Ignore bot messages and subtypes (edited, deleted, etc.)
    if event.get("bot_id") or event.get("subtype"):
        return

    slack_id: str = event.get("user", "")
    text: str = event.get("text", "").strip()

    if not slack_id or not text:
        return

    logger.info("DM received", extra={"slack_id": slack_id, "text_preview": text[:80]})
    _spawn_background(_route_dm(slack_id, text), "route_dm")


async def _route_dm(slack_id: str, text: str) -> None:
    """Background task: classify intent and dispatch to the right agent."""
    try:
        intent = await intent_router.classify_intent(slack_id, text)

        if intent == Intent.STANDUP_RESPONSE:
            await standup_agent.handle_standup_response(slack_id, text)

        elif intent == Intent.POLICY_QA:
            await slack_service.dm_user(slack_id, ":hourglass: Looking that up in our policy documents...")
            answer = await policy_agent.answer_policy_question(text, slack_id)
            await slack_service.dm_user(slack_id, answer)

        elif intent == Intent.LEAVE_REQUEST:
            await leave_agent.handle_leave_message(slack_id, text)

        elif intent == Intent.FEEDBACK:
            await _handle_feedback(slack_id, text)

        else:  # GENERAL_CHAT
            answer = await general_chat_agent.reply_general_chat(slack_id, text)
            await slack_service.dm_user(slack_id, answer)

    except Exception:
        logger.exception("DM routing failed", extra={"slack_id": slack_id})
        try:
            await slack_service.dm_user(
                slack_id,
                ":x: Something went wrong processing your message. Please try again in a moment.",
            )
        except Exception:
            pass


async def _handle_feedback(slack_id: str, text: str) -> None:
    """Post anonymous feedback to the HR private channel."""
    try:
        await slack_service.post_to_channel(
            settings.hr_private_channel,
            text=f":speech_balloon: *Anonymous Feedback*\n{text}",
            blocks=[
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f":speech_balloon: *Anonymous Employee Feedback*\n\n{text}",
                    },
                },
                {
                    "type": "context",
                    "elements": [{"type": "mrkdwn", "text": "_Submitted anonymously via bot_"}],
                },
            ],
        )
        await slack_service.dm_user(
            slack_id,
            ":white_check_mark: Your feedback has been submitted *anonymously* to HR. Thank you!",
        )
        logger.info("Anonymous feedback submitted", extra={"channel": settings.hr_private_channel})
    except Exception:
        logger.exception("Feedback submission failed", extra={"slack_id": slack_id})
        await slack_service.dm_user(
            slack_id, ":x: Failed to submit your feedback. Please try again."
        )


# ------------------------------------------------------------------ #
# Team Join (new member onboarding)                                    #
# ------------------------------------------------------------------ #

@bolt_app.event("team_join")
async def handle_team_join(event: dict, ack) -> None:
    """Welcome new workspace members."""
    await ack()
    user = event.get("user", {})
    slack_id: str = user.get("id", "") if isinstance(user, dict) else str(user)
    if not slack_id:
        return
    logger.info("team_join event received", extra={"slack_id": slack_id})
    _spawn_background(onboarding_agent.onboard_new_member(slack_id), "onboard_new_member")


# ------------------------------------------------------------------ #
# App Mention                                                          #
# ------------------------------------------------------------------ #

@bolt_app.event("app_mention")
async def handle_mention(event: dict, say, ack) -> None:
    """Handle @bot mentions in channels."""
    await ack()
    slack_id: str = event.get("user", "")
    text: str = event.get("text", "")
    # Strip the mention tag
    clean_text = " ".join(w for w in text.split() if not w.startswith("<@"))
    if clean_text.strip():
        _spawn_background(_route_dm(slack_id, clean_text.strip()), "route_mention_dm")
    else:
        await say("Hi! Mention me with a question, e.g. `@Bot What is the leave policy?`")


# ------------------------------------------------------------------ #
# Slash Commands                                                       #
# ------------------------------------------------------------------ #

@bolt_app.command("/standup")
async def cmd_standup(ack, command) -> None:
    """Manually trigger standup for the invoking user."""
    await ack()
    slack_id: str = command["user_id"]
    _spawn_background(standup_agent.trigger_standup_for_user(slack_id), "trigger_standup_for_user")


@bolt_app.command("/policy")
async def cmd_policy(ack, command) -> None:
    """Answer a policy question inline."""
    await ack()
    slack_id: str = command["user_id"]
    question: str = command.get("text", "").strip()
    if not question:
        await slack_service.dm_user(slack_id, "Please include a question: `/policy What is the WFH policy?`")
        return
    _spawn_background(_answer_policy_command(slack_id, question), "policy_command")


async def _answer_policy_command(slack_id: str, question: str) -> None:
    try:
        await slack_service.dm_user(slack_id, ":hourglass: Looking that up...")
        answer = await policy_agent.answer_policy_question(question, slack_id)
        await slack_service.dm_user(slack_id, answer)
    except Exception:
        logger.exception("Policy command failed", extra={"slack_id": slack_id})
        await slack_service.dm_user(slack_id, ":x: Failed to retrieve policy information.")


@bolt_app.command("/announce")
async def cmd_announce(ack, command) -> None:
    """HR-only: broadcast an announcement to all employees."""
    await ack()
    slack_id: str = command["user_id"]
    message: str = command.get("text", "").strip()
    if not message:
        await slack_service.dm_user(slack_id, "Usage: `/announce Your message here`")
        return
    _spawn_background(_run_broadcast_command(slack_id, message), "broadcast_command")


async def _run_broadcast_command(slack_id: str, message: str) -> None:
    try:
        async with AsyncSessionLocal() as session:
            async with session.begin():
                result = await session.execute(select(User).where(User.slack_id == slack_id))
                user = result.scalar_one_or_none()
                if not user:
                    await slack_service.dm_user(slack_id, ":x: Your user account was not found.")
                    return
                result_data = await send_broadcast(session, slack_id, message, user)
        await slack_service.dm_user(
            slack_id,
            f":white_check_mark: Announcement sent to *{result_data['sent']}* employees "
            f"({result_data['failed']} failed).",
        )
    except AuthorizationError:
        await slack_service.dm_user(slack_id, ":no_entry: Only HR admins can send announcements.")
    except Exception:
        logger.exception("Broadcast command failed", extra={"slack_id": slack_id})
        await slack_service.dm_user(slack_id, ":x: Failed to send announcement.")


@bolt_app.command("/applyleave")
async def cmd_leave(ack, command) -> None:
    """Start a leave request conversation."""
    await ack()
    slack_id: str = command["user_id"]
    _spawn_background(leave_agent.start_leave_conversation(slack_id), "start_leave_conversation")


@bolt_app.command("/leave")
async def cmd_leave_alias(ack, command) -> None:
    """Optional alias for workspaces where /leave is available."""
    await ack()
    slack_id: str = command["user_id"]
    _spawn_background(leave_agent.start_leave_conversation(slack_id), "start_leave_conversation_alias")


@bolt_app.command("/feedback")
async def cmd_feedback(ack, command) -> None:
    """Submit anonymous feedback."""
    await ack()
    slack_id: str = command["user_id"]
    text: str = command.get("text", "").strip()
    if not text:
        await slack_service.dm_user(slack_id, "Usage: `/feedback Your feedback here`")
        return
    _spawn_background(_handle_feedback(slack_id, text), "feedback_command")


@bolt_app.command("/reminder")
async def cmd_reminder(ack, command) -> None:
    """Set a natural-language reminder."""
    await ack()
    slack_id: str = command["user_id"]
    text: str = command.get("text", "").strip()
    if not text:
        await slack_service.dm_user(
            slack_id,
            "Usage: `/reminder me in 2 hours to review the PR`",
        )
        return
    _spawn_background(_run_reminder_command(slack_id, text), "reminder_command")


async def _run_reminder_command(slack_id: str, text: str) -> None:
    try:
        from app.agents.reminder_agent import parse_and_create_reminder

        result = await parse_and_create_reminder(slack_id, text)
        await slack_service.dm_user(slack_id, result)
    except Exception:
        logger.exception("Reminder command failed", extra={"slack_id": slack_id})
        await slack_service.dm_user(slack_id, ":x: Failed to set reminder. Please try again.")


# ------------------------------------------------------------------ #
# Celebrations (HR Admin)                                              #
# ------------------------------------------------------------------ #

def _parse_target_and_date(text: str) -> tuple[str | None, str | None]:
    """
    Parse slash text for target user and date (YYYY-MM-DD).
    Supports:
    - <@U12345>
    - @username
    - unicode display names (everything before date)
    """
    text = (text or "").strip()
    date_match = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", text)
    if not date_match:
        return None, None

    date_str = date_match.group(1)
    target_part = text[:date_match.start()].strip()
    if not target_part:
        return None, date_str

    # Slack mention format: <@U123ABC|optional_name>
    mention_match = re.search(r"<@([A-Za-z0-9]+)(?:\|[^>]+)?>", target_part)
    if mention_match:
        return mention_match.group(1), date_str

    # Plain @username or free-form name
    target = target_part.lstrip("@").strip()
    return (target or None), date_str


@bolt_app.command("/setbirthday")
async def cmd_setbirthday(ack, command) -> None:
    """HR Admin: Set a user's birthday. Usage: /setbirthday @user YYYY-MM-DD"""
    await ack()
    slack_id: str = command["user_id"]
    text: str = command.get("text", "").strip()
    _spawn_background(_run_setbirthday(slack_id, text), "setbirthday_command")


async def _run_setbirthday(slack_id: str, text: str) -> None:
    try:
        from app.agents.celebration_agent import set_user_birthday

        target_user, date_str = _parse_target_and_date(text)
        if not target_user or not date_str:
            await slack_service.dm_user(
                slack_id,
                "Usage: `/setbirthday @user 1995-06-15`",
            )
            return

        result = await set_user_birthday(slack_id, target_user, date_str)
        await slack_service.dm_user(slack_id, result)
    except Exception:
        logger.exception("Set birthday command failed", extra={"slack_id": slack_id})
        await slack_service.dm_user(slack_id, ":x: Failed to set birthday. Please try again.")


@bolt_app.command("/setanniversary")
async def cmd_setanniversary(ack, command) -> None:
    """HR Admin: Set a user's join date. Usage: /setanniversary @user YYYY-MM-DD"""
    await ack()
    slack_id: str = command["user_id"]
    text: str = command.get("text", "").strip()
    _spawn_background(_run_setanniversary(slack_id, text), "setanniversary_command")


async def _run_setanniversary(slack_id: str, text: str) -> None:
    try:
        from app.agents.celebration_agent import set_user_anniversary

        target_user, date_str = _parse_target_and_date(text)
        if not target_user or not date_str:
            await slack_service.dm_user(
                slack_id,
                "Usage: `/setanniversary @user 2023-01-10`",
            )
            return

        result = await set_user_anniversary(slack_id, target_user, date_str)
        await slack_service.dm_user(slack_id, result)
    except Exception:
        logger.exception("Set anniversary command failed", extra={"slack_id": slack_id})
        await slack_service.dm_user(slack_id, ":x: Failed to set anniversary date. Please try again.")


# ------------------------------------------------------------------ #
# Interactive Actions (button clicks)                                  #
# ------------------------------------------------------------------ #

@bolt_app.action("leave_approve")
async def action_leave_approve(ack, body, action) -> None:
    await ack()
    _spawn_background(_process_leave_action(body, action, "leave_approve"), "leave_approve_action")


@bolt_app.action("leave_reject")
async def action_leave_reject(ack, body, action) -> None:
    await ack()
    _spawn_background(_process_leave_action(body, action, "leave_reject"), "leave_reject_action")


async def _process_leave_action(body: dict, action: dict, action_id: str) -> None:
    try:
        leave_id = int(action["value"])
        manager_slack_id: str = body["user"]["id"]
        channel_id: str = body["channel"]["id"]
        message_ts: str = body["message"]["ts"]
        await leave_agent.handle_leave_action(
            leave_id=leave_id,
            action=action_id,
            manager_slack_id=manager_slack_id,
            channel_id=channel_id,
            message_ts=message_ts,
        )
    except Exception:
        logger.exception("Leave action handler failed", extra={"action": action_id})
