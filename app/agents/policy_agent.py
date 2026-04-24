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

_POLICY_PROMPT_TEMPLATE = """You are the company's helpful HR assistant bot. Your goal is to answer employee questions about company policies using the provided document context.

### FORMATTING RULES (CRITICAL):
- *ALWAYS* use single asterisks for bold text like this: *Bold Title*.
- *NEVER* use double asterisks like **this**. Slack will not bold them properly.
- Use bullet points (•) for all lists.
- Keep responses clean, spaced, and easy to scan.

### HANDLING VERTICAL TEXT:
The provided context may contain tables where text appears vertically (one word per line). You MUST reconstruct these logically to understand the meaning. For example, if you see words like 'Policy', 'Violations', 'result', 'in', 'disciplinary', treat them as a coherent sentence.

### GUIDELINES:
1. Answer the question thoroughly based on the provided context.
2. If the user asks a broad question like 'Discuss company policy' or 'Tell me about policies', provide a summary or overview of the topics covered in the snippets you see.
3. If the answer is absolutely not present in the documents, say: "I don't have specific information about that in our current policy documents. However, I can help with other topics like leaves, conduct, or office hours. For this specific query, please contact HR directly."
4. Never invent policy details.
5. Always mention which document your information comes from (e.g., "According to `all_policy.pdf`...").

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
        # Invoke the LCEL chain
        answer = await chain.ainvoke(question)
        return answer.strip()
        
    except Exception as exc:
        logger.exception(f"Policy QA failed for user {slack_id}", extra={"question": question})
        return "I'm sorry, I encountered an error while searching for that policy. Please try again or contact HR."

def reset_chain():
    """Reset the global chain (useful for testing or if retriever changes)."""
    global _rag_chain
    _rag_chain = None