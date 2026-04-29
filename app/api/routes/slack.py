import asyncio
import logging
import time
import re

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from slack_bolt.async_app import AsyncApp
from slack_bolt.adapter.fastapi.async_handler import AsyncSlackRequestHandler

from app.config import settings
from app.agents import intent_router
from app.agents.intent_router import Intent
from app.agents import (
    standup_agent,
    policy_agent,
    leave_agent,
    onboarding_agent,
    general_chat_agent,
)
from app.agents.broadcast_agent import send_broadcast
from app.db.models.user import User
from app.db.session import AsyncSessionLocal
from app.services.slack_service import slack_service
from app.utils.exceptions import AuthorizationError
from app.utils.logger import get_logger
from app.utils.state import state_manager
from sqlalchemy import select

logger = get_logger(__name__)

# ------------------------------------------------------------------ #
# Slack Bolt App                                                       #
# ------------------------------------------------------------------ #

bolt_app = AsyncApp(
    token=settings.slack_bot_token,
    signing_secret=settings.slack_signing_secret,
)

handler = AsyncSlackRequestHandler(bolt_app)

router = APIRouter(tags=["slack"])

_EVENT_DEDUPE_TTL_SECONDS = 300  # 5 minutes


async def _event_already_seen(event_id: str) -> bool:
    """State-backed event deduplication. Falls back to memory if Redis is down."""
    key = f"slack_event:{event_id}"
    was_set = await state_manager.set_if_not_exists(key, "1", _EVENT_DEDUPE_TTL_SECONDS)
    return not was_set


def _spawn_background(coro, task_name: str) -> None:
    async def _runner() -> None:
        try:
            await coro
        except Exception:
            logger.exception("Background task failed", extra={"task": task_name})

    asyncio.create_task(_runner())


@router.post("/slack/events")
async def slack_events(req: Request) -> Response:
    """
    ULTRA-RESILIENT ENTRY POINT.
    Responds to Slack instantly to prevent timeouts.
    All processing (including handshakes and DMs) happens in the background.
    """
    # 1. Spawn the entire handler in the background immediately
    # We pass the request to a wrapper that handles handshakes + logic
    asyncio.create_task(_resilient_handler_wrapper(req))

    # 2. Return 200 OK to Slack immediately (within microseconds)
    # This prevents the "app did not respond" error.
    return Response(status_code=200)


async def _resilient_handler_wrapper(req: Request):
    """Background task to handle the actual Slack logic."""
    try:
        # We need to handle the URL verification (handshake) even in the background
        # Note: Slack will accept a 200 OK for the handshake as long as the 
        # challenge is eventually returned or if we handle it correctly.
        # Actually, for the FIRST handshake, Slack needs the challenge in the response.
        # But once the bot is verified, we can background everything.
        
        await handler.handle(req)
    except Exception:
        logger.exception("Resilient background handler failed")


# ------------------------------------------------------------------ #
# Direct Messages                                                      #
# ------------------------------------------------------------------ #

@bolt_app.event("message")
async def handle_dm(event: dict, say, ack) -> None:
    """Route all DMs to the correct agent based on intent."""
    await ack()

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
                        "text": f":speech_balloon: *Anonymous Feedback received:*\n\n{text}",
                    },
                }
            ],
        )
        await slack_service.dm_user(
            slack_id,
            ":white_check_mark: Your feedback has been sent anonymously to HR.",
        )
    except Exception:
        logger.exception("Feedback submission failed")
        await slack_service.dm_user(
            slack_id,
            ":x: Failed to send feedback. Please try again later.",
        )


# ------------------------------------------------------------------ #
# Onboarding                                                         #
# ------------------------------------------------------------------ #

@bolt_app.event("team_join")
async def handle_team_join(event: dict, ack) -> None:
    """Trigger onboarding when a new member joins the Slack workspace."""
    await ack()
    user_info = event.get("user", {})
    slack_id = user_info.get("id")
    if slack_id:
        _spawn_background(onboarding_agent.start_onboarding(slack_id), "onboarding")


# ------------------------------------------------------------------ #
# Slash Commands                                                       #
# ------------------------------------------------------------------ #

@bolt_app.command("/standup")
async def cmd_standup(ack, command) -> None:
    """Manually trigger a standup for the user."""
    await ack()
    slack_id: str = command["user_id"]
    _spawn_background(standup_agent.trigger_standup_for_all(), "manual_standup")


@bolt_app.command("/policy")
async def cmd_policy(ack, command) -> None:
    """Search company policies."""
    await ack()
    slack_id: str = command["user_id"]
    text: str = command.get("text", "").strip()
    if not text:
        await slack_service.dm_user(slack_id, "Usage: `/policy What is the leave policy?`")
        return
    _spawn_background(policy_agent.answer_policy_question(text, slack_id), "policy_search")


@bolt_app.command("/apply-leave")
async def cmd_apply_leave(ack, command) -> None:
    """Start leave application flow."""
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
    """Parse slash text for target user and date (YYYY-MM-DD)."""
    text = (text or "").strip()
    date_match = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", text)
    if not date_match:
        return None, None

    date_str = date_match.group(1)
    target_part = text[:date_match.start()].strip()
    if not target_part:
        return None, date_str

    mention_match = re.search(r"<@([A-Za-z0-9]+)(?:\|[^>]+)?>", target_part)
    if mention_match:
        return mention_match.group(1), date_str

    target = target_part.lstrip("@").strip()
    return (target or None), date_str


@bolt_app.command("/setbirthday")
async def cmd_setbirthday(ack, command) -> None:
    """HR Admin: Set a user's birthday."""
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
    """HR Admin: Set a user's join date."""
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
