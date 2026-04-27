import importlib
import sys
import types
from types import SimpleNamespace


def _load_policy_agent():
    fake_policy_service_module = types.ModuleType("app.services.policy_service")
    fake_policy_service_module.policy_service = SimpleNamespace(get_retriever=lambda: None)

    sys.modules["app.services.policy_service"] = fake_policy_service_module
    sys.modules.pop("app.agents.policy_agent", None)
    return importlib.import_module("app.agents.policy_agent")


def _doc(content: str, source: str = "all_policy.pdf", page: int = 1):
    return SimpleNamespace(
        page_content=content,
        metadata={"source": source, "page": page},
    )


def test_normalize_question_maps_hr_manager_to_hr():
    policy_agent = _load_policy_agent()

    normalized, aliases = policy_agent._normalize_question("Who is HR Manager of Unikwork?")

    assert normalized == "Who is HR of Unikwork?"
    assert "hr manager" in aliases


def test_build_retrieval_queries_includes_normalized_and_alias_queries():
    policy_agent = _load_policy_agent()

    normalized, aliases, queries = policy_agent._build_retrieval_queries("Who is HR Manager?")

    assert normalized == "Who is HR?"
    assert "hr manager" in aliases
    assert "Who is HR Manager?" in queries
    assert "Who is HR?" in queries
    assert any("Hr Manager" in query for query in queries)


def test_retrieve_policy_docs_merges_results_for_role_aliases(monkeypatch):
    policy_agent = _load_policy_agent()

    query_to_docs = {
        "Who is HR Manager?": [_doc("Shraddha Shah is the HR contact.")],
        "Who is HR?": [_doc("Shraddha Shah is the HR contact.")],
        "Who is HR? Hr Manager": [_doc("Shraddha Shah is the HR Manager.")],
    }

    class FakeRetriever:
        def invoke(self, query: str):
            return query_to_docs.get(query, [])

    monkeypatch.setattr(policy_agent.policy_service, "get_retriever", lambda: FakeRetriever())

    normalized, aliases, docs = policy_agent._retrieve_policy_docs("Who is HR Manager?")

    assert normalized == "Who is HR?"
    assert "hr manager" in aliases
    assert len(docs) == 2
    assert any("HR Manager" in doc.page_content for doc in docs)
