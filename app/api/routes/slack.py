import asyncio
import logging
import re
import json

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from slack_bolt.async_app import AsyncApp
from slack_bolt.request.async_request import AsyncBoltRequest

from app.config import settings
from app.agents import intent_router
from app.agents.intent_router import Intent
from app.agents import (
    standup_agent,
    policy_agent,
    leave_agent,
    onboarding_agent,
    general_chat_agent,
    kudos_agent,
)
from app.services.slack_service import slack_service
from app.utils.logger import get_logger
from app.utils.state import state_manager

logger = get_logger(__name__)

# ------------------------------------------------------------------ #
# Slack Bolt App Configuration                                         #
# ------------------------------------------------------------------ #

bolt_app = AsyncApp(
    token=settings.slack_bot_token,
    signing_secret=settings.slack_signing_secret,
    # We handle the background processing manually to ensure reliability
    process_before_response=False, 
)

router = APIRouter(tags=["slack"])

_EVENT_DEDUPE_TTL_SECONDS = 300


async def _event_already_seen(event_id: str) -> bool:
    key = f"slack_event:{event_id}"
    was_set = await state_manager.set_if_not_exists(key, "1", _EVENT_DEDUPE_TTL_SECONDS)
    return not was_set


@router.post("/slack/events")
async def slack_events(req: Request) -> Response:
    """
    The Ultimate Resilient Bridge.
    Instantly consumes the request and responds to Slack, 
    then processes the data in an isolated background task.
    """
    # 1. Capture all raw data immediately to avoid stream consumption issues
    body_bytes = await req.body()
    headers = dict(req.headers)
    
    # 2. Handle URL Verification Handshake (Must be synchronous and foreground)
    if b"url_verification" in body_bytes:
        try:
            body_json = json.loads(body_bytes)
            if body_json.get("type") == "url_verification":
                return JSONResponse(content={"challenge": body_json.get("challenge", "")})
        except Exception:
            pass

    # 3. Handle Deduplication for Event API
    if headers.get("content-type") == "application/json":
        try:
            body_json = json.loads(body_bytes)
            if body_json.get("type") == "event_callback":
                event_id = body_json.get("event_id")
                if event_id and await _event_already_seen(event_id):
                    return Response(status_code=200)
        except Exception:
            pass

    # 4. Dispatch to Bolt in a truly isolated background task
    # We pass the raw bytes and headers so it doesn't depend on the 'req' object
    asyncio.create_task(_dispatch_to_bolt(body_bytes, headers))

    # 5. Acknowledge Slack immediately (within microseconds)
    return Response(status_code=200)


async def _dispatch_to_bolt(body: bytes, headers: dict):
    """
    Isolated background processor.
    Reconstructs the request and dispatches to Bolt agents.
    """
    try:
        # Reconstruct a Bolt-compatible request
        bolt_req = AsyncBoltRequest(body=body.decode("utf-8"), headers=headers)
        
        # Dispatch the request to the Bolt app
        await bolt_app.async_dispatch(bolt_req)
    except Exception:
        logger.exception("Background Slack dispatch failed")


def _spawn_background(coro, task_name: str) -> None:
    async def _runner() -> None:
        try:
            await coro
        except Exception:
            logger.exception("Background task failed", extra={"task": task_name})
    asyncio.create_task(_runner())


# ------------------------------------------------------------------ #
# Slack Event Handlers (Agents)                                        #
# ------------------------------------------------------------------ #

@bolt_app.event("message")
async def handle_dm(event: dict, ack) -> None:
    await ack()
    if event.get("bot_id") or event.get("subtype"):
        return
    slack_id: str = event.get("user", "")
    text: str = event.get("text", "").strip()
    if slack_id and text:
        _spawn_background(_route_dm(slack_id, text), "route_dm")


async def _route_dm(slack_id: str, text: str) -> None:
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
        else:
            answer = await general_chat_agent.reply_general_chat(slack_id, text)
            await slack_service.dm_user(slack_id, answer)
    except Exception:
        logger.exception("DM routing failed")


async def _handle_feedback(slack_id: str, text: str) -> None:
    try:
        await slack_service.post_to_channel(
            settings.hr_private_channel,
            text=f":speech_balloon: *Anonymous Feedback*\n{text}",
            blocks=[{"type": "section", "text": {"type": "mrkdwn", "text": f":speech_balloon: *Anonymous Feedback received:*\n\n{text}"}}]
        )
        await slack_service.dm_user(slack_id, ":white_check_mark: Your feedback has been sent anonymously to HR.")
    except Exception:
        logger.exception("Feedback failed")


@bolt_app.event("team_join")
async def handle_team_join(event: dict, ack) -> None:
    await ack()
    slack_id = event.get("user", {}).get("id")
    if slack_id:
        _spawn_background(onboarding_agent.start_onboarding(slack_id), "onboarding")


# ------------------------------------------------------------------ #
# Slash Commands                                                       #
# ------------------------------------------------------------------ #

@bolt_app.command("/standup")
async def cmd_standup(ack, command) -> None:
    await ack()
    _spawn_background(standup_agent.trigger_standup_for_all(), "manual_standup")


@bolt_app.command("/kudos")
async def cmd_kudos(ack, command) -> None:
    await ack()
    slack_id: str = command["user_id"]
    text: str = command.get("text", "").strip()
    _spawn_background(_run_kudos_command(slack_id, text), "kudos_command")


async def _run_kudos_command(slack_id: str, text: str) -> None:
    result = await kudos_agent.handle_kudos_command(slack_id, text)
    await slack_service.dm_user(slack_id, result)


@bolt_app.command("/policy")
async def cmd_policy(ack, command) -> None:
    await ack()
    slack_id: str = command["user_id"]
    text: str = command.get("text", "").strip()
    if text:
        _spawn_background(policy_agent.answer_policy_question(text, slack_id), "policy_search")


@bolt_app.command("/apply-leave")
async def cmd_apply_leave(ack, command) -> None:
    await ack()
    _spawn_background(leave_agent.start_leave_conversation(command["user_id"]), "apply_leave")


@bolt_app.command("/leave")
async def cmd_leave_alias(ack, command) -> None:
    await ack()
    _spawn_background(leave_agent.start_leave_conversation(command["user_id"]), "leave_alias")


@bolt_app.command("/feedback")
async def cmd_feedback(ack, command) -> None:
    await ack()
    text: str = command.get("text", "").strip()
    if text:
        _spawn_background(_handle_feedback(command["user_id"], text), "feedback_command")


@bolt_app.command("/reminder")
async def cmd_reminder(ack, command) -> None:
    await ack()
    text: str = command.get("text", "").strip()
    if text:
        _spawn_background(_run_reminder_command(command["user_id"], text), "reminder_command")


async def _run_reminder_command(slack_id: str, text: str) -> None:
    try:
        from app.agents.reminder_agent import parse_and_create_reminder
        result = await parse_and_create_reminder(slack_id, text)
        await slack_service.dm_user(slack_id, result)
    except Exception:
        logger.exception("Reminder failed")


# ------------------------------------------------------------------ #
# Interactive Actions                                                  #
# ------------------------------------------------------------------ #

@bolt_app.action("leave_approve")
async def action_leave_approve(ack, body, action) -> None:
    await ack()
    _spawn_background(_process_leave_action(body, action, "leave_approve"), "leave_approve")


@bolt_app.action("leave_reject")
async def action_leave_reject(ack, body, action) -> None:
    await ack()
    _spawn_background(_process_leave_action(body, action, "leave_reject"), "leave_reject")


async def _process_leave_action(body: dict, action: dict, action_id: str) -> None:
    try:
        await leave_agent.handle_leave_action(
            leave_id=int(action["value"]),
            action=action_id,
            manager_slack_id=body["user"]["id"],
            channel_id=body["channel"]["id"],
            message_ts=body["message"]["ts"],
        )
    except Exception:
        logger.exception("Action failed")
