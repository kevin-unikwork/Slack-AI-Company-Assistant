import asyncio
from typing import Any

from slack_sdk.web.async_client import AsyncWebClient
from slack_sdk.errors import SlackApiError

from app.config import settings
from app.utils.logger import get_logger
from app.utils.exceptions import SlackServiceError

logger = get_logger(__name__)


class SlackService:
    """
    Centralised wrapper around the Slack Web API.
    All Slack API calls in the application MUST go through this class.
    Never instantiate AsyncWebClient outside this module.
    """

    def __init__(self) -> None:
        self._client = AsyncWebClient(token=settings.slack_bot_token)

    # ------------------------------------------------------------------ #
    # DMs & channel posting                                              #
    # ------------------------------------------------------------------ #

    async def dm_user(
        self,
        slack_id: str,
        text: str,
        blocks: list[dict] | None = None,
    ) -> str:
        """
        Send a direct message to a user.
        Returns the message timestamp (ts) on success.
        """
        try:
            # Open a DM channel first
            im_response = await self._client.conversations_open(users=[slack_id])
            channel_id: str = im_response["channel"]["id"]

            kwargs: dict[str, Any] = {"channel": channel_id, "text": text}
            if blocks:
                kwargs["blocks"] = blocks

            response = await self._client.chat_postMessage(**kwargs)
            ts: str = response["ts"]
            logger.info(
                "DM sent",
                extra={"slack_id": slack_id, "channel": channel_id, "ts": ts},
            )
            return ts
        except SlackApiError as exc:
            logger.exception(
                "Failed to send DM",
                extra={"slack_id": slack_id, "error": str(exc)},
            )
            raise SlackServiceError(
                f"Could not DM user {slack_id}: {exc.response['error']}",
                slack_error_code=exc.response.get("error"),
            ) from exc

    async def post_to_channel(
        self,
        channel: str,
        text: str,
        blocks: list[dict] | None = None,
    ) -> str:
        """
        Post a message to a public/private channel or channel name.
        Returns the message timestamp (ts).
        """
        try:
            target_channel = channel
            
            # If it's a channel name like #general, find the ID and join
            if channel.startswith("#"):
                chan_id = await self._get_channel_id_by_name(channel[1:])
                if chan_id:
                    target_channel = chan_id
                    # Try to join (only works for public channels)
                    try:
                        await self._client.conversations_join(channel=target_channel)
                    except SlackApiError:
                        # Might be a private channel we're already in, or join is restricted
                        pass

            kwargs: dict[str, Any] = {"channel": target_channel, "text": text}
            if blocks:
                kwargs["blocks"] = blocks

            try:
                response = await self._client.chat_postMessage(**kwargs)
            except SlackApiError as e:
                if e.response.get("error") == "not_in_channel":
                    logger.info(f"Bot not in {channel}, attempting emergency join...")
                    try:
                        await self._client.conversations_join(channel=target_channel)
                        response = await self._client.chat_postMessage(**kwargs)
                    except SlackApiError as join_err:
                        logger.error(f"Failed to join channel {channel}: {join_err}")
                        raise SlackServiceError(
                            f"Bot is not in {channel} and lacks 'channels:join' scope to join automatically. Please invite the bot to this channel manually.",
                            slack_error_code="not_in_channel"
                        ) from join_err
                else:
                    raise

            ts: str = response["ts"]
            logger.info(
                "Channel message posted",
                extra={"channel": channel, "ts": ts},
            )
            return ts
        except SlackApiError as exc:
            logger.exception(
                "Failed to post to channel",
                extra={"channel": channel, "error": str(exc)},
            )
            raise SlackServiceError(
                f"Could not post to {channel}: {exc.response['error']}",
                slack_error_code=exc.response.get("error"),
            ) from exc

    async def _get_channel_id_by_name(self, name: str) -> str | None:
        """Helper to find a channel ID by its human-readable name."""
        try:
            cursor = None
            while True:
                response = await self._client.conversations_list(
                    types="public_channel,private_channel",
                    cursor=cursor,
                    limit=500
                )
                for channel in response.get("channels", []):
                    if channel["name"] == name:
                        return channel["id"]
                
                cursor = response.get("response_metadata", {}).get("next_cursor")
                if not cursor:
                    break
            return None
        except SlackApiError:
            return None

    async def update_message(
        self,
        channel: str,
        ts: str,
        text: str,
        blocks: list[dict] | None = None,
    ) -> None:
        """Update an existing message in place (e.g. after leave approval)."""
        try:
            kwargs: dict[str, Any] = {"channel": channel, "ts": ts, "text": text}
            if blocks:
                kwargs["blocks"] = blocks
            await self._client.chat_update(**kwargs)
            logger.info("Message updated", extra={"channel": channel, "ts": ts})
        except SlackApiError as exc:
            logger.exception(
                "Failed to update message",
                extra={"channel": channel, "ts": ts, "error": str(exc)},
            )
            raise SlackServiceError(
                f"Could not update message in {channel}: {exc.response['error']}",
                slack_error_code=exc.response.get("error"),
            ) from exc

    async def add_reaction(self, channel: str, ts: str, emoji: str) -> None:
        """Add an emoji reaction to a message."""
        try:
            await self._client.reactions_add(channel=channel, timestamp=ts, name=emoji)
        except SlackApiError as exc:
            # Reactions already added is not fatal — swallow it
            if exc.response.get("error") == "already_reacted":
                return
            logger.exception(
                "Failed to add reaction",
                extra={"channel": channel, "ts": ts, "emoji": emoji},
            )
            raise SlackServiceError(
                f"Could not add reaction :{emoji}: to {ts}",
                slack_error_code=exc.response.get("error"),
            ) from exc

    # ------------------------------------------------------------------ #
    # User / workspace info                                                #
    # ------------------------------------------------------------------ #

    async def get_user_info(self, slack_id: str) -> dict[str, Any]:
        """
        Fetch a user's profile from the Slack API.
        Returns the full 'user' dict from users.info.
        """
        try:
            response = await self._client.users_info(user=slack_id)
            return response["user"]  # type: ignore[return-value]
        except SlackApiError as exc:
            logger.exception(
                "Failed to get user info",
                extra={"slack_id": slack_id, "error": str(exc)},
            )
            raise SlackServiceError(
                f"Could not fetch info for {slack_id}: {exc.response['error']}",
                slack_error_code=exc.response.get("error"),
            ) from exc

    async def get_all_workspace_users(self) -> list[dict[str, Any]]:
        """
        Return a list of all non-bot, non-deleted workspace members.
        Handles cursor-based pagination automatically.
        """
        users: list[dict[str, Any]] = []
        cursor: str | None = None

        try:
            while True:
                kwargs: dict[str, Any] = {"limit": 200}
                if cursor:
                    kwargs["cursor"] = cursor

                response = await self._client.users_list(**kwargs)
                members: list[dict] = response.get("members", [])

                for member in members:
                    if not member.get("is_bot") and not member.get("deleted") and member["id"] != "USLACKBOT":
                        users.append(member)

                cursor = response.get("response_metadata", {}).get("next_cursor")
                if not cursor:
                    break

            logger.info("Workspace users fetched", extra={"count": len(users)})
            return users
        except SlackApiError as exc:
            logger.exception("Failed to list workspace users", extra={"error": str(exc)})
            raise SlackServiceError(
                f"Could not list workspace users: {exc.response['error']}",
                slack_error_code=exc.response.get("error"),
            ) from exc

    async def broadcast_dm(
        self,
        slack_ids: list[str],
        text: str,
        blocks: list[dict] | None = None,
    ) -> tuple[int, int]:
        """
        Send a DM to every user in `slack_ids`.
        Rate-limited to ~1 msg/sec (Slack Tier 3 limit).
        Returns (sent_count, failed_count).
        """
        sent = 0
        failed = 0
        for slack_id in slack_ids:
            try:
                await self.dm_user(slack_id, text, blocks)
                sent += 1
            except SlackServiceError:
                failed += 1
                logger.warning("Broadcast DM failed for user", extra={"slack_id": slack_id})
            await asyncio.sleep(0.1)  # ~10 msgs/sec, well under Slack's 1/sec burst limit

        logger.info(
            "Broadcast complete",
            extra={"sent": sent, "failed": failed, "total": len(slack_ids)},
        )
        return sent, failed


# Module-level singleton — import and use this everywhere
slack_service = SlackService()