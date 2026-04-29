import asyncio
import logging
import re
import json

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from slack_bolt.async_app import AsyncApp
from slack_bolt.request.async_request import AsyncBoltRequest

from app.config import settings
from app.agents import (
    intent_router,
    standup_agent,
    policy_agent,
    leave_agent,
    onboarding_agent,
    general_chat_agent,
    kudos_agent,
    broadcast_agent,
)
from app.agents.intent_router import Intent
from app.db.models.user import User
from app.db.session import AsyncSessionLocal
from app.services.slack_service import slack_service
from app.utils.logger import get_logger
from app.utils.state import state_manager
from sqlalchemy import select

logger = get_logger(__name__)

# ------------------------------------------------------------------ #
# Slack Bolt App Configuration                                         #
# ------------------------------------------------------------------ #

bolt_app = AsyncApp(
    token=settings.slack_bot_token,
    signing_secret=settings.slack_signing_secret,
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
    """Zero-Dependency Proxy Entry Point."""
    body_bytes = await req.body()
    headers = dict(req.headers)
    
    # Handshake
    if b"url_verification" in body_bytes:
        try:
            body_json = json.loads(body_bytes)
            if body_json.get("type") == "url_verification":
                return JSONResponse(content={"challenge": body_json.get("challenge", "")})
        except Exception:
            pass

    # Deduplication
    if headers.get("content-type") == "application/json":
        try:
            body_json = json.loads(body_bytes)
            if body_json.get("type") == "event_callback":
                event_id = body_json.get("event_id")
                if event_id and await _event_already_seen(event_id):
                    return Response(status_code=200)
        except Exception:
            pass

    # Isolated background processing
    asyncio.create_task(_dispatch_to_bolt(body_bytes, headers))
    return Response(status_code=200)


async def _dispatch_to_bolt(body: bytes, headers: dict):
    try:
        bolt_req = AsyncBoltRequest(body=body.decode("utf-8"), headers=headers)
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
# Direct Messages & Events                                             #
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
            await _handle_feedback_logic(slack_id, text)
        else:
            answer = await general_chat_agent.reply_general_chat(slack_id, text)
            await slack_service.dm_user(slack_id, answer)
    except Exception:
        logger.exception("DM routing failed")


async def _handle_feedback_logic(slack_id: str, text: str) -> None:
    try:
        await slack_service.post_to_channel(
            settings.hr_private_channel,
            text=f":speech_balloon: *Anonymous Feedback received:*\n\n{text}",
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
# Slash Commands - FULL LIST                                           #
# ------------------------------------------------------------------ #

@bolt_app.command("/help")
async def cmd_help(ack, command) -> None:
    await ack()
    slack_id = command["user_id"]
    help_text = """
*Available Commands:*
• `/standup` - Manually start your daily standup prompt.
• `/policy <question>` - Ask anything about company rules, leaves, or info.
• `/applyleave` or `/leave` - Start the flow to apply for leaves.
• `/kudos @user <message>` - Give a public shout-out to a colleague!
• `/reminder <time> <task>` - Set a personal reminder.
• `/feedback <message>` - Send anonymous feedback to HR.
• `/announce <message>` - Send a global DM to all employees (HR Only).
• `/hierarchy` - View the company reporting structure.
• `/assign @user to @manager` - (HR Admin) Assign a manager to an employee.
    """
    await slack_service.dm_user(slack_id, help_text)


@bolt_app.command("/standup")
async def cmd_standup(ack, command) -> None:
    await ack()
    _spawn_background(standup_agent.trigger_standup_for_all(), "manual_standup")


@bolt_app.command("/policy")
async def cmd_policy(ack, command) -> None:
    await ack()
    slack_id = command["user_id"]
    text = command.get("text", "").strip()
    if text:
        _spawn_background(policy_agent.answer_policy_question(text, slack_id), "policy_search")
    else:
        await slack_service.dm_user(slack_id, "Usage: `/policy What is the leave policy?`")


@bolt_app.command("/announce")
async def cmd_announce(ack, command) -> None:
    await ack()
    slack_id = command["user_id"]
    text = command.get("text", "").strip()
    if not text:
        await slack_service.dm_user(slack_id, "Usage: `/announce Important: Office will be closed tomorrow.`")
        return
    _spawn_background(_run_announce(slack_id, text), "announce_command")


async def _run_announce(slack_id: str, text: str) -> None:
    try:
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(User).where(User.slack_id == slack_id))
            user = result.scalars().first()
            if not user or not user.is_hr_admin:
                await slack_service.dm_user(slack_id, ":x: Only HR Admins can send global announcements.")
                return
            await broadcast_agent.send_broadcast(session, slack_id, text, user)
            await session.commit()
            await slack_service.dm_user(slack_id, ":white_check_mark: Announcement sent to all active users.")
    except Exception:
        logger.exception("Announce failed")


@bolt_app.command("/applyleave")
@bolt_app.command("/apply-leave")
@bolt_app.command("/leave")
async def cmd_leave_flow(ack, command) -> None:
    await ack()
    _spawn_background(leave_agent.start_leave_conversation(command["user_id"]), "leave_flow")


@bolt_app.command("/feedbacks")
@bolt_app.command("/feedback")
async def cmd_feedback(ack, command) -> None:
    await ack()
    text = command.get("text", "").strip()
    if text:
        _spawn_background(_handle_feedback_logic(command["user_id"], text), "feedback_command")
    else:
        await slack_service.dm_user(command["user_id"], "Usage: `/feedback Your message here`")


@bolt_app.command("/assign")
async def cmd_assign(ack, command) -> None:
    await ack()
    slack_id = command["user_id"]
    text = command.get("text", "").strip()
    # Expecting: @user to @manager
    match = re.search(r"<@([A-Z0-9]+)>.*?to.*?<@([A-Z0-9]+)>", text)
    if not match:
        await slack_service.dm_user(slack_id, "Usage: `/assign @employee to @manager`")
        return
    _spawn_background(_run_assign(slack_id, match.group(1), match.group(2)), "assign_command")


async def _run_assign(admin_id: str, employee_id: str, manager_id: str) -> None:
    try:
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(User).where(User.slack_id == admin_id))
            admin = result.scalars().first()
            if not admin or not admin.is_hr_admin:
                await slack_service.dm_user(admin_id, ":x: Only HR Admins can assign managers.")
                return
            
            res = await session.execute(select(User).where(User.slack_id == employee_id))
            emp = res.scalars().first()
            if not emp:
                await slack_service.dm_user(admin_id, f":x: User <@{employee_id}> not found in database.")
                return
            
            emp.manager_slack_id = manager_id
            await session.commit()
            await slack_service.dm_user(admin_id, f":white_check_mark: Assigned <@{manager_id}> as manager for <@{employee_id}>.")
    except Exception:
        logger.exception("Assign failed")


@bolt_app.command("/hierarchy")
async def cmd_hierarchy(ack, command) -> None:
    await ack()
    _spawn_background(_run_hierarchy(command["user_id"]), "hierarchy_command")


async def _run_hierarchy(slack_id: str) -> None:
    try:
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(User).where(User.is_active == True))
            users = result.scalars().all()
            
            # Simple tree builder
            tree = "🏢 *Company Structure:*\n"
            managers = {u.slack_id: u for u in users if any(x.manager_slack_id == u.slack_id for x in users)}
            
            for m_id, m in managers.items():
                tree += f"\n• *<@{m_id}>* (Manager)\n"
                reports = [u for u in users if u.manager_slack_id == m_id]
                for r in reports:
                    tree += f"  └─ <@{r.slack_id}>\n"
            
            await slack_service.dm_user(slack_id, tree)
    except Exception:
        logger.exception("Hierarchy failed")


@bolt_app.command("/reminder")
async def cmd_reminder(ack, command) -> None:
    await ack()
    text = command.get("text", "").strip()
    if text:
        _spawn_background(_run_reminder_command(command["user_id"], text), "reminder_command")


async def _run_reminder_command(slack_id: str, text: str) -> None:
    try:
        from app.agents.reminder_agent import parse_and_create_reminder
        result = await parse_and_create_reminder(slack_id, text)
        await slack_service.dm_user(slack_id, result)
    except Exception:
        logger.exception("Reminder failed")


@bolt_app.command("/setbirthday")
async def cmd_setbirthday(ack, command) -> None:
    await ack()
    _spawn_background(_run_celebration_cmd(command["user_id"], command["text"], "birthday"), "set_birthday")


@bolt_app.command("/setanniversary")
async def cmd_setanniversary(ack, command) -> None:
    await ack()
    _spawn_background(_run_celebration_cmd(command["user_id"], command["text"], "anniversary"), "set_anniversary")


async def _run_celebration_cmd(slack_id: str, text: str, type: str) -> None:
    try:
        from app.agents.celebration_agent import set_user_birthday, set_user_anniversary
        # Basic parsing for @user YYYY-MM-DD
        match = re.search(r"<@([A-Z0-9]+)>.*?(\d{4}-\d{2}-\d{2})", text)
        if not match:
            await slack_service.dm_user(slack_id, f"Usage: `/set{type} @user YYYY-MM-DD`")
            return
        
        target, date_str = match.group(1), match.group(2)
        if type == "birthday":
            res = await set_user_birthday(slack_id, target, date_str)
        else:
            res = await set_user_anniversary(slack_id, target, date_str)
        await slack_service.dm_user(slack_id, res)
    except Exception:
        logger.exception(f"Set {type} failed")


@bolt_app.command("/kudos")
async def cmd_kudos(ack, command) -> None:
    await ack()
    _spawn_background(_run_kudos_command(command["user_id"], command["text"]), "kudos_command")


async def _run_kudos_command(slack_id: str, text: str) -> None:
    result = await kudos_agent.handle_kudos_command(slack_id, text)
    await slack_service.dm_user(slack_id, result)


# ------------------------------------------------------------------ #
# Interactive Actions                                                  #
# ------------------------------------------------------------------ #

@bolt_app.action("leave_approve")
@bolt_app.action("leave_reject")
async def action_leave_handler(ack, body, action) -> None:
    await ack()
    _spawn_background(_process_leave_action(body, action, action["action_id"]), "leave_action")


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
