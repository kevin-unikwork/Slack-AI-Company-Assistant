import re
import logging
from datetime import datetime

from app.config import settings
from app.db.session import AsyncSessionLocal
from app.db.models.kudos import Kudos
from app.services.slack_service import slack_service

logger = logging.getLogger(__name__)

async def handle_kudos_command(sender_slack_id: str, text: str) -> str:
    """
    Processes the /kudos command.
    Format: /kudos @user <message>
    """
    text = text.strip()
    if not text:
        return "Usage: `/kudos @user for doing a great job!`"

    # Regex to find Slack user mention: <@U12345678>
    mention_match = re.search(r"<@([A-Z0-9]+)>", text)
    if not mention_match:
        return "Please mention the user you want to give kudos to (e.g., `/kudos @user ...`)."

    receiver_slack_id = mention_match.group(1)
    
    # Check if user is giving kudos to themselves
    if sender_slack_id == receiver_slack_id:
        return "You're doing great, but self-kudos don't count here! 😉"

    # Extract the message (everything after the mention)
    # The message might contain the mention anywhere, but usually it's at the start.
    # Let's just remove the mention from the text to get the message.
    message = re.sub(r"<@[A-Z0-9]+>", "", text).strip()
    if not message:
        return f"Please include a message explaining why you're giving kudos to <@{receiver_slack_id}>!"

    try:
        # 1. Save to Database
        async with AsyncSessionLocal() as session:
            async with session.begin():
                new_kudos = Kudos(
                    sender_slack_id=sender_slack_id,
                    receiver_slack_id=receiver_slack_id,
                    message=message,
                    created_at=datetime.utcnow()
                )
                session.add(new_kudos)

        # 2. Notify the receiver via DM
        dm_text = f"🚀 *You just received Kudos from <@{sender_slack_id}>!*"
        dm_blocks = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": dm_text
                }
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"> {message}"
                }
            }
        ]
        await slack_service.dm_user(receiver_slack_id, text=dm_text, blocks=dm_blocks)

        # 3. Post to the public kudos channel
        public_text = f"🌟 *Kudos Alert!* <@{sender_slack_id}> just gave a shout-out to <@{receiver_slack_id}>!"
        public_blocks = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": public_text
                }
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"> {message}"
                }
            }
        ]
        await slack_service.post_to_channel(settings.kudos_channel, text=public_text, blocks=public_blocks)

        return f"Successfully sent kudos to <@{receiver_slack_id}>! 🎊"

    except Exception as e:
        logger.exception("Error handling kudos command")
        return "Oops! Something went wrong while saving your kudos. Please try again later."
