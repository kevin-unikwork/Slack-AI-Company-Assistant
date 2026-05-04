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
    vault_agent,
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
    """The Zero-Dependency Bridge."""
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
# Slash Commands                                                       #
# ------------------------------------------------------------------ #

@bolt_app.command("/help")
async def cmd_help(ack, command) -> None:
    """A professional, detailed guide for all bot features."""
    await ack()
    slack_id = command["user_id"]
    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "📘 Professional Help & Usage Guide", "emoji": True}
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": "Welcome to your AI-powered Company Assistant! This guide provides detailed instructions on how to use every feature. *All commands can be used in any channel or via Direct Message.*"}
        },
        {"type": "divider"},
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": "*🌟 COMPANY CULTURE & APPRECIATION*"}
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": "• `/kudos @user <message>`\n_Recognize a colleague for their hard work. Your message will be sent to them privately and posted in the #general channel._\n*Example:* `/kudos @Shraddha for the amazing audit support!`"}
        },
        {"type": "divider"},
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": "*📅 HR, ATTENDANCE & STRUCTURE*"}
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": "• `/standup` - _Manually trigger your daily standup prompt if you missed the automated 9:00 AM DM._\n• `/applyleave` - _Start a conversational flow to apply for leaves or check your balance._\n• `/hierarchy` - _View the company's reporting structure and manager assignments._"}
        },
        {"type": "divider"},
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": "*📜 KNOWLEDGE & SEARCH*"}
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": "• `/policy <your question>`\n_Ask our AI about company policies, leave rules, or office hours. It searches all official PDF documents._\n*Example:* `/policy What is the maternity leave policy?`"}
        },
        {"type": "divider"},
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": "*⏰ PRODUCTIVITY & FEEDBACK*"}
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": "• `/reminder <time> <task>`\n_Set a natural-language reminder for yourself._\n*Example:* `/reminder in 2 hours to review the PR`\n• `/vault <action> <key> <value>`\n_Store and retrieve encrypted secrets (API keys, links)._\n*Example:* `/vault set Figma https://...`\n• `/feedback <message>`\n_Send a truly anonymous message to the HR team. Your identity is never revealed._"}
        },
        {"type": "divider"},
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": "*⚙️ HR ADMIN TOOLS (Admin Only)*"}
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": "• `/announce <message>` - _Broadcast a DM to every employee in the company._\n• `/assign @user to @manager` - _Update reporting relationships._\n• `/setbirthday @user YYYY-MM-DD` - _Schedule automated birthday celebrations._"}
        },
        {"type": "divider"},
        {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": "💡 *Pro-Tip:* You can also just type 'Hello' or 'Who is HR?' in a Direct Message to me for a quick AI chat!"}]
        }
    ]
    await slack_service.dm_user(slack_id, text="Professional Help & Usage Guide", blocks=blocks)


@bolt_app.command("/standup")
async def cmd_standup(ack, command) -> None:
    await ack()
    _spawn_background(standup_agent.trigger_standup_for_user(command["user_id"]), "manual_standup")


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
            await slack_service.dm_user(slack_id, ":white_check_mark: Announcement sent successfully.")
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
    # Extract all words starting with @ or standard <@ID> mentions
    potential_mentions = re.findall(r"(?:<@([A-Z0-9]+)(?:\|[^>]+)?>|@([^\s]+))", command.get("text", ""))
    
    resolved_ids = []
    for m in potential_mentions:
        slack_id, plain_name = m
        if slack_id:
            resolved_ids.append(slack_id)
        elif plain_name:
            # Try to resolve plain name to ID
            users = await slack_service.get_all_workspace_users()
            for u in users:
                if plain_name.lower() in [u.get("name", "").lower(), u.get("real_name", "").lower(), u.get("profile", {}).get("display_name", "").lower()]:
                    resolved_ids.append(u["id"])
                    break
    
    if len(resolved_ids) < 2:
        await slack_service.dm_user(command["user_id"], "Usage: `/assign @employee to @manager` (Please make sure to @mention both users)")
        return
        
    _spawn_background(_run_assign(command["user_id"], resolved_ids[0], resolved_ids[1]), "assign_command")


async def _run_assign(admin_id: str, employee_id: str, manager_id: str) -> None:
    try:
        async with AsyncSessionLocal() as session:
            res = await session.execute(select(User).where(User.slack_id == admin_id))
            admin = res.scalars().first()
            if not admin or not admin.is_hr_admin:
                await slack_service.dm_user(admin_id, ":x: Only HR Admins can assign managers.")
                return
            res = await session.execute(select(User).where(User.slack_id == employee_id))
            emp = res.scalars().first()
            if emp:
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
            tree = "🏢 *Company Structure:*\n"
            for m in [u for u in users if any(x.manager_slack_id == u.slack_id for x in users)]:
                tree += f"\n• *<@{m.slack_id}>*\n"
                for r in [u for u in users if u.manager_slack_id == m.slack_id]:
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
@bolt_app.command("/setanniversary")
async def cmd_celebration(ack, command) -> None:
    await ack()
    type = "birthday" if "birthday" in command["command"] else "anniversary"
    _spawn_background(_run_celebration_cmd(command["user_id"], command["text"], type), f"set_{type}")


async def _run_celebration_cmd(slack_id: str, text: str, type: str) -> None:
    try:
        from app.agents.celebration_agent import set_user_birthday, set_user_anniversary
        match = re.search(r"<@([A-Z0-9]+)(?:\|[^>]+)?>.*?(\d{4}-\d{2}-\d{2})", text)
        if not match:
            await slack_service.dm_user(slack_id, f"Usage: `/set{type} @user YYYY-MM-DD`")
            return
        res = await (set_user_birthday if type == "birthday" else set_user_anniversary)(slack_id, match.group(1), match.group(2))
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


@bolt_app.command("/vault")
async def cmd_vault(ack, command) -> None:
    await ack()
    _spawn_background(_run_vault_command(command["user_id"], command.get("text", "").strip()), "vault_command")


async def _run_vault_command(slack_id: str, text: str) -> None:
    try:
        if not text or text.lower() == "list":
            res = await vault_agent.list_vault(slack_id)
            await slack_service.dm_user(slack_id, res)
            return

        parts = text.split(maxsplit=2)
        action = parts[0].lower()
        
        if action == "set" and len(parts) == 3:
            res = await vault_agent.add_to_vault(slack_id, parts[1], parts[2])
            await slack_service.dm_user(slack_id, res)
        elif action == "get" and len(parts) >= 2:
            res = await vault_agent.get_from_vault(slack_id, parts[1])
            await slack_service.dm_user(slack_id, res)
        elif action == "delete" and len(parts) >= 2:
            res = await vault_agent.delete_from_vault(slack_id, parts[1])
            await slack_service.dm_user(slack_id, res)
        else:
            usage = (
                ":vault: *Vault Usage Guide:*\n"
                "• `/vault set <key> <secret>` - Store a secret\n"
                "• `/vault get <key>` - Retrieve a secret (private DM)\n"
                "• `/vault list` - List all your keys\n"
                "• `/vault delete <key>` - Remove a key"
            )
            await slack_service.dm_user(slack_id, usage)
    except Exception:
        logger.exception("Vault command failed")
        await slack_service.dm_user(slack_id, ":warning: Vault error occurred. Please try again later.")


@bolt_app.command("/setmessage")
async def cmd_setmessage(ack, command) -> None:
    await ack()
    _spawn_background(
        _run_setmessage_command(command["user_id"], command.get("text", "").strip()),
        "setmessage_command",
    )


async def _run_setmessage_command(slack_id: str, text: str) -> None:
    from app.agents.celebration_agent import (
        set_celebration_message,
        view_celebration_message,
        reset_celebration_message,
    )

    try:
        if not text:
            usage = (
                ":scroll: *Celebration Message Manager (HR Admin Only):*\n\n"
                "*Set a message:*\n"
                "• `/setmessage set birthday <your message>`\n"
                "• `/setmessage set anniversary <your message>`\n\n"
                "*View current templates:*\n"
                "• `/setmessage view birthday`\n"
                "• `/setmessage view anniversary`\n\n"
                "*Reset to AI-generated:*\n"
                "• `/setmessage reset birthday`\n"
                "• `/setmessage reset anniversary`\n\n"
                "_Available variables:_ `{name}`, `{years}`, `{date}`\n"
                "_Example:_ `/setmessage set birthday Happy Birthday {name}! 🎂 Wishing you a great year ahead from all of us!`"
            )
            await slack_service.dm_user(slack_id, usage)
            return

        parts = text.split(maxsplit=2)
        action = parts[0].lower()

        if action == "set" and len(parts) >= 3:
            template_type = parts[1].lower()
            message = parts[2]
            res = await set_celebration_message(slack_id, template_type, message)
            await slack_service.dm_user(slack_id, res)

        elif action == "view" and len(parts) >= 2:
            template_type = parts[1].lower()
            res = await view_celebration_message(slack_id, template_type)
            await slack_service.dm_user(slack_id, res)

        elif action == "reset" and len(parts) >= 2:
            template_type = parts[1].lower()
            res = await reset_celebration_message(slack_id, template_type)
            await slack_service.dm_user(slack_id, res)

        else:
            await slack_service.dm_user(
                slack_id,
                ":x: Invalid command. Use `/setmessage` to see usage guide.",
            )
    except Exception:
        logger.exception("Setmessage command failed")
        await slack_service.dm_user(slack_id, ":warning: An error occurred. Please try again.")


@bolt_app.command("/triggercelebration")
async def cmd_triggercelebration(ack, command) -> None:
    await ack()
    _spawn_background(_run_triggercelebration_command(command["user_id"]), "triggercelebration_command")


async def _run_triggercelebration_command(slack_id: str) -> None:
    from app.agents.celebration_agent import check_and_post_celebrations
    from app.db.session import AsyncSessionLocal
    from app.db.models.user import User
    from sqlalchemy import select

    try:
        async with AsyncSessionLocal() as session:
            hr_res = await session.execute(select(User).where(User.slack_id == slack_id))
            hr_user = hr_res.scalar_one_or_none()
            if not hr_user or not hr_user.is_hr_admin:
                await slack_service.dm_user(slack_id, ":no_entry: Only HR admins can trigger celebrations.")
                return

        await slack_service.dm_user(slack_id, ":hourglass_flowing_sand: Triggering celebration check...")
        count = await check_and_post_celebrations()
        await slack_service.dm_user(slack_id, f":white_check_mark: Celebration check complete. Posted {count} message(s).")
    except Exception:
        logger.exception("Triggercelebration command failed")
        await slack_service.dm_user(slack_id, ":warning: An error occurred while triggering celebrations.")
