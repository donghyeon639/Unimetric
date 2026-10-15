"""RAG 검색 — 카탈로그(테이블/지표)를 임베딩·인덱싱하고 질문에 맞는 것만 추린다.

흐름:
1. index_project(): 테이블(컬럼+FK+샘플)·지표 설명을 '문서'로 만들어 임베딩 →
   pgvector(unimetric_embeddings)에 저장.
2. build_context(): 질문을 임베딩 → 벡터 유사도 top-k 문서만 골라 LLM 프롬프트용
   컨텍스트를 만든다. 지표가 매칭되면 '재사용 우선'으로 앞에 둔다.
- 임베딩 provider가 없거나 인덱스가 비어 있으면 어휘(lexical) 겹침으로 폴백한다.
"""

import re
import threading

from sqlalchemy import text

from ..core.db import get_engine
from ..core.logconf import log
from ..semantic import model as metrics
from ..workspace import projects
from . import embeddings
from ..data import query as dq


def _vec_literal(vec: list[float]) -> str:
    """벡터를 pgvector가 받는 '[..]' 문자열로 변환한다."""
    return "[" + ",".join(repr(float(x)) for x in vec) + "]"


def _trunc(v, n: int = 40) -> str:
    s = str(v)
    return s if len(s) <= n else s[:n] + "…"


# --------------------------------------------------------------------------
# 문서(카탈로그) 구성
# --------------------------------------------------------------------------
def _table_documents(project: str) -> list[tuple[str, str]]:
    """테이블별 (ref, content) 문서 목록. content = 컬럼+관계+샘플행."""
    schema = dq.get_schema(project)
    fks = dq.foreign_keys(project)
    docs = []
    for t, cols in schema.items():
        col_str = ", ".join(f"{c['column']}({c['type']})" for c in cols)
        doc = f"테이블 {t}. 컬럼: {col_str}."
        rel = fks.get(t)
        if rel:
            doc += " 관계: " + "; ".join(
                f"{r['column']}→{r['ref_table']}.{r['ref_column']}" for r in rel
            ) + "."
        try:
            rows = dq.preview_table(project, t, 0, 2)["rows"]
            if rows:
                doc += " 예시행: " + " | ".join(
                    ", ".join(f"{k}={_trunc(v)}" for k, v in row.items()) for row in rows
                )
        except Exception:
            pass
        docs.append((t, doc))
    return docs


def _metric_documents(project: str) -> list[tuple[str, str]]:
    """지표별 (ref, content) 문서 목록(재사용 후보 검색용)."""
    proj = projects.get_project(project)
    model = metrics.load_model(proj.get("metrics_yaml") or "")
    docs = []
    for m in model.list_metrics():
        doc = f"지표 {m['name']}: {m.get('description') or ''} (엔티티 {m['entity']}, {m['type']})"
        docs.append((m["name"], doc))
    return docs


# --------------------------------------------------------------------------
# 인덱싱(임베딩 저장)
# --------------------------------------------------------------------------
def index_project(project: str) -> dict:
    """프로젝트의 테이블/지표 문서를 임베딩해 pgvector에 저장한다(기존 것 교체)."""
    if not embeddings.is_enabled():
        return {"indexed": 0, "skipped": "임베딩 provider 미설정"}
    log.info("[index] '%s' 문서 수집 중…", project)
    items = [("table", r, c) for r, c in _table_documents(project)]
    items += [("metric", r, c) for r, c in _metric_documents(project)]
    log.info("[index] 문서 %d개 (테이블+지표) → 임베딩 시작", len(items))
    if not items:
        return {"indexed": 0}
    vecs = embeddings.embed([c for _, _, c in items])
    with get_engine().begin() as conn:
        conn.execute(text("DELETE FROM public.unimetric_embeddings WHERE project = :p"),
                     {"p": project})
        for (kind, ref, content), vec in zip(items, vecs):
            conn.execute(
                text(
                    "INSERT INTO public.unimetric_embeddings (project, kind, ref, content, embedding) "
                    "VALUES (:p, :k, :r, :c, CAST(:e AS vector))"
                ),
                {"p": project, "k": kind, "r": ref, "c": content, "e": _vec_literal(vec)},
            )
    return {"indexed": len(items)}


# 동시 중복 인덱싱 방지용(프로젝트별 1개만).
_INDEXING: set = set()


def index_project_async(project: str) -> None:
    """백그라운드 스레드로 인덱싱을 시작한다(연동 시점에 미리 호출).

    채팅을 막지 않도록 비동기로 돈다. 같은 프로젝트가 이미 인덱싱 중이면 무시.
    """
    if not embeddings.is_enabled() or project in _INDEXING:
        return
    _INDEXING.add(project)

    def _run():
        try:
            log.info("[index] '%s' 백그라운드 인덱싱 시작", project)
            index_project(project)
            log.info("[index] '%s' 백그라운드 인덱싱 완료", project)
        except Exception as exc:
            log.warning("[index] '%s' 백그라운드 인덱싱 실패: %s", project, exc)
        finally:
            _INDEXING.discard(project)

    threading.Thread(target=_run, daemon=True).start()


def _count(project: str) -> int:
    try:
        with get_engine().connect() as conn:
            return int(conn.execute(
                text("SELECT COUNT(*) FROM public.unimetric_embeddings WHERE project = :p"),
                {"p": project}).scalar())
    except Exception:
        return 0


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


# --------------------------------------------------------------------------
# 키워드 매칭(하이브리드 검색의 한 축) — 저장된 카탈로그 content/ref에서 토큰 매칭
# --------------------------------------------------------------------------
def _keyword_hits(project: str, kind: str, question: str, k: int) -> list[dict]:
    """질문 토큰이 content/ref에 들어있는 행을 매칭 수 기준으로 가져온다.

    'sell_price', 'purchase', 'workshop' 같은 정확 토큰이나 샘플값에 든 한글을
    벡터 검색이 놓쳐도 끌어오기 위함.
    """
    toks = [t for t in _tokens(question) if len(t) >= 2][:12]
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


# --------------------------------------------------------------------------
# 어휘(lexical) 폴백 — 임베딩 없을 때
# --------------------------------------------------------------------------
def _tokens(s: str) -> set:
    return set(re.findall(r"[A-Za-z0-9_]+|[가-힣]+", s.lower()))


def _lexical(docs: list[tuple[str, str]], question: str, k: int) -> list[dict]:
    qt = _tokens(question)
    scored = []
    for ref, content in docs:
        score = len(qt & _tokens(ref + " " + content))
        scored.append((score, ref, content))
    scored.sort(reverse=True)
    return [{"ref": r, "content": c} for s, r, c in scored[:k] if s > 0] or \
           [{"ref": r, "content": c} for s, r, c in scored[:k]]


# --------------------------------------------------------------------------
# 컨텍스트 조립(프롬프트용)
# --------------------------------------------------------------------------
def build_context(project: str, question: str, k_tables: int = 8, k_metrics: int = 5) -> str:
    """질문에 맞는 지표/테이블만 추려 LLM 프롬프트용 컨텍스트 텍스트를 만든다.

    인덱스가 있으면 하이브리드(벡터 유사도 ∪ 키워드 매칭)로 검색한다.
    """
    use_vec = embeddings.is_enabled()
    if use_vec and _count(project) == 0:
        # 인덱스가 아직 없으면 채팅을 막지 않는다: 백그라운드로 인덱싱만 걸고
        # 이번 턴은 어휘 폴백으로 즉시 답한다(다음 턴부터 벡터 검색).
        log.info("[context] 인덱스 비어있음 → 백그라운드 인덱싱 시작(이번 턴 어휘 폴백)")
        index_project_async(project)
        use_vec = False

    if use_vec:
        try:
            # 하이브리드: 벡터 top-k + 키워드 매칭을 합쳐(dedup) recall을 높인다.
            metric_hits = _merge(_search(project, question, "metric", k_metrics),
                                 _keyword_hits(project, "metric", question, 4),
                                 limit=k_metrics + 2)
            table_hits = _merge(_search(project, question, "table", k_tables),
                                _keyword_hits(project, "table", question, 5),
                                limit=k_tables + 5)
            log.info("[context] 하이브리드(벡터+키워드): 지표 %d, 테이블 %d",
                     len(metric_hits), len(table_hits))
        except Exception as exc:
            log.warning("[context] 하이브리드 검색 실패: %s", exc)
            use_vec = False

    if not use_vec:  # 어휘 폴백
        log.info("[context] 벡터 검색 불가 → 어휘(lexical) 폴백 사용")
        metric_hits = _lexical(_metric_documents(project), question, k_metrics)
        table_hits = _lexical(_table_documents(project), question, k_tables)
    log.info("[context] mode=%s 지표 %d개, 테이블 %d개 선택",
             "vector" if use_vec else "lexical", len(metric_hits), len(table_hits))

    parts = []
    if metric_hits:
        parts.append("[관련 지표 — 질문에 맞으면 새로 만들지 말고 이걸 재사용/인용]")
        parts += [f"- {h['content']}" for h in metric_hits]
    parts.append("\n[관련 테이블 — 이 안에서만 컬럼/조인/조건을 고르세요]")
    parts += [h["content"] for h in table_hits]
    return "\n".join(parts)
