from langchain.chains import RetrievalQA
from langchain_openai import ChatOpenAI
from langchain.prompts import PromptTemplate

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

# Chain is initialised lazily on first use so ChromaDB is ready
_qa_chain: RetrievalQA | None = None


def _get_qa_chain() -> RetrievalQA:
    global _qa_chain
    if _qa_chain is None:
        retriever = policy_service.get_retriever()
        _qa_chain = RetrievalQA.from_chain_type(
            llm=_llm,
            chain_type="stuff",
            retriever=retriever,
            return_source_documents=True,
            chain_type_kwargs={"prompt": _POLICY_PROMPT},
        )
        logger.info("Policy QA chain initialised")
    return _qa_chain


async def answer_policy_question(question: str, slack_id: str) -> str:
    """
    Run a RAG query against company policy documents.
    Returns a formatted string ready to send as a Slack message.
    """
    try:
        chain = _get_qa_chain()
        result = await chain.ainvoke({"query": question})

        answer: str = result.get("result", "No answer generated.")
        source_docs = result.get("source_documents", [])

        # Deduplicate source filenames
        sources: list[str] = []
        seen: set[str] = set()
        for doc in source_docs:
            src = doc.metadata.get("source", "Unknown document")
            if src not in seen:
                sources.append(src)
                seen.add(src)

        response_parts = [answer]
        if sources:
            source_list = ", ".join(f"`{s}`" for s in sources)
            response_parts.append(f"\n\n📄 *Source:* {source_list}")

        formatted = "\n".join(response_parts)
        logger.info(
            "Policy question answered",
            extra={
                "slack_id": slack_id,
                "question_preview": question[:80],
                "sources": sources,
            },
        )
        return formatted

    except Exception as exc:
        logger.exception(
            "Policy QA chain failed",
            extra={"slack_id": slack_id, "question_preview": question[:80]},
        )
        raise PolicyAgentError(f"Policy Q&A failed: {exc}") from exc


def reset_chain() -> None:
    """Force re-initialisation of the QA chain (e.g. after new doc ingested)."""
    global _qa_chain
    _qa_chain = None
    logger.info("Policy QA chain reset")