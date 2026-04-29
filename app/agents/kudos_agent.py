import re
import logging
from datetime import datetime
from sqlalchemy import select, func

from app.config import settings
from app.db.session import AsyncSessionLocal
from app.db.models.kudos import Kudos
from app.db.models.user import User
from app.services.slack_service import slack_service

logger = logging.getLogger(__name__)

async def handle_kudos_command(sender_slack_id: str, text: str) -> str:
    """
    Processes the /kudos command with Smart Name Recognition.
    """
    text = text.strip()
    if not text:
        return "Usage: `/kudos @user for doing a great job!`"

    receiver_slack_id = None
    message = text

    # 1. Try to find a technical mention: <@U12345678>
    mention_match = re.search(r"<@([A-Z0-9]+)>", text)
    if mention_match:
        receiver_slack_id = mention_match.group(1)
        message = re.sub(r"<@[A-Z0-9]+>", "", text).strip()
    else:
        # 2. Fallback: Search for a name/username after the "@"
        name_match = re.search(r"@([^\s]+)", text)
        if name_match:
            search_term = name_match.group(1).lower()
            async with AsyncSessionLocal() as session:
                # Search by username or full name
                result = await session.execute(
                    select(User.slack_id).where(
                        (func.lower(User.slack_username) == search_term) |
                        (func.lower(User.full_name).contains(search_term))
                    )
                )
                receiver_slack_id = result.scalars().first()
                if receiver_slack_id:
                    message = re.sub(r"@[^\s]+", "", text).strip()

    if not receiver_slack_id:
        return "I couldn't find that user. Please use the Slack '@' picker or type their exact username."

    if sender_slack_id == receiver_slack_id:
        return "Self-kudos are great for the soul, but let's keep this for recognizing others! 😉"

    if not message:
        return f"Please include a message for <@{receiver_slack_id}>!"

    try:
        async with AsyncSessionLocal() as session:
            async with session.begin():
                new_kudos = Kudos(
                    sender_slack_id=sender_slack_id,
                    receiver_slack_id=receiver_slack_id,
                    message=message,
                    created_at=datetime.utcnow()
                )
                session.add(new_kudos)

        # Notify recipient
        dm_text = f"🚀 *You just received Kudos from <@{sender_slack_id}>!*"
        dm_blocks = [
            {"type": "section", "text": {"type": "mrkdwn", "text": dm_text}},
            {"type": "section", "text": {"type": "mrkdwn", "text": f"> {message}"}}
        ]
        await slack_service.dm_user(receiver_slack_id, text=dm_text, blocks=dm_blocks)

        # Post to public channel
        public_text = f"🌟 *Kudos Alert!* <@{sender_slack_id}> just gave a shout-out to <@{receiver_slack_id}>!"
        public_blocks = [
            {"type": "section", "text": {"type": "mrkdwn", "text": public_text}},
            {"type": "section", "text": {"type": "mrkdwn", "text": f"> {message}"}}
        ]
        await slack_service.post_to_channel(settings.kudos_channel, text=public_text, blocks=public_blocks)

        return f"Successfully sent kudos to <@{receiver_slack_id}>! 🎊"

    except Exception:
        logger.exception("Kudos failed")
        return "Oops! Something went wrong. Please try again later."
