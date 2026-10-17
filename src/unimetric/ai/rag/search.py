"""Lexical ranking, vector similarity, keyword hits, and deduplication."""

import re
from sqlalchemy import text

from ...core.db import get_engine
from ...core.logconf import log
from .. import embeddings

def _vec_literal(vec: list[float]) -> str:
    """벡터를 pgvector가 받는 '[..]' 문자열로 변환한다."""
    return "[" + ",".join(repr(float(x)) for x in vec) + "]"


def _tokens(s: str) -> set:
    return set(re.findall(r"[A-Za-z0-9_]+|[가-힣]+", s.lower()))


def _lexical(
    docs: list[tuple[str, str]], question: str, k: int, *, fill: bool = False,
) -> list[dict]:
    """어휘 점수순 검색. 테이블은 fill로 0점 후보도 예산 안에서 보충한다.

    한국어 질문과 영문 컬럼명이 직접 겹치지 않을 수 있다. 샘플의 일반 단어
    하나가 일치했다고 나머지 스키마를 전부 버리면 필요한 조인을 만들 수 없다.
    지표 검색은 관련 없는 정의가 끼지 않도록 기존 양수 점수 우선을 유지한다.
    """
    qt = _tokens(question)
    scored = []
    for ref, content in docs:
        score = len(qt & _tokens(ref + " " + content))
        scored.append((score, ref, content))
    scored.sort(reverse=True)
    return [{"ref": r, "content": c} for s, r, c in scored[:k] if fill or s > 0] or \
           [{"ref": r, "content": c} for s, r, c in scored[:k]]


def _merge(*lists: list[dict], limit: int) -> list[dict]:
    """여러 히트 목록을 ref 기준 dedup하며 앞쪽 우선으로 합친다."""
    seen, out = set(), []
    for lst in lists:
        for h in lst:
            if h["ref"] in seen:
                continue
            seen.add(h["ref"])
            out.append(h)
    return out[:limit]


def _search(project: str, question: str, kind: str, k: int) -> list[dict]:
    """질문 임베딩과 코사인 거리가 가까운 문서 top-k를 반환한다."""
    qvec = _vec_literal(embeddings.embed_one(question))
    with get_engine().connect() as conn:
        rows = conn.execute(
            text(
                "SELECT ref, content FROM public.unimetric_embeddings "
                "WHERE project = :p AND kind = :k "
                "ORDER BY embedding <=> CAST(:q AS vector) LIMIT :n"
            ),
            {"p": project, "k": kind, "q": qvec, "n": k},
        ).mappings().all()
    log.info("[search] kind=%s q=%r -> top%d: %s", kind, question[:30], k,
             [r["ref"] for r in rows])
    return [dict(r) for r in rows]


def _keyword_hits(project: str, kind: str, question: str, k: int) -> list[dict]:
    """질문 토큰이 content/ref에 들어있는 행을 매칭 수 기준으로 가져온다.

    'sell_price', 'purchase', 'workshop' 같은 정확 토큰이나 샘플값에 든 한글을
    벡터 검색이 놓쳐도 끌어오기 위함.
    """
    toks = sorted(t for t in _tokens(question) if len(t) >= 2)[:12]
    if not toks:
        return []
    where = " OR ".join(f"content ILIKE :t{i} OR ref ILIKE :t{i}" for i in range(len(toks)))
    score = " + ".join(f"(content ILIKE :t{i})::int + (ref ILIKE :t{i})::int"
                       for i in range(len(toks)))
    params = {"p": project, "k": kind, "n": k}
    for i, t in enumerate(toks):
        params[f"t{i}"] = f"%{t}%"
    sql = (f"SELECT ref, content FROM public.unimetric_embeddings "
           f"WHERE project = :p AND kind = :k AND ({where}) "
           f"ORDER BY ({score}) DESC LIMIT :n")
    with get_engine().connect() as conn:
        rows = conn.execute(text(sql), params).mappings().all()
    hits = [dict(r) for r in rows]
    if hits:
        log.info("[keyword] kind=%s -> %s", kind, [h["ref"] for h in hits])
    return hits
