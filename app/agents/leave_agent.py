from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone

from sqlalchemy import select, update

from app.db.models.leave import LeaveRequest
from app.db.models.user import User
from app.db.session import AsyncSessionLocal
from app.services.slack_service import slack_service
from app.utils.logger import get_logger

from app.utils.state import state_manager

logger = get_logger(__name__)

LEAVE_STATE_KEY = "leave:{slack_id}:state"
LEAVE_DATA_KEY = "leave:{slack_id}:data"
LEAVE_TTL = 60 * 60  # 1 hour

DATE_RANGE_RE = re.compile(
    r"(\d{1,2})[/\-](\d{1,2})[/\-](\d{2,4})\s+(?:to|–|-)\s+(\d{1,2})[/\-](\d{1,2})[/\-](\d{2,4})",
    re.IGNORECASE,
)


# ------------------------------------------------------------------ #
# Date parsing helpers                                                 #
# ------------------------------------------------------------------ #

def _parse_date(d: str, m: str, y: str) -> datetime:
    year = int(y)
    if year < 100:
        year += 2000
    return datetime(year, int(m), int(d))


def _parse_date_range(text: str) -> tuple[datetime, datetime] | None:
    match = DATE_RANGE_RE.search(text)
    if not match:
        return None
    d1, m1, y1, d2, m2, y2 = match.groups()
    try:
        start = _parse_date(d1, m1, y1)
        end = _parse_date(d2, m2, y2)
        return start, end
    except ValueError:
        return None


def _days_between(start: datetime, end: datetime) -> int:
    return max(1, (end.date() - start.date()).days + 1)


def _leave_request_blocks(
    slack_id: str,
    leave_id: int,
    start: datetime,
    end: datetime,
    days: int,
    reason: str,
) -> list[dict]:
    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f":calendar: *Leave Request from <@{slack_id}>*\n"
                    f"*Dates:* {start.strftime('%d %b %Y')} → {end.strftime('%d %b %Y')} "
                    f"({days} day{'s' if days != 1 else ''})\n"
                    f"*Reason:* {reason}"
                ),
            },
        },
        {
            "type": "actions",
            "block_id": f"leave_{leave_id}",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Approve"},
                    "style": "primary",
                    "action_id": "leave_approve",
                    "value": str(leave_id),
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Reject"},
                    "style": "danger",
                    "action_id": "leave_reject",
                    "value": str(leave_id),
                },
            ],
        },
    ]


# ------------------------------------------------------------------ #
# Conversation state machine                                           #
# ------------------------------------------------------------------ #

async def start_leave_conversation(slack_id: str) -> None:
    """Entry point: user said /leave or 'I want to apply for leave'."""
    await state_manager.set_state(LEAVE_STATE_KEY.format(slack_id=slack_id), "awaiting_dates", LEAVE_TTL)
    await state_manager.delete_state(LEAVE_DATA_KEY.format(slack_id=slack_id))

    await slack_service.dm_user(
        slack_id,
        ":calendar: Sure! Please provide your *leave start and end dates* in the format:\n"
        "`DD/MM/YYYY to DD/MM/YYYY`",
    )
    logger.info("Leave conversation started", extra={"slack_id": slack_id})


async def handle_leave_message(slack_id: str, message: str) -> None:
    """Route an incoming message to the correct leave conversation step."""
    state = await state_manager.get_state(LEAVE_STATE_KEY.format(slack_id=slack_id))

    if not state or state in ("done", "cancelled"):
        await start_leave_conversation(slack_id)
        return

    if state == "awaiting_dates":
        await _handle_dates(slack_id, message)
    elif state == "awaiting_reason":
        await _handle_reason(slack_id, message)
    else:
        await start_leave_conversation(slack_id)


async def _handle_dates(slack_id: str, message: str) -> None:
    parsed = _parse_date_range(message)
    if not parsed:
        await slack_service.dm_user(
            slack_id,
            ":x: I couldn't parse those dates. Please use the format: `DD/MM/YYYY to DD/MM/YYYY`\n"
            "Example: `15/07/2025 to 19/07/2025`",
        )
        return

    start, end = parsed
    if end < start:
        await slack_service.dm_user(
            slack_id, ":x: End date must be on or after start date. Please try again."
        )
        return

    days = _days_between(start, end)

    data = {"start": start.isoformat(), "end": end.isoformat(), "days": days}
    await state_manager.set_state(LEAVE_DATA_KEY.format(slack_id=slack_id), data, LEAVE_TTL)
    await state_manager.set_state(LEAVE_STATE_KEY.format(slack_id=slack_id), "awaiting_reason", LEAVE_TTL)

    await slack_service.dm_user(
        slack_id,
        f":white_check_mark: Got it — *{start.strftime('%d %b %Y')}* to *{end.strftime('%d %b %Y')}* "
        f"({days} day{'s' if days != 1 else ''}).\n\n"
        "What's the *reason* for your leave? (type `skip` if you'd prefer not to share)",
    )


async def _handle_reason(slack_id: str, message: str) -> None:
    data_raw = await state_manager.get_state(LEAVE_DATA_KEY.format(slack_id=slack_id))
    if not data_raw:
        await start_leave_conversation(slack_id)
        return

    data = json.loads(data_raw)
    start = datetime.fromisoformat(data["start"])
    end = datetime.fromisoformat(data["end"])
    days: int = data["days"]
    reason = "" if message.lower().strip() == "skip" else message.strip()

    async with AsyncSessionLocal() as session:
        async with session.begin():
            result = await session.execute(select(User).where(User.slack_id == slack_id))
            user = result.scalar_one_or_none()

            manager_slack_id = user.manager_slack_id if user else None
            user_name = (user.full_name or user.slack_username) if user else slack_id

            leave = LeaveRequest(
                user_slack_id=slack_id,
                manager_slack_id=manager_slack_id,
                start_date=start,
                end_date=end,
                reason=reason or None,
                status="pending",
            )
            session.add(leave)
            await session.flush()
            leave_id = leave.id

    # Confirm to employee
    await slack_service.dm_user(
        slack_id,
        f":white_check_mark: *Leave request submitted!*\n"
        f"• *Dates:* {start.strftime('%d %b %Y')} – {end.strftime('%d %b %Y')} ({days} days)\n"
        f"• *Reason:* {reason or 'Not provided'}\n"
        f"• *Status:* Pending approval\n\n"
        "Your manager will be notified. You'll receive a DM when a decision is made.",
    )

    # Notify HR Managers
    try:
        async with AsyncSessionLocal() as session:
            async with session.begin():
                hr_result = await session.execute(
                    select(User).where(User.is_hr_admin == True)
                )
                hr_managers = list(hr_result.scalars().all())

        if hr_managers:
            blocks = _leave_request_blocks(slack_id, leave_id, start, end, days, reason or "Not provided")
            for hr in hr_managers:
                # Open DM with HR Manager and post
                im_resp = await slack_service._client.conversations_open(users=[hr.slack_id])
                channel_id: str = im_resp["channel"]["id"]
                msg_resp = await slack_service._client.chat_postMessage(
                    channel=channel_id,
                    text=f"Leave request from {user_name}",
                    blocks=blocks,
                )
                
                # We store the first notification as the main reference for simplicity, 
                # or we could store all. For this system, we'll store the latest.
                async with AsyncSessionLocal() as session:
                    async with session.begin():
                        res = await session.execute(select(LeaveRequest).where(LeaveRequest.id == leave_id))
                        leave_row = res.scalar_one()
                        leave_row.manager_message_ts = msg_resp["ts"]
                        leave_row.manager_channel = channel_id
                        leave_row.manager_slack_id = hr.slack_id # Record who was notified

                logger.info(
                    "HR Manager notified of leave request",
                    extra={"leave_id": leave_id, "hr_manager": hr.slack_id},
                )
        else:
            logger.warning("No HR Managers found to notify for leave request", extra={"leave_id": leave_id})

    except Exception:
        logger.exception("Failed to notify HR Managers of leave request", extra={"leave_id": leave_id})

    await state_manager.set_state(LEAVE_STATE_KEY.format(slack_id=slack_id), "done", LEAVE_TTL)
    await state_manager.delete_state(LEAVE_DATA_KEY.format(slack_id=slack_id))


# ------------------------------------------------------------------ #
# Button action handlers                                               #
# ------------------------------------------------------------------ #

async def handle_leave_action(
    leave_id: int,
    action: str,
    manager_slack_id: str,
    channel_id: str,
    message_ts: str,
) -> None:
    """Process approve/reject button click from manager."""
    new_status = "approved" if action == "leave_approve" else "rejected"
    emoji = ":white_check_mark:" if new_status == "approved" else ":x:"

    async with AsyncSessionLocal() as session:
        async with session.begin():
            result = await session.execute(select(LeaveRequest).where(LeaveRequest.id == leave_id))
            leave = result.scalar_one_or_none()
            if not leave:
                logger.error("Leave request not found for action", extra={"leave_id": leave_id})
                return

            leave.status = new_status
            leave.resolved_at = datetime.now(timezone.utc)
            employee_slack_id = leave.user_slack_id
            start = leave.start_date
            end = leave.end_date

    # Update manager's DM to replace buttons with decision text
    updated_blocks = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"{emoji} *Leave Request {new_status.upper()}*\n"
                    f"<@{employee_slack_id}> | "
                    f"{start.strftime('%d %b %Y')} – {end.strftime('%d %b %Y')}\n"
                    f"_Decision made by <@{manager_slack_id}>_"
                ),
            },
        }
    ]
    try:
        await slack_service.update_message(
            channel=channel_id,
            ts=message_ts,
            text=f"Leave request {new_status}",
            blocks=updated_blocks,
        )
    except Exception:
        logger.warning("Could not update manager's leave message", extra={"leave_id": leave_id})

    # Notify employee
    await slack_service.dm_user(
        employee_slack_id,
        f"{emoji} Your leave request ({start.strftime('%d %b %Y')} – {end.strftime('%d %b %Y')}) "
        f"has been *{new_status}* by your manager.",
    )

    logger.info(
        "Leave action processed",
        extra={
            "leave_id": leave_id,
            "status": new_status,
            "manager": manager_slack_id,
            "employee": employee_slack_id,
        },
    )