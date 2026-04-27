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


def test_build_canonical_question_is_generic_not_hr_specific():
    policy_agent = _load_policy_agent()

    canonical, removed = policy_agent._build_canonical_question("Who is Finance Manager of Unikwork?")

    assert canonical == "Who is Finance of Unikwork?"
    assert removed is True


def test_build_retrieval_queries_include_original_and_generic_variants():
    policy_agent = _load_policy_agent()

    canonical, queries = policy_agent._build_retrieval_queries("Who is HR Manager?")

    assert canonical == "Who is HR?"
    assert queries[0] == "Who is HR Manager?"
    assert "Who is HR?" in queries
    assert "HR contact" in queries


def test_retrieve_policy_docs_merges_and_dedupes_results(monkeypatch):
    policy_agent = _load_policy_agent()

    query_to_docs = {
        "Who is HR Manager?": [_doc("Shraddha Shah is the HR contact.", page=2)],
        "Who is HR?": [_doc("Shraddha Shah is the HR contact.", page=2)],
        "HR contact": [_doc("Shraddha Shah is the HR Manager.", page=5)],
    }

    class FakeRetriever:
        def invoke(self, query: str):
            return query_to_docs.get(query, [])

    monkeypatch.setattr(
        policy_agent,
        "_build_retrieval_queries",
        lambda question: ("Who is HR?", ["Who is HR Manager?", "Who is HR?", "HR contact"]),
    )
    monkeypatch.setattr(policy_agent.policy_service, "get_retriever", lambda: FakeRetriever())

    canonical, retrieval_queries, docs = policy_agent._retrieve_policy_docs("Who is HR Manager?")

    assert canonical == "Who is HR?"
    assert "HR contact" in retrieval_queries
    assert len(docs) == 2
    assert docs[0].page_content == "Shraddha Shah is the HR contact."
