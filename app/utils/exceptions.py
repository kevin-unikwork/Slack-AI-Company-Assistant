class SlackBotBaseError(Exception):
    """Base exception for all SlackBot errors."""


class SlackServiceError(SlackBotBaseError):
    """Raised when a Slack API call fails."""

    def __init__(self, message: str, slack_error_code: str | None = None) -> None:
        super().__init__(message)
        self.slack_error_code = slack_error_code


class IntentClassificationError(SlackBotBaseError):
    """Raised when intent classification fails or returns an unknown intent."""


class PolicyAgentError(SlackBotBaseError):
    """Raised when the policy RAG agent encounters an error."""


class StandupAgentError(SlackBotBaseError):
    """Raised when the standup state machine encounters an error."""


class LeaveAgentError(SlackBotBaseError):
    """Raised when the leave request conversation encounters an error."""


class BroadcastError(SlackBotBaseError):
    """Raised when an HR broadcast fails."""


class OnboardingError(SlackBotBaseError):
    """Raised when the onboarding flow fails."""


class AuthenticationError(SlackBotBaseError):
    """Raised for JWT auth failures."""


class AuthorizationError(SlackBotBaseError):
    """Raised when a user lacks required permissions (e.g. not HR admin)."""


class UserNotFoundError(SlackBotBaseError):
    """Raised when a user record is not found in the database."""


class DatabaseError(SlackBotBaseError):
    """Raised for unrecoverable database operation failures."""


class DocumentNotFoundError(SlackBotBaseError):
    """Raised when a policy document record is not found."""