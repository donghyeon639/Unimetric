"""Regression coverage for weak Korean keyword matches hiding join tables."""

from unimetric.ai import retrieval
from unimetric.ai.rag import catalog, context as rag_context, index, search


QUESTION = (
    "판매글 등록 지역별로 전체 판매글 수, 거래가 완료된 판매글 수, 거래 성사율, "
    "완료 거래금액을 보여줘. 성사율은 전체 판매글 중 완료 거래가 있는 판매글의 "
    "비율이고, 취소 거래는 제외해줘."
)
DOCS = [
    ("neighborhoods", "테이블 neighborhoods. 컬럼: id(integer), name(text)."),
    ("users", "테이블 users. 컬럼: id(integer), neighborhood_id(integer)."),
    ("listings", "테이블 listings. 컬럼: id(integer), neighborhood_id(integer)."),
    ("categories", "테이블 categories. 컬럼: id(integer), name(text)."),
    ("offers", "테이블 offers. 컬럼: id(integer), listing_id(integer)."),
    ("transactions", "테이블 transactions. 컬럼: listing_id(integer), amount(numeric), status(text)."),
    ("reviews", "테이블 reviews. 컬럼: transaction_id(integer). 예시행: body=좋은 거래 감사합니다."),
]


def setup_documents(monkeypatch):
    monkeypatch.setattr(catalog, "_table_documents", lambda project: DOCS)
    monkeypatch.setattr(catalog, "_metric_documents", lambda project: [])


def test_korean_question_does_not_lose_join_tables_on_one_sample_match(monkeypatch):
    setup_documents(monkeypatch)
    monkeypatch.setattr(rag_context.embeddings, "is_enabled", lambda: False)
    # Reproduce the old failure: only the review sample shares a whole token.
    assert [h["ref"] for h in search._lexical(DOCS, QUESTION, 8)] == ["reviews"]
    context = retrieval.build_context("market", QUESTION)
    for ref, _ in DOCS:
        assert f"테이블 {ref}." in context


def test_table_filling_preserves_rank_and_budget():
    docs = DOCS + [(f"extra_{i}", f"테이블 extra_{i}.") for i in range(30)]
    hits = search._lexical(docs, QUESTION, 8, fill=True)
    assert len(hits) == 8
    assert hits[0]["ref"] == "reviews"
    assert len({hit["ref"] for hit in hits}) == 8
    assert search._lexical([], QUESTION, 8, fill=True) == []


def test_metric_search_does_not_fill_irrelevant_definitions():
    hits = search._lexical(
        [("completed", "완료 거래"), ("unrelated", "가입 사용자")], QUESTION, 5,
    )
    assert [h["ref"] for h in hits] == ["completed"]


def test_small_schema_is_complete_even_with_partial_vector_index(monkeypatch):
    setup_documents(monkeypatch)
    monkeypatch.setattr(rag_context.embeddings, "is_enabled", lambda: True)
    monkeypatch.setattr(index, "_count", lambda project: 1)
    monkeypatch.setattr(search, "_search", lambda project, question, kind, k:
                        [{"ref": "reviews", "content": DOCS[-1][1]}] if kind == "table" else [])
    monkeypatch.setattr(search, "_keyword_hits", lambda *args: [])
    monkeypatch.setattr(rag_context.dq, "list_tables", lambda project: [ref for ref, _ in DOCS])
    context = retrieval.build_context("market", QUESTION)
    for ref, _ in DOCS:
        assert f"테이블 {ref}." in context


def test_large_vector_schema_keeps_retrieval_without_loading_all_samples(monkeypatch):
    monkeypatch.setattr(rag_context.embeddings, "is_enabled", lambda: True)
    monkeypatch.setattr(index, "_count", lambda project: 1)
    monkeypatch.setattr(search, "_search", lambda project, question, kind, k:
                        [{"ref": "listings", "content": DOCS[2][1]}] if kind == "table" else [])
    monkeypatch.setattr(search, "_keyword_hits", lambda *args: [])
    monkeypatch.setattr(rag_context.dq, "list_tables", lambda project: [str(i) for i in range(30)])
    def unexpected_full_load(project):
        raise AssertionError("Large schemas should still use the retrieval budget")
    monkeypatch.setattr(catalog, "_table_documents", unexpected_full_load)
    context = retrieval.build_context("large", QUESTION)
    assert "테이블 listings." in context
    assert "테이블 reviews." not in context
