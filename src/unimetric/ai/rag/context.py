"""Select catalog evidence and assemble the model's RAG context."""

from ...core.logconf import log
from ...data import query as dq
from .. import embeddings
from . import catalog, index, relations, search

def build_context(project: str, question: str, k_tables: int = 8, k_metrics: int = 5) -> str:
    """질문에 맞는 지표/테이블만 추려 LLM 프롬프트용 컨텍스트 텍스트를 만든다.

    인덱스가 있으면 하이브리드(벡터 유사도 ∪ 키워드 매칭)로 검색한다.
    """
    use_vec = embeddings.is_enabled()
    if use_vec and index._count(project) == 0:
        # 인덱스가 아직 없으면 채팅을 막지 않는다: 백그라운드로 인덱싱만 걸고
        # 이번 턴은 어휘 폴백으로 즉시 답한다(다음 턴부터 벡터 검색).
        log.info("[context] 인덱스 비어있음 → 백그라운드 인덱싱 시작(이번 턴 어휘 폴백)")
        index.index_project_async(project)
        use_vec = False

    if use_vec:
        try:
            # 하이브리드: 벡터 top-k + 키워드 매칭을 합쳐(dedup) recall을 높인다.
            metric_hits = search._merge(search._search(project, question, "metric", k_metrics),
                                 search._keyword_hits(project, "metric", question, 4),
                                 limit=k_metrics + 2)
            table_hits = search._merge(search._search(project, question, "table", k_tables),
                                search._keyword_hits(project, "table", question, 5),
                                limit=k_tables + 5)
            log.info("[context] 하이브리드(벡터+키워드): 지표 %d, 테이블 %d",
                     len(metric_hits), len(table_hits))
        except Exception as exc:
            log.warning("[context] 하이브리드 검색 실패: %s", exc)
            use_vec = False

    schema = dq.get_schema(project)
    fks = dq.foreign_keys(project)
    aliases = relations.matching_terms(question, schema)
    explicit = search._tokens(question)
    anchors = [t for t in schema if t.lower() in explicit]
    anchors += [t for t in aliases if t not in anchors]

    if not use_vec:  # 어휘 폴백
        log.info("[context] 벡터 검색 불가 → 어휘(lexical) 폴백 사용")
        metric_hits = search._lexical(catalog._metric_documents(project), question, k_metrics)
        docs = catalog._table_documents(project, schema=schema, fks=fks)
        table_hits = search._lexical(docs, question, k_tables, fill=True)
    elif len(schema) <= k_tables:
        # 작은 스키마는 검색 점수/인덱스 갱신 여부와 관계없이 전체 관계를 전달한다.
        table_hits = [{"ref": ref, "content": content}
                      for ref, content in catalog._table_documents(project, schema=schema, fks=fks)]

    if len(schema) > k_tables:
        hits = {h["ref"]: h["content"] for h in table_hits}
        # Zero-score filler is useful only after real matches and their join paths.
        ranked = list(hits) if use_vec else [
            h["ref"] for h in table_hits
            if explicit & search._tokens(h["ref"] + " " + h["content"])
        ]
        selected = relations.expand_tables(ranked, fks, schema, k_tables, anchors=anchors)
        selected = list(dict.fromkeys([*selected, *(t for t in hits if t in schema)]))[:k_tables]
        if not use_vec:
            hits.update(docs)
        missing = {t: schema[t] for t in selected if t not in hits}
        if missing:
            hits.update(catalog._table_documents(project, schema=missing, fks=fks, samples=False))
        table_hits = [{"ref": t, "content": hits[t]} for t in selected]
    log.info("[context] mode=%s 지표 %d개, 테이블 %d개 선택",
             "vector" if use_vec else "lexical", len(metric_hits), len(table_hits))

    parts = []
    if metric_hits:
        parts.append("[관련 지표 — 질문에 맞으면 새로 만들지 말고 이걸 재사용/인용]")
        parts += [f"- {h['content']}" for h in metric_hits]
    parts.append("\n[관련 테이블 — 이 안에서만 컬럼/조인/조건을 고르세요]")
    for hit in table_hits:
        terms = aliases.get(hit["ref"])
        parts.append(hit["content"] + (" 업무 용어: " + ", ".join(terms) + "." if terms else ""))
    return "\n".join(parts)
