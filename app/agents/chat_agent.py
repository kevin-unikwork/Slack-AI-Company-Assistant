import logging
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage
from app.config import settings
from app.services.slack_service import slack_service
from app.utils.logger import get_logger

logger = get_logger(__name__)

_llm = ChatOpenAI(
    model="gpt-4o",
    temperature=0.7,
    openai_api_key=settings.openai_api_key
)

async def generate_chat_reply(slack_id: str, text: str) -> str:
    """
    Generate a natural response and suggest relevant bot features.
    Returns the string reply.
    """
    system_prompt = """
    You are a friendly and helpful Company AI Assistant. 
    Your goal is to provide a natural, conversational response to the user's message.
    
    If the user is just saying hello or asking how you are, be warm and polite.
    If they are asking a question that seems related to company life but isn't a specific policy query, give a helpful general answer.
    
    CRITICAL: Always end your response by subtly suggesting one or two things the user can do with you, such as:
    - Asking about company policies (e.g., WFH, leave types).
    - Setting reminders (e.g., "Remind me in 1 hour to check mail").
    - Applying for leave.
    - Submitting anonymous feedback.
    
    Keep the response concise, professional, and friendly. Use emojis sparingly but effectively.
    Never give the exact same response twice.
    """
    
    try:
        response = await _llm.ainvoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content=text)
        ])
        
        return response.content.strip()
        
    except Exception as exc:
        logger.error(f"Chat agent failed: {exc}")
        return (
            "Hi there! I'm here to help. You can ask me about company policies, "
            "set reminders, apply for leave, or submit feedback. What can I do for you today? 😊"
        )
