from langchain_openai import ChatOpenAI
from langchain_core.prompts import PromptTemplate
from langchain_core.runnables import RunnablePassthrough
from langchain_core.output_parsers import StrOutputParser

from app.config import settings
from app.services.policy_service import policy_service
from app.utils.logger import get_logger
from app.utils.exceptions import PolicyAgentError

logger = get_logger(__name__)

_llm = ChatOpenAI(
    model="gpt-4o",
    temperature=0.1,
    openai_api_key=settings.openai_api_key,
)

_POLICY_PROMPT_TEMPLATE = """You are a STRICT and PROFESSIONAL CORPORATE HR BOT. Your only job is to answer questions based on the provided company policy documents.

### SAFETY & SCOPE RULES (MOST IMPORTANT):
1. IF the question is NOT about company policies, office hours, leaves, HR, or work (e.g., cooking recipes, sports, jokes, personal opinions, coding help), YOU MUST REFUSE TO ANSWER.
2. DO NOT use your own knowledge to answer out-of-scope questions.
3. DO NOT be helpful for non-work tasks.

### EXAMPLE REFUSALS:
- Employee: "How do I make a pizza?"
- You: "I'm sorry, I am only authorized to assist with company-related queries. If you have a question about policies, leaves, or office conduct, feel free to ask!"

- Employee: "Who won the world cup?"
- You: "I'm sorry, I am only authorized to assist with company-related queries. If you have a question about policies, leaves, or office conduct, feel free to ask!"

### FORMATTING RULES:
- Use single asterisks for bold: *Bold Text*.
- Use bullet points (•) for lists.
- Mention the source (e.g., "According to *all_policy.pdf*...").

Context from policy documents:
{context}

Employee question: {question}

Answer:"""

_POLICY_PROMPT = PromptTemplate(
    template=_POLICY_PROMPT_TEMPLATE,
    input_variables=["context", "question"],
)

# Global chain reference
_rag_chain = None

def _get_rag_chain():
    global _rag_chain
    if _rag_chain is None:
        retriever = policy_service.get_retriever()
        
        def format_docs(docs):
            return "\n\n".join(doc.page_content for doc in docs)
        
        _rag_chain = (
            {"context": retriever | format_docs, "question": RunnablePassthrough()}
            | _POLICY_PROMPT
            | _llm
            | StrOutputParser()
        )
    return _rag_chain

async def answer_policy_question(question: str, slack_id: str) -> str:
    """
    Main entry point for policy QA.
    Retrieves context from ChromaDB and generates an answer using LCEL.
    """
    try:
        chain = _get_rag_chain()
        # Use sync invoke to match the sync database connection
        answer = chain.invoke(question)
        return answer.strip()
        
    except Exception as exc:
        logger.exception(f"Policy QA failed for user {slack_id}", extra={"question": question})
        return f"I'm sorry, I encountered an error while searching for that policy.\n*Error:* `{str(exc)}`"

def reset_chain():
    """Reset the global chain (useful for testing or if retriever changes)."""
    global _rag_chain
    _rag_chain = None