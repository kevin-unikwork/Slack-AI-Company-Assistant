from enum import Enum

import redis.asyncio as aioredis
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage

from app.config import settings
from app.utils.logger import get_logger
from app.utils.exceptions import IntentClassificationError
from app.utils.state import state_manager

logger = get_logger(__name__)

# Module-level LLM instance (safe to reuse across requests)
_llm = ChatOpenAI(
    model="gpt-4o-mini",
    temperature=0,
    openai_api_key=settings.openai_api_key,
)

SYSTEM_PROMPT = """You are an intent classifier for a company Slack bot.
Classify the user message into EXACTLY ONE of these intents:

- standup_response   : User is answering a standup question (yesterday/today/blockers).
- policy_qa          : User is asking about company policies, HR rules, procedures, 
                       OR asking about company information, leadership (CEO, CTO, etc.), 
                       and contact details for staff.
- leave_request      : User wants to apply for or check on leave/vacation/time-off.
- feedback           : User wants to submit anonymous feedback or a complaint.
- general_chat       : Greetings, small talk, or off-topic questions.

Respond with ONLY the intent label, nothing else. No punctuation, no explanation.
"""


class Intent(str, Enum):
    STANDUP_RESPONSE = "standup_response"
    POLICY_QA = "policy_qa"
    LEAVE_REQUEST = "leave_request"
    GENERAL_CHAT = "general_chat"
    FEEDBACK = "feedback"


async def get_user_state(slack_id: str) -> str | None:
    """Return the current conversation state key for a user, or None."""
    return await state_manager.get_state(f"session:{slack_id}")


async def classify_intent(slack_id: str, message: str) -> Intent:
    """
    Classify `message` into an Intent.

    Priority rules:
    1. If the user is mid-standup (state = standup_step_*), always return STANDUP_RESPONSE.
    2. If the user is mid-leave-flow (state = leave_*), always return LEAVE_REQUEST.
    3. Otherwise, call GPT-4o to classify.
    """
    try:
        # Check shared state manager (Redis with in-memory fallback)
        standup_step = await state_manager.get_state(f"standup:{slack_id}:step")
        if standup_step and int(standup_step) in (1, 2, 3):
            logger.info(
                "Intent overridden by standup state",
                extra={"slack_id": slack_id, "step": standup_step},
            )
            return Intent.STANDUP_RESPONSE

        leave_state = await state_manager.get_state(f"leave:{slack_id}:state")
        if leave_state and leave_state not in ("done", "cancelled"):
            logger.info(
                "Intent overridden by leave state",
                extra={"slack_id": slack_id, "leave_state": leave_state},
            )
            return Intent.LEAVE_REQUEST

    except Exception as exc:
        logger.warning(
            "State manager check failed, will use fallback detection",
            extra={"error": str(exc), "slack_id": slack_id},
        )

    try:
        response = await _llm.ainvoke([
            SystemMessage(content=SYSTEM_PROMPT),
            HumanMessage(content=message.strip()[:500]),  # cap to avoid token waste
        ])
        raw = response.content.strip().lower().replace("-", "_")

        try:
            intent = Intent(raw)
        except ValueError:
            logger.warning(
                "LLM returned unknown intent, defaulting to general_chat",
                extra={"raw": raw, "slack_id": slack_id},
            )
            intent = Intent.GENERAL_CHAT

        logger.info(
            "Intent classified",
            extra={"slack_id": slack_id, "intent": intent.value, "message_preview": message[:80]},
        )
        return intent

    except Exception as exc:
        logger.exception("Intent classification LLM call failed", extra={"slack_id": slack_id})
        raise IntentClassificationError(f"Classification failed: {exc}") from exc
