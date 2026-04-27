import asyncio
import json
import logging
import contextlib

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from slack_bolt.async_app import AsyncApp
from slack_bolt.adapter.fastapi.async_handler import AsyncSlackRequestHandler

from app.config import settings
from app.agents import intent_router
from app.agents.intent_router import Intent
from app.agents import standup_agent, policy_agent, leave_agent, onboarding_agent, chat_agent
from app.services.slack_service import slack_service
from app.utils.logger import get_logger
from app.utils.exceptions import AuthorizationError
from app.db.session import AsyncSessionLocal
from app.services.user_service import user_service
from app.db.models.user import User

logger = get_logger(__name__)

# ------------------------------------------------------------------ #
# Slack Bolt App                                                     #
# ------------------------------------------------------------------ #

bolt_app = AsyncApp(
    token=settings.slack_bot_token,
    signing_secret=settings.slack_signing_secret,
)

handler = AsyncSlackRequestHandler(bolt_app)

router = APIRouter(tags=["slack"])


@router.post("/slack/events")
async def slack_events(req: Request) -> Response:
    """Single endpoint for all Slack Events API and interaction payloads."""
    # Read the raw body once so it can be reused or checked for URL verification.
    body_bytes = await req.body()
    
    # Slack URL verification handshake must return {"challenge": "..."}.
    try:
        # Only attempt to parse as JSON if it's not a form-encoded request.
        content_type = req.headers.get("content-type", "")
        if "application/json" in content_type:
            body_json = json.loads(body_bytes)
            if body_json.get("type") == "url_verification":
                return JSONResponse(content={"challenge": body_json.get("challenge", "")})
    except (json.JSONDecodeError, AttributeError):
        pass

    # Pass the request to Bolt. 
    # Note: Bolt's AsyncSlackRequestHandler will reuse the already-read body from the request object.
    return await handler.handle(req)


# ------------------------------------------------------------------ #
# Direct Messages                                                    #
# ------------------------------------------------------------------ # 

@bolt_app.event("message")
async def handle_dm(event: dict, say, ack) -> None:
    """Route all DMs to the correct agent based on intent."""
    await ack()

    # Ignore bot messages and message_changed subtypes
    if event.get("bot_id") or event.get("subtype"):
        return

    slack_id: str = event.get("user", "")
    text: str = event.get("text", "").strip()
    channel_id: str = event.get("channel", "")
    ts: str = event.get("ts", "")

    if not slack_id or not text:
        return

    logger.info("DM received", extra={"slack_id": slack_id, "text_preview": text[:80]})

    # Professional touch: immediate reaction to show we are listening
    asyncio.create_task(slack_service.add_reaction(channel_id, ts, "speech_balloon"))
    
    asyncio.create_task(_route_dm(slack_id, text, channel_id))


async def _route_dm(slack_id: str, text: str, channel_id: str) -> None:
    """Background task: classify intent and dispatch to the right agent."""
    # Send an initial loading message in the same channel where the user asked.
    loading_ts = await slack_service.post_to_channel(
        channel_id,
        "_Processing your request..._ :hourglass_flowing_sand:",
    )

    async def _run_policy_loading_animation() -> None:
        frames = [
            "_Searching policy documents._ :mag_right:",
            "_Searching policy documents.._ :mag_right:",
            "_Searching policy documents..._ :mag_right:",
        ]
        i = 0
        while True:
            await slack_service.update_message(channel_id, loading_ts, frames[i % len(frames)])
            i += 1
            await asyncio.sleep(0.8)
    
    try:
        intent = await intent_router.classify_intent(slack_id, text)

        if intent == Intent.STANDUP_RESPONSE:
            await slack_service.update_message(channel_id, loading_ts, "_Processing your standup update..._ :memo:")
            await standup_agent.handle_standup_response(slack_id, text)
            await slack_service.delete_message(channel_id, loading_ts)

        elif intent == Intent.POLICY_QA:
            animation_task = asyncio.create_task(_run_policy_loading_animation())
            try:
                answer = await policy_agent.answer_policy_question(text, slack_id)
            finally:
                animation_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await animation_task
            await slack_service.delete_message(channel_id, loading_ts)
            await slack_service.post_to_channel(channel_id, answer)

        elif intent == Intent.LEAVE_REQUEST:
            await slack_service.update_message(channel_id, loading_ts, "_Checking leave system..._ :calendar:")
            # The leave agent might send multiple messages, so we delete our loading one first
            await slack_service.delete_message(channel_id, loading_ts)
            await leave_agent.handle_leave_message(slack_id, text)

        elif intent == Intent.FEEDBACK:
            await slack_service.update_message(channel_id, loading_ts, "_Submitting anonymous feedback..._ :postbox:")
            await _handle_feedback(slack_id, text)
            await slack_service.delete_message(channel_id, loading_ts)

        else:  # GENERAL_CHAT
            # For chat, we can keep the loading state or just let the agent handle it
            await slack_service.update_message(channel_id, loading_ts, "_Thinking..._ :brain:")
            reply = await chat_agent.generate_chat_reply(slack_id, text)
            await slack_service.delete_message(channel_id, loading_ts)
            await slack_service.post_to_channel(channel_id, reply)

    except Exception as exc:
        logger.exception("DM routing failed", extra={"slack_id": slack_id})
        error_msg = str(exc)
        with contextlib.suppress(Exception):
            await slack_service.delete_message(channel_id, loading_ts)
        await slack_service.post_to_channel(
            channel_id,
            f":x: Something went wrong processing your message.\n*Error:* `{error_msg}`\n_Please check your environment variables (like OPENAI_API_KEY) or logs._",
        )


async def _handle_feedback(slack_id: str, text: str) -> None:
    """Post anonymous feedback to the HR private channel and save to database."""
    try:
        from app.db.session import AsyncSessionLocal
        from app.db.models.feedback import Feedback

        async with AsyncSessionLocal() as session:
            feedback_record = Feedback(user_slack_id=slack_id, text=text)
            session.add(feedback_record)
            await session.commit()

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
    except Exception as exc:
        logger.exception("Feedback submission failed", extra={"slack_id": slack_id, "error": str(exc)})
        await slack_service.dm_user(
            slack_id, f":x: Failed to submit your feedback. *Reason:* `{str(exc)}`"
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
    asyncio.create_task(onboarding_agent.onboard_new_member(slack_id))


# ------------------------------------------------------------------ #
# App Mention                                                          #
# ------------------------------------------------------------------ #

@bolt_app.event("app_mention")
async def handle_mention(event: dict, say, ack) -> None:
    """Handle @bot mentions in channels."""
    await ack()
    slack_id: str = event.get("user", "")
    text: str = event.get("text", "")
    channel_id: str = event.get("channel", "")
    # Strip the mention tag and treat the rest as a DM
    clean_text = " ".join(w for w in text.split() if not w.startswith("<@"))
    if clean_text.strip():
        asyncio.create_task(_route_dm(slack_id, clean_text.strip(), channel_id))
    else:
        await say(
            "Hi! Mention me with a question, e.g. `@Bot What is the leave policy?`"
        )


# ------------------------------------------------------------------ #
# Slash Commands                                                       #
# ------------------------------------------------------------------ #

@bolt_app.command("/standup")
async def cmd_standup(ack, command) -> None:
    """Manually trigger standup for the invoking user."""
    await ack()
    slack_id: str = command["user_id"]
    asyncio.create_task(standup_agent.trigger_standup_for_user(slack_id))


@bolt_app.command("/policy")
async def cmd_policy(ack, command, say) -> None:
    """Answer a policy question inline."""
    await ack()
    slack_id: str = command["user_id"]
    channel_id: str = command["channel_id"]
    question: str = command.get("text", "").strip()
    if not question:
        await slack_service.dm_user(slack_id, "Please include a question: `/policy What is the WFH policy?`")
        return
    asyncio.create_task(_answer_policy_command(slack_id, channel_id, question))


async def _answer_policy_command(slack_id: str, channel_id: str, question: str) -> None:
    loading_ts = None
    try:
        loading_ts = await slack_service.post_to_channel(channel_id, "_Searching policy documents._ :mag_right:")

        async def _run_policy_loading_animation() -> None:
            frames = [
                "_Searching policy documents._ :mag_right:",
                "_Searching policy documents.._ :mag_right:",
                "_Searching policy documents..._ :mag_right:",
            ]
            i = 0
            while True:
                await slack_service.update_message(channel_id, loading_ts, frames[i % len(frames)])
                i += 1
                await asyncio.sleep(0.8)

        animation_task = asyncio.create_task(_run_policy_loading_animation())
        try:
            answer = await policy_agent.answer_policy_question(question, slack_id)
        finally:
            animation_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await animation_task
        await slack_service.delete_message(channel_id, loading_ts)
        await slack_service.post_to_channel(channel_id, answer)
    except Exception:
        logger.exception("Policy command failed", extra={"slack_id": slack_id})
        if loading_ts:
            with contextlib.suppress(Exception):
                await slack_service.delete_message(channel_id, loading_ts)
            await slack_service.post_to_channel(channel_id, ":x: Failed to retrieve policy information.")
        else:
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
    asyncio.create_task(_run_broadcast_command(slack_id, message))


async def _run_broadcast_command(slack_id: str, message: str) -> None:
    from app.db.session import AsyncSessionLocal
    from app.db.models.user import User
    from app.agents.broadcast_agent import send_broadcast
    from sqlalchemy import select

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
            f":mega: *Announcement Broadcasted*\n"
            f"• *Sent to:* {result_data['sent']} employees\n"
            f"• *Failed:* {result_data['failed']}\n"
            f"• *ID:* `{result_data['broadcast_id']}`"
        )
    except AuthorizationError:
        await slack_service.dm_user(slack_id, ":no_entry: Only HR admins can send announcements.")
    except Exception as exc:
        logger.exception("Broadcast command failed", extra={"slack_id": slack_id})
        error_msg = str(exc)
        await slack_service.dm_user(
            slack_id, 
            f":x: Failed to send announcement.\n*Error:* `{error_msg}`"
        )


@bolt_app.command("/applyleave")
async def cmd_leave(ack, command) -> None:
    """Start a leave request conversation."""
    await ack()
    slack_id: str = command["user_id"]
    asyncio.create_task(leave_agent.start_leave_conversation(slack_id))


@bolt_app.command("/assign")
async def cmd_assign(ack, command) -> None:
    """HR Command: Assign a Project Manager to an employee."""
    await ack()
    asyncio.create_task(_run_assign_command(command))


@bolt_app.command("/hierarchy")
async def cmd_hierarchy(ack, command) -> None:
    """Display the current reporting structure."""
    await ack()
    asyncio.create_task(_run_hierarchy_command(command))


async def _run_assign_command(command: dict) -> None:
    slack_id: str = command["user_id"]
    text: str = command.get("text", "").strip()
    
    try:
        async with AsyncSessionLocal() as session:
            user = await user_service.get_by_slack_id(session, slack_id)
            if not user or not user.is_hr_admin:
                await slack_service.dm_user(slack_id, ":no_entry: Only HR admins can manage the hierarchy.")
                return

            import re
            # Match standard Slack mentions like <@U12345>
            mentions = re.findall(r"<@([a-zA-Z0-9]+)[^>]*>", text)
            
            if len(mentions) < 2:
                # Fallback: Check if they passed literal usernames like @username
                usernames = re.findall(r"@([a-zA-Z0-9._-]+)", text)
                if len(usernames) >= 2:
                    emp_user = await user_service.get_by_slack_username(session, usernames[0])
                    mgr_user = await user_service.get_by_slack_username(session, usernames[1])
                    
                    if not emp_user:
                        await slack_service.dm_user(slack_id, f":x: Employee username `@{usernames[0]}` not found in system.")
                        return
                    if not mgr_user:
                        await slack_service.dm_user(slack_id, f":x: Manager username `@{usernames[1]}` not found in system.")
                        return
                        
                    mentions = [emp_user.slack_id, mgr_user.slack_id]
                else:
                    await slack_service.dm_user(slack_id, f"Usage: `/assign @Employee @ProjectManager` (Could not parse mentions from: `{text}`)")
                    return
            
            emp_id, mgr_id = mentions[0], mentions[1]
            
            try:
                mgr = await user_service.get_by_slack_id(session, mgr_id)
                if not mgr:
                     await slack_service.dm_user(slack_id, f":x: Manager <@{mgr_id}> not found in system.")
                     return
                
                if not mgr.is_project_manager:
                    mgr.is_project_manager = True
                
                emp = await user_service.get_by_slack_id(session, emp_id)
                if not emp:
                    await slack_service.dm_user(slack_id, f":x: Employee <@{emp_id}> not found in system.")
                    return
                
                emp.manager_slack_id = mgr_id
                await session.commit()
                
                await slack_service.dm_user(
                    slack_id, 
                    f"✅ *Assignment Successful*\nEmployee: <@{emp_id}>\nProject Manager: <@{mgr_id}>"
                )
            except Exception as e:
                await slack_service.dm_user(slack_id, f":x: Error: {str(e)}")
    except Exception as exc:
        logger.exception("Background assign command failed", extra={"slack_id": slack_id})
        await slack_service.dm_user(slack_id, f":x: A system error occurred while processing your request: `{str(exc)}`")


async def _run_hierarchy_command(command: dict) -> None:
    slack_id: str = command["user_id"]
    try:
        async with AsyncSessionLocal() as session:
            from sqlalchemy import select
            result = await session.execute(select(User).where(User.is_project_manager == True))
            pms = result.scalars().all()
            
            if not pms:
                await slack_service.dm_user(slack_id, "No Project Managers defined in the system.")
                return
                
            blocks = [
                {"type": "header", "text": {"type": "plain_text", "text": "📊 Organizational Hierarchy"}},
                {"type": "divider"}
            ]
            
            for pm in pms:
                res = await session.execute(select(User).where(User.manager_slack_id == pm.slack_id))
                reports = res.scalars().all()
                report_list = "\n".join([f"• <@{r.slack_id}>" for r in reports]) or "_No direct reports_"
                
                blocks.append({
                    "type": "section",
                    "text": {
                        "type": "mrkdwn", 
                        "text": f"*PM: <@{pm.slack_id}>* ({pm.slack_username})\n{report_list}"
                    }
                })
                
            await slack_service.dm_user(slack_id, text="Company Hierarchy", blocks=blocks)
    except Exception as exc:
        logger.exception("Background hierarchy command failed", extra={"slack_id": slack_id})
        await slack_service.dm_user(slack_id, f":x: A system error occurred: `{str(exc)}`")


@bolt_app.command("/help")
async def cmd_help(ack, command) -> None:
    """Send a beautifully formatted help guide via DM."""
    await ack()
    slack_id: str = command["user_id"]
    asyncio.create_task(_send_help(slack_id))


async def _send_help(slack_id: str) -> None:
    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "📖 Bot Command Reference", "emoji": True},
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "Welcome! Here's everything I can do for you. Just type a command or DM me naturally.",
            },
        },
        {"type": "divider"},
        # ---- Daily Standup ----
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    "*:memo: Daily Standup*\n"
                    "• `/standup` — Start your daily standup manually\n"
                    "_I'll also DM you every morning automatically._\n"
                    "_Tip: Mention project channels like_ `#social` _to group updates by project!_"
                ),
            },
        },
        {"type": "divider"},
        # ---- Leave Management ----
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    "*:calendar: Leave Management*\n"
                    "• `/applyleave` — Start a leave request conversation\n"
                    "• _Or just DM me:_ `I want to apply for leave`\n"
                    "_Your manager will receive an approve/reject notification._"
                ),
            },
        },
        {"type": "divider"},
        # ---- Policy Q&A ----
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    "*:question: Policy Q&A*\n"
                    "• `/policy What is the WFH policy?` — Ask any policy question\n"
                    "• _Or just DM me your question directly!_\n"
                    "_Powered by AI + your company's policy documents._"
                ),
            },
        },
        {"type": "divider"},
        # ---- Reminders ----
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    "*:alarm_clock: Reminders*\n"
                    "• `/reminder me in 2 hours to review the PR`\n"
                    "• `/reminder me at 3pm to join the standup call`\n"
                    "• `/reminder me tomorrow at 10am to submit the report`\n"
                    "_I'll DM you at the right time!_"
                ),
            },
        },
        {"type": "divider"},
        # ---- Feedback ----
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    "*:speech_balloon: Anonymous Feedback*\n"
                    "• `/feedbacks Your feedback message` — Submit anonymous feedback to HR\n"
                    "_Your identity is kept confidential._"
                ),
            },
        },
        {"type": "divider"},
        # ---- Celebrations ----
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    "*:birthday: Birthdays & Anniversaries*\n"
                    "• _Automatic!_ I post birthday and work anniversary wishes in `#general` every morning.\n"
                    "• *HR Only:* `/setbirthday @user 1995-06-15` — Set a team member's birthday\n"
                    "• *HR Only:* `/setanniversary @user 2023-01-10` — Set a team member's join date"
                ),
            },
        },
        {"type": "divider"},
        # ---- HR Admin ----
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    "*:shield: HR Admin Commands*\n"
                    "• `/announce Your message` — Broadcast to all employees _(HR only)_\n"
                    "• `/assign @employee @PM` — Assign a Project Manager _(HR only)_\n"
                    "• `/hierarchy` — View the current reporting structure"
                ),
            },
        },
        {"type": "divider"},
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": "_💡 You can also just DM me naturally — I understand questions about policies, leave requests, and more!_",
                }
            ],
        },
    ]

    await slack_service.dm_user(slack_id, text="Bot Command Reference", blocks=blocks)


@bolt_app.command("/feedbacks")
async def cmd_feedback(ack, command) -> None:
    """Submit anonymous feedback."""
    await ack()
    slack_id: str = command["user_id"]
    text: str = command.get("text", "").strip()
    if not text:
        await slack_service.dm_user(slack_id, "Usage: `/feedbacks Your feedback here`")
        return
    asyncio.create_task(_handle_feedback(slack_id, text))


# ------------------------------------------------------------------ #
# Reminders                                                            #
# ------------------------------------------------------------------ #

@bolt_app.command("/reminder")
async def cmd_remind(ack, command) -> None:
    """Set a natural language reminder."""
    await ack()
    slack_id: str = command["user_id"]
    text: str = command.get("text", "").strip()
    if not text:
        await slack_service.dm_user(
            slack_id,
            "Usage: `/reminder me in 2 hours to review the PR`\n"
            "Or: `/reminder me at 3pm to join the standup call`\n"
            "Or: `/reminder me tomorrow at 10am to submit the report`",
        )
        return
    asyncio.create_task(_run_remind_command(slack_id, text))


async def _run_remind_command(slack_id: str, text: str) -> None:
    try:
        from app.agents.reminder_agent import parse_and_create_reminder

        result_msg = await parse_and_create_reminder(slack_id, text)
        await slack_service.dm_user(slack_id, result_msg)
    except Exception as exc:
        logger.exception("Remind command failed", extra={"slack_id": slack_id})
        await slack_service.dm_user(slack_id, f":x: Failed to create reminder: `{exc}`")


# ------------------------------------------------------------------ #
# Celebrations (HR Admin)                                              #
# ------------------------------------------------------------------ #

@bolt_app.command("/setbirthday")
async def cmd_setbirthday(ack, command) -> None:
    """HR Admin: Set a user's birthday. Usage: /setbirthday @user YYYY-MM-DD"""
    await ack()
    slack_id: str = command["user_id"]
    text: str = command.get("text", "").strip()
    asyncio.create_task(_run_setbirthday(slack_id, text))


async def _run_setbirthday(slack_id: str, text: str) -> None:
    try:
        import re
        from app.agents.celebration_agent import set_user_birthday

        # Match either `<@UID...>` or `@username`
        mentions = re.findall(r"<@([a-zA-Z0-9]+)[^>]*>|@([a-zA-Z0-9_.-]+)", text)
        date_match = re.search(r"\d{4}-\d{2}-\d{2}", text)

        if not mentions or not date_match:
            await slack_service.dm_user(
                slack_id,
                "Usage: `/setbirthday @user 1995-06-15`",
            )
            return

        # Extract the matched group (either the UID or the username)
        target_user = mentions[0][0] if mentions[0][0] else mentions[0][1]
        
        result = await set_user_birthday(slack_id, target_user, date_match.group())
        await slack_service.dm_user(slack_id, result)
    except Exception as exc:
        logger.exception("Set birthday command failed", extra={"slack_id": slack_id})
        await slack_service.dm_user(slack_id, f":x: Error: `{exc}`")


@bolt_app.command("/setanniversary")
async def cmd_setanniversary(ack, command) -> None:
    """HR Admin: Set a user's join date. Usage: /setanniversary @user YYYY-MM-DD"""
    await ack()
    slack_id: str = command["user_id"]
    text: str = command.get("text", "").strip()
    asyncio.create_task(_run_setanniversary(slack_id, text))


async def _run_setanniversary(slack_id: str, text: str) -> None:
    try:
        import re
        from app.agents.celebration_agent import set_user_anniversary

        # Match either `<@UID...>` or `@username`
        mentions = re.findall(r"<@([a-zA-Z0-9]+)[^>]*>|@([a-zA-Z0-9_.-]+)", text)
        date_match = re.search(r"\d{4}-\d{2}-\d{2}", text)

        if not mentions or not date_match:
            await slack_service.dm_user(
                slack_id,
                "Usage: `/setanniversary @user 2023-01-10`",
            )
            return

        # Extract the matched group (either the UID or the username)
        target_user = mentions[0][0] if mentions[0][0] else mentions[0][1]

        result = await set_user_anniversary(slack_id, target_user, date_match.group())
        await slack_service.dm_user(slack_id, result)
    except Exception as exc:
        logger.exception("Set anniversary command failed", extra={"slack_id": slack_id})
        await slack_service.dm_user(slack_id, f":x: Error: `{exc}`")


# ------------------------------------------------------------------ #
# Interactive Actions (button clicks)                                  #
# ------------------------------------------------------------------ #

@bolt_app.action("leave_approve")
async def action_leave_approve(ack, body, action) -> None:
    await ack()
    asyncio.create_task(_process_leave_action(body, action, "leave_approve"))


@bolt_app.action("leave_reject")
async def action_leave_reject(ack, body, action) -> None:
    await ack()
    asyncio.create_task(_process_leave_action(body, action, "leave_reject"))


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
