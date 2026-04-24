from __future__ import annotations

from typing import Optional


class AppError(Exception):
    """Base application exception."""


class DatabaseError(AppError):
    """Raised for database operation failures."""


class UserNotFoundError(AppError):
    """Raised when an expected user does not exist."""


class DocumentNotFoundError(AppError):
    """Raised when an expected policy document does not exist."""


class AuthenticationError(AppError):
    """Raised for authentication failures."""


class AuthorizationError(AppError):
    """Raised when a caller does not have sufficient permissions."""


class SlackServiceError(AppError):
    """Raised for Slack API failures."""

    def __init__(self, message: str, slack_error_code: Optional[str] = None) -> None:
        super().__init__(message)
        self.slack_error_code = slack_error_code


class IntentClassificationError(AppError):
    """Raised when intent classification fails."""


class StandupAgentError(AppError):
    """Raised for standup flow failures."""


class PolicyAgentError(AppError):
    """Raised for policy ingestion or retrieval failures."""


class BroadcastError(AppError):
    """Raised for broadcast workflow failures."""


class OnboardingError(AppError):
    """Raised for onboarding workflow failures."""


class LeaveAgentError(AppError):
    """Raised for leave workflow failures."""
