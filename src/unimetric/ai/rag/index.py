"""Persist embedding documents and schedule background index updates."""

import threading
from sqlalchemy import text

from ...core.db import get_engine
from ...core.logconf import log
from .. import embeddings
from . import catalog
from .search import _vec_literal

_INDEXING: set[str] = set()

def index_project(project: str) -> dict:
    """프로젝트의 테이블/지표 문서를 임베딩해 pgvector에 저장한다(기존 것 교체)."""
    if not embeddings.is_enabled():
        return {"indexed": 0, "skipped": "임베딩 provider 미설정"}
    log.info("[index] '%s' 문서 수집 중…", project)
    items = [("table", r, c) for r, c in catalog._table_documents(project)]
    items += [("metric", r, c) for r, c in catalog._metric_documents(project)]
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
