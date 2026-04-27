import re

from langchain_openai import ChatOpenAI
from langchain_core.prompts import PromptTemplate
from langchain_core.output_parsers import StrOutputParser

from app.config import settings
from app.services.policy_service import policy_service
from app.utils.logger import get_logger

logger = get_logger(__name__)

_llm = ChatOpenAI(
    model="gpt-4o",
    temperature=0.1,
    openai_api_key=settings.openai_api_key,
)

_POLICY_PROMPT_TEMPLATE = """You are a STRICT and PROFESSIONAL CORPORATE HR BOT. Your only job is to answer questions based on the provided company policy documents.

### SAFETY & SCOPE RULES (MOST IMPORTANT):
1. IF the question is about company policies, office hours, leaves, HR, departments, company roles, leadership, or work-related logistics, YOU MUST ANSWER using the context.
2. IF the question is NOT about work (e.g., cooking, sports, jokes, personal opinions), YOU MUST REFUSE TO ANSWER.
3. Use *ONLY* the provided context. Do not use outside knowledge.

### EXAMPLE REFUSALS:
- Employee: "How do I make a pizza?"
- You: "I'm sorry, I am only authorized to assist with company-related queries. If you have a question about policies, leaves, or office conduct, feel free to ask!"

### GUIDELINES:
1. Answer the question thoroughly based on the provided context.
2. If the answer is not present in the documents but the question is work-related, say: "I don't have specific information about that in our current policy documents. However, I can help with other topics like leaves, conduct, or office hours. For this specific query, please contact HR directly."
3. Mention the source (e.g., "According to *all_policy.pdf*...").
4. Use single asterisks for bold: *Bold Text*. Use bullet points (•) for lists.

Context from policy documents:
{context}

Employee question: {question}

Answer:"""

_POLICY_PROMPT = PromptTemplate(
    template=_POLICY_PROMPT_TEMPLATE,
    input_variables=["context", "question"],
)

_TITLE_MODIFIER_WORDS = {
    "manager",
    "lead",
    "head",
    "director",
    "officer",
    "representative",
    "rep",
    "coordinator",
    "administrator",
    "admin",
    "specialist",
    "executive",
    "chief",
    "vp",
    "avp",
    "senior",
    "jr",
    "sr",
    "junior",
    "principal",
}

_QUERY_STOPWORDS = {
    "a",
    "an",
    "the",
    "is",
    "are",
    "was",
    "were",
    "be",
    "to",
    "of",
    "for",
    "in",
    "on",
    "at",
    "by",
    "from",
    "with",
    "and",
    "or",
    "who",
    "what",
    "which",
    "when",
    "where",
    "why",
    "how",
    "do",
    "does",
    "did",
    "can",
    "could",
    "should",
    "would",
    "please",
    "tell",
    "me",
    "about",
}


def _clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def _tokenize_words(text: str) -> list[str]:
    return re.findall(r"[A-Za-z0-9]+", text or "")


def _build_canonical_question(question: str) -> tuple[str, bool]:
    original = _clean_text(question)
    words = _tokenize_words(original)
    if not words:
        return original, False

    kept_words: list[str] = []
    removed_title_modifier = False
    for word in words:
        if word.lower() in _TITLE_MODIFIER_WORDS:
            removed_title_modifier = True
            continue
        kept_words.append(word)

    if not kept_words:
        kept_words = words
        removed_title_modifier = False

    canonical = " ".join(kept_words)
    if original.endswith("?"):
        canonical = canonical.rstrip("?") + "?"

    return canonical, removed_title_modifier


def _build_keyword_query(question: str) -> str:
    words = _tokenize_words(question)
    keywords = [w for w in words if w.lower() not in _QUERY_STOPWORDS and len(w) > 1]
    return " ".join(keywords)


def _build_retrieval_queries(question: str) -> tuple[str, list[str]]:
    original = _clean_text(question)
    canonical, removed_title_modifier = _build_canonical_question(original)
    keyword_query = _build_keyword_query(canonical)
    contact_query = ""
    if removed_title_modifier:
        base = keyword_query or canonical
        if base:
            contact_query = f"{base} contact"

    queries: list[str] = []
    seen = set()
    for query in [original, canonical, keyword_query, contact_query]:
        normalized = _clean_text(query)
        if not normalized:
            continue
        key = normalized.lower()
        if key in seen:
            continue
        seen.add(key)
        queries.append(normalized)

    return canonical or original or question, queries


def _doc_key(doc) -> tuple[str | None, str | None, str]:
    metadata = getattr(doc, "metadata", {}) or {}
    return (
        metadata.get("source"),
        str(metadata.get("page")) if metadata.get("page") is not None else None,
        re.sub(r"\s+", " ", doc.page_content).strip(),
    )


def _merge_retrieved_docs(retrieved_by_query: list[list]) -> list:
    merged = {}
    for query_index, docs in enumerate(retrieved_by_query):
        for rank, doc in enumerate(docs):
            key = _doc_key(doc)
            if key not in merged:
                merged[key] = {
                    "doc": doc,
                    "hits": 0,
                    "first_query_index": query_index,
                    "best_rank": rank,
                }
            merged[key]["hits"] += 1
            merged[key]["best_rank"] = min(merged[key]["best_rank"], rank)

    ranked = sorted(
        merged.values(),
        key=lambda item: (-item["hits"], item["first_query_index"], item["best_rank"]),
    )
    return [item["doc"] for item in ranked]


def _format_docs(docs) -> str:
    formatted_chunks = []
    for doc in docs:
        metadata = getattr(doc, "metadata", {}) or {}
        source = metadata.get("source", "Unknown source")
        page = metadata.get("page")
        page_text = f", Page: {page}" if page is not None else ""
        formatted_chunks.append(f"[Source: {source}{page_text}]\n{doc.page_content.strip()}")
    return "\n\n".join(formatted_chunks)


def _retrieve_policy_docs(question: str):
    canonical_question, queries = _build_retrieval_queries(question)
    retriever = policy_service.get_retriever()

    retrieved_by_query: list[list] = []
    for query in queries:
        try:
            query_docs = retriever.invoke(query)
        except Exception:
            logger.warning("Policy retrieval failed for query variant", extra={"query": query})
            query_docs = []
        retrieved_by_query.append(query_docs)

    merged_docs = _merge_retrieved_docs(retrieved_by_query)
    return canonical_question, queries, merged_docs

async def answer_policy_question(question: str, slack_id: str) -> str:
    """
    Main entry point for policy QA.
    Retrieves context from PGVector and generates an answer using LCEL.
    """
    try:
        canonical_question, retrieval_queries, docs = _retrieve_policy_docs(question)
        context_text = _format_docs(docs)

        logger.info(
            "Policy Search Debug",
            extra={
                "question": question,
                "canonical_question": canonical_question,
                "retrieval_queries": retrieval_queries,
                "chunks_found": len(docs),
                "context_preview": context_text[:200] if context_text else "EMPTY",
            },
        )

        if not docs:
            return "I couldn't find any information about that in our policy documents. Please try again or contact HR."

        final_chain = _POLICY_PROMPT | _llm | StrOutputParser()
        answer = final_chain.invoke({"context": context_text, "question": question})

        return answer.strip()

    except Exception as exc:
        logger.exception(f"Policy QA failed for user {slack_id}", extra={"question": question})
        return f"I'm sorry, I encountered an error while searching for that policy.\n*Error:* `{str(exc)}`"

def reset_chain():
    """Reset the global chain (useful for testing or if retriever changes)."""
    return None
