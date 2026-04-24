from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.broadcast import BroadcastLog
from app.db.models.user import User
from app.services.slack_service import slack_service
from app.utils.logger import get_logger
from app.utils.exceptions import BroadcastError, AuthorizationError

logger = get_logger(__name__)


async def send_broadcast(
    session: AsyncSession,
    sender_slack_id: str,
    message_text: str,
    sender_user: User,
) -> dict:
    """
    Send an announcement DM to every active employee.
    Only HR admins may trigger this.

    Returns: {"sent": int, "failed": int, "broadcast_id": int}
    """
    if not sender_user.is_hr_admin:
        raise AuthorizationError(
            f"User {sender_slack_id} is not an HR admin and cannot send broadcasts."
        )

    # 1. Extract mentions in multiple formats
    import re
    from sqlalchemy import select
    
    # Slack formatted mentions: <@U123456>
    formatted_mentions = re.findall(r"<@(U[A-Z0-9]+)>", message_text)
    
    # Raw text mentions: @username (common in slash commands)
    raw_mentions = re.findall(r"@([a-zA-Z0-9\.\-_]+)", message_text)
    
    slack_ids = set(formatted_mentions)
    
    # Resolve raw usernames to Slack IDs using the DB
    if raw_mentions:
        from sqlalchemy import func
        result = await session.execute(
            select(User.slack_id).where(
                func.lower(User.slack_username).in_([m.lower() for m in raw_mentions])
            )
        )
        found_ids = result.scalars().all()
        slack_ids.update(found_ids)

    # 2. Determine target list
    if slack_ids:
        # Targeted broadcast: Only send to mentioned users
        final_ids = list(slack_ids)
        if sender_slack_id in final_ids:
            final_ids.remove(sender_slack_id)
        
        logger.info(
            "Targeted broadcast starting",
            extra={"sender": sender_slack_id, "recipient_count": len(final_ids), "targets": final_ids},
        )
    else:
        # Global broadcast: Fetch all active users
        result = await session.execute(select(User).where(User.is_active == True))
        all_users = list(result.scalars().all())
        final_ids = [u.slack_id for u in all_users if u.slack_id != sender_slack_id]

        logger.info(
            "Global broadcast starting",
            extra={"sender": sender_slack_id, "recipient_count": len(final_ids)},
        )

    # 3. Build Slack blocks for announcement
    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "📢 Company Announcement", "emoji": True},
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": message_text},
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f"_Sent by HR | {datetime.now(timezone.utc).strftime('%d %b %Y %H:%M UTC')}_",
                }
            ],
        },
    ]

    sent, failed = await slack_service.broadcast_dm(
        slack_ids=final_ids,
        text=f"📢 Company Announcement: {message_text[:100]}...",
        blocks=blocks,
    )

    # Log to DB
    broadcast_log = BroadcastLog(
        sender_slack_id=sender_slack_id,
        message_text=message_text,
        recipient_count=sent,
        failed_count=failed,
        sent_at=datetime.now(timezone.utc),
    )
    session.add(broadcast_log)
    await session.flush()

    logger.info(
        "Broadcast complete",
        extra={"sent": sent, "failed": failed, "broadcast_id": broadcast_log.id},
    )
    return {"sent": sent, "failed": failed, "broadcast_id": broadcast_log.id}