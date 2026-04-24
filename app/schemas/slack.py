from pydantic import BaseModel, Field
from typing import Any


class SlackEventPayload(BaseModel):
    """Top-level Slack Events API payload envelope."""
    token: str | None = None
    team_id: str | None = None
    api_app_id: str | None = None
    event: dict[str, Any] | None = None
    type: str
    event_id: str | None = None
    event_time: int | None = None
    challenge: str | None = None  # URL verification


class SlackInteractionPayload(BaseModel):
    """Parsed interactive component payload (button clicks, etc.)."""
    type: str
    callback_id: str | None = None
    action_id: str | None = None
    block_id: str | None = None
    value: str | None = None
    user_id: str | None = None
    channel_id: str | None = None
    message_ts: str | None = None
    response_url: str | None = None


class LeaveActionPayload(BaseModel):
    """Validated data extracted from leave approve/reject button click."""
    action: str = Field(..., pattern="^(leave_approve|leave_reject)$")
    leave_id: int
    manager_slack_id: str
    channel_id: str
    message_ts: str