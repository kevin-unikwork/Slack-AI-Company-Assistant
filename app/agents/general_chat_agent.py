from __future__ import annotations

import time
from collections import defaultdict

from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage, AIMessage

from app.config import settings
from app.utils.logger import get_logger

logger = get_logger(__name__)

_llm = ChatOpenAI(
    model="gpt-4o",
    temperature=0.4,
    openai_api_key=settings.openai_api_key,
)

_chat_memory: dict[str, list[tuple[str, str, float]]] = defaultdict(list)
_MAX_TURNS = 6
_MEMORY_TTL_SECONDS = 60 * 30

_SYSTEM_PROMPT = """You are Company AI Bot for an IT services company.
Be concise, friendly, and practical.
If the user asks about policy, leave, standup, HR, onboarding, or feedback, guide them to the right bot feature.
If you don't know company-specific facts, say so clearly and suggest checking HR/policy docs.
Never invent internal policy details.
"""


def _prune_memory(slack_id: str) -> None:
    now = time.time()
    recent = [turn for turn in _chat_memory.get(slack_id, []) if now - turn[2] <= _MEMORY_TTL_SECONDS]
    if len(recent) > _MAX_TURNS:
        recent = recent[-_MAX_TURNS:]
    _chat_memory[slack_id] = recent


async def reply_general_chat(slack_id: str, user_text: str) -> str:
    """Generate a conversational reply for general DMs."""
    try:
        _prune_memory(slack_id)
        turns = _chat_memory.get(slack_id, [])

        messages = [SystemMessage(content=_SYSTEM_PROMPT)]
        for role, content, _ in turns:
            if role == "user":
                messages.append(HumanMessage(content=content))
            else:
                messages.append(AIMessage(content=content))
        messages.append(HumanMessage(content=user_text))

        response = await _llm.ainvoke(messages)
        answer = (response.content or "").strip()
        if not answer:
            answer = "I’m here. Try asking about leave, policy, standup, or any work question."

        now = time.time()
        _chat_memory[slack_id].append(("user", user_text, now))
        _chat_memory[slack_id].append(("assistant", answer, now))
        _prune_memory(slack_id)

        return answer
    except Exception:
        logger.exception("General chat reply failed", extra={"slack_id": slack_id})
        return (
            "I hit a temporary issue. You can still use:\n"
            "• `/applyleave` for leave requests\n"
            "• `/standup` for daily standup\n"
            "• `/policy <question>` for policy Q&A\n"
            "• `/feedback <message>` for anonymous feedback"
        )
