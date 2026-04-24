import asyncio

from app.config import settings
from app.db.session import AsyncSessionLocal
from app.db.models.user import User
from app.services.slack_service import slack_service
from app.services.user_service import user_service
from app.schemas.user import UserCreate
from app.utils.logger import get_logger
from app.utils.exceptions import OnboardingError

logger = get_logger(__name__)

WELCOME_MESSAGE = """:tada: *Welcome to the Team! We're thrilled to have you here.* :tada:

I'm your **Company AI Assistant**, designed to make your work life easier and more productive. I can handle everything from HR tasks to intelligent reminders.

---

### :rocket: *What I can do for you:*

• *💡 AI Policy Assistant:* Ask me anything about our company handbook! Try: _"What is our maternity leave policy?"_ or _"Tell me about the travel expense rules."_
• *📅 Intelligent Reminders:* Just tell me in plain English! Try: _"Remind me in 2 hours to check the server logs"_ or _"Remind me at 4 PM tomorrow to call the client."_
• *🏖️ Leave Management:* Check your balance or apply for time off instantly using `/leave` or just tell me: _"I want to take a leave from Monday to Wednesday."_
• *📝 Daily Standups:* I'll check in with you every morning at **9:30 AM IST** to collect your updates and share the team's progress in `#standup`.
• *🎂 Celebrations:* I'll make sure the whole team celebrates your birthdays and work anniversaries with personalized greetings!
• *🤫 Anonymous Feedback:* Have a suggestion? Use `/feedback` to send it safely to HR.

---

### :terminal: *Quick Commands Reference:*

• `/policy <question>` — Instant answers from the company handbook.
• `/reminder <me ... to ...>` — Set natural language reminders.
• `/leave` — Start a leave request.
• `/standup` — Submit your daily update manually.
• `/feedback` — Send anonymous suggestions to the management.

---

### :help: *Frequently Asked Questions:*
"""

FAQS = [
    ("How do I update my profile?", "I pull your info from Slack, so just keep your Slack profile updated with your full name and email!"),
    ("Is my feedback really anonymous?", "Yes! When you use `/feedback`, your name is stripped away before HR sees it."),
    ("Can I set reminders for others?", "Currently, I only set personal reminders, but I'll DM you exactly when it's time!"),
    ("Who is my manager in the system?", "Your manager is assigned during onboarding. If you need to change it, please contact HR."),
    ("What if the AI gives a wrong answer?", "I'm always learning! If a policy answer seems off, please check the official documents in `#general` pinned items."),
]


async def onboard_new_member(slack_id: str) -> None:
    """
    Full onboarding flow for a new workspace member.
    Called from the team_join event handler.
    """
    try:
        # Fetch user info from Slack
        user_info = await slack_service.get_user_info(slack_id)
        profile = user_info.get("profile", {})
        slack_username: str = user_info.get("name", slack_id)
        full_name: str | None = profile.get("real_name") or profile.get("display_name")
        email: str | None = profile.get("email")

        # Persist user in DB
        async with AsyncSessionLocal() as session:
            async with session.begin():
                await user_service.create_or_update(
                    session,
                    UserCreate(
                        slack_id=slack_id,
                        slack_username=slack_username,
                        full_name=full_name,
                        email=email,
                    ),
                )

        logger.info(
            "New user persisted during onboarding",
            extra={"slack_id": slack_id, "username": slack_username},
        )

        # Give the user 30 seconds to settle into the workspace
        await asyncio.sleep(30)

        # Send welcome DM
        faq_text = "\n".join(
            f"• *Q: {q}*\n  A: {a}" for q, a in FAQS
        )
        full_message = WELCOME_MESSAGE + f"\n\n*Frequently Asked Questions:*\n{faq_text}"

        await slack_service.dm_user(
            slack_id,
            text="Welcome to the team! Check this message for everything you need to get started.",
            blocks=[
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": full_message},
                }
            ],
        )

        # Post introduction in the welcome channel
        display_name = full_name or slack_username
        await slack_service.post_to_channel(
            settings.onboarding_welcome_channel,
            text=f"Please welcome <@{slack_id}> to the team!",
            blocks=[
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": (
                            f":tada: Please give a warm welcome to *{display_name}* (<@{slack_id}>)! "
                            "They've just joined the workspace. Say hi! 👋"
                        ),
                    },
                }
            ],
        )

        logger.info("Onboarding complete", extra={"slack_id": slack_id, "full_name": full_name})

    except Exception as exc:
        logger.exception("Onboarding flow failed", extra={"slack_id": slack_id})
        raise OnboardingError(f"Onboarding failed for {slack_id}: {exc}") from exc