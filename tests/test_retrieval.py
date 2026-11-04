"""Regression coverage for weak Korean keyword matches hiding join tables."""

from unimetric.ai import retrieval
from unimetric.ai.rag import catalog, context as rag_context, index, relations, search


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
    monkeypatch.setattr(catalog, "_table_documents", lambda project, **kwargs: DOCS)
    monkeypatch.setattr(catalog, "_metric_documents", lambda project: [])
    monkeypatch.setattr(rag_context.dq, "get_schema", lambda project: dict.fromkeys(t for t, _ in DOCS))
    monkeypatch.setattr(rag_context.dq, "foreign_keys", lambda project: {})


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
    context = retrieval.build_context("market", QUESTION)
    for ref, _ in DOCS:
        assert f"테이블 {ref}." in context


def test_large_vector_schema_keeps_retrieval_without_loading_all_samples(monkeypatch):
    monkeypatch.setattr(rag_context.embeddings, "is_enabled", lambda: True)
    monkeypatch.setattr(index, "_count", lambda project: 1)
    monkeypatch.setattr(search, "_search", lambda project, question, kind, k:
                        [{"ref": "listings", "content": DOCS[2][1]}] if kind == "table" else [])
    monkeypatch.setattr(search, "_keyword_hits", lambda *args: [])
    monkeypatch.setattr(rag_context.dq, "get_schema", lambda project: dict.fromkeys(["listings", *map(str, range(29))]))
    monkeypatch.setattr(rag_context.dq, "foreign_keys", lambda project: {})
    def unexpected_full_load(project):
        raise AssertionError("Large schemas should still use the retrieval budget")
    monkeypatch.setattr(catalog, "_table_documents", unexpected_full_load)
    context = retrieval.build_context("large", QUESTION)
    assert "테이블 listings." in context
    assert "테이블 reviews." not in context


def test_korean_terms_and_foreign_keys_recover_join_on_large_schema(monkeypatch):
    schema = {
        table: [{"column": "id", "type": "integer"}]
        for table in ("transactions", "listings", "neighborhoods", *(f"zz_{i:02d}" for i in range(17)))
    }
    fks = {
        "transactions": [{"column": "listing_id", "ref_table": "listings", "ref_column": "id"}],
        "listings": [{"column": "neighborhood_id", "ref_table": "neighborhoods", "ref_column": "id"}],
    }
    monkeypatch.setattr(rag_context.embeddings, "is_enabled", lambda: False)
    monkeypatch.setattr(rag_context.dq, "get_schema", lambda project: schema)
    monkeypatch.setattr(rag_context.dq, "foreign_keys", lambda project: fks)
    monkeypatch.setattr(rag_context.dq, "preview_table", lambda *args: {"rows": []})
    monkeypatch.setattr(catalog, "_metric_documents", lambda project: [])

    # No mention of the listing table: it must come from the FK path.
    question = "동네별로 완료된 거래금액을 합산해줘."
    old = search._lexical(catalog._table_documents("market"), question, 8, fill=True)
    assert not {"transactions", "listings", "neighborhoods"} & {h["ref"] for h in old}
    context = retrieval.build_context("market", question, k_tables=3)
    for table in ("transactions", "listings", "neighborhoods"):
        assert f"테이블 {table}." in context
    assert "listing_id→listings.id" in context
    assert "neighborhood_id→neighborhoods.id" in context
    assert "테이블 zz_" not in context


def test_expansion_uses_remaining_budget_for_referenced_table():
    tables = ["transactions", "listings", "reviews", "extra"]
    fks = {"transactions": [{"ref_table": "listings"}],
           "reviews": [{"ref_table": "transactions"}]}
    assert relations.expand_tables(
        ["transactions", "extra"], fks, tables, 2, anchors=["transactions"],
    ) == ["transactions", "listings"]
    assert relations.matching_terms("판매글의 거래처", tables) == {"listings": ["판매글"]}
