"""채팅 세션 + 컨텍스트 압축(compaction).

대화 상태를 서버가 소유한다. 길어지는 대화를 무한정 다시 보내지 않도록:
- 최근 메시지는 원문으로 유지(recent window)
- 예산(BUDGET_CHARS)을 넘으면 오래된 메시지를 LLM으로 '요약'해 summary에 접고,
  summarized_count를 올려 그 이후만 원문으로 보낸다.
- 프롬프트로 나가는 것 = [요약] + [최근 원문 메시지] (+ 라우터가 RAG 컨텍스트를 더함)
"""

import uuid

from sqlalchemy import text

from ..core.db import get_engine
from ..core.logconf import log
from . import llm

BUDGET_CHARS = 6000   # 최근 원문으로 보낼 글자 예산(초과 시 압축)
KEEP_RECENT = 6       # 압축하더라도 최소 유지할 최근 메시지 수


def start_session(project: str) -> str:
    """새 세션을 만들고 id를 반환한다."""
    sid = "sess_" + uuid.uuid4().hex[:12]
    with get_engine().begin() as conn:
        conn.execute(
            text("INSERT INTO public.unimetric_chat_sessions (id, project) VALUES (:i, :p)"),
            {"i": sid, "p": project},
        )
    return sid


def _session(session_id: str) -> dict | None:
    with get_engine().connect() as conn:
        row = conn.execute(
            text("SELECT id, project, summary, summarized_count "
                 "FROM public.unimetric_chat_sessions WHERE id = :i"),
            {"i": session_id},
        ).mappings().first()
    return dict(row) if row else None


def ensure_session(session_id: str | None, project: str) -> str:
    """세션 id가 유효하고 '같은 단체' 소속이면 재사용, 아니면 새로 만든다.

    다른 단체의 세션을 재사용하면 엉뚱한 DB 맥락이 섞이므로 project가 일치할 때만 재사용.
    """
    if session_id:
        s = _session(session_id)
        if s and s["project"] == project:
            return session_id
    return start_session(project)


def list_sessions(project: str, limit: int = 30) -> list[dict]:
    """해당 단체의 채팅 세션 목록(최근순) — id, 제목(첫 사용자 메시지), 갱신시각."""
    with get_engine().connect() as conn:
        rows = conn.execute(
            text(
                "SELECT s.id, s.updated_at, "
                "(SELECT content FROM public.unimetric_chat_messages m "
                " WHERE m.session_id = s.id AND m.role='user' ORDER BY m.id LIMIT 1) AS title "
                "FROM public.unimetric_chat_sessions s "
                "WHERE s.project = :p ORDER BY s.updated_at DESC LIMIT :n"
            ),
            {"p": project, "n": limit},
        ).mappings().all()
    return [dict(r) for r in rows]


def session_messages(session_id: str) -> list[dict]:
    """세션의 전체 메시지(원문, 시간순)를 반환한다(불러오기용)."""
    return _all_messages(session_id)


def delete_session(session_id: str) -> None:
    """세션과 그 메시지를 삭제한다."""
    with get_engine().begin() as conn:
        conn.execute(text("DELETE FROM public.unimetric_chat_messages WHERE session_id = :s"),
                     {"s": session_id})
        conn.execute(text("DELETE FROM public.unimetric_chat_sessions WHERE id = :s"),
                     {"s": session_id})


def add_message(session_id: str, role: str, content: str) -> None:
    """세션에 메시지(원문)를 기록한다."""
    with get_engine().begin() as conn:
        conn.execute(
            text("INSERT INTO public.unimetric_chat_messages (session_id, role, content) "
                 "VALUES (:s, :r, :c)"),
            {"s": session_id, "r": role, "c": content},
        )
        conn.execute(text("UPDATE public.unimetric_chat_sessions SET updated_at = now() WHERE id = :i"),
                     {"i": session_id})


def _all_messages(session_id: str) -> list[dict]:
    with get_engine().connect() as conn:
        rows = conn.execute(
            text("SELECT role, content FROM public.unimetric_chat_messages "
                 "WHERE session_id = :s ORDER BY id"),
            {"s": session_id},
        ).mappings().all()
    return [dict(r) for r in rows]


def history(session_id: str) -> tuple[str, list[dict]]:
    """(요약, 최근 원문 메시지) 반환 — summarized_count 이후만 원문."""
    s = _session(session_id)
    if not s:
        return "", []
    tail = _all_messages(session_id)[s["summarized_count"]:]
    return s["summary"], tail


def maybe_compact(session_id: str, provider: str | None = None) -> None:
    """최근 원문이 예산을 넘으면 오래된 부분을 요약해 summary에 접는다."""
    s = _session(session_id)
    if not s:
        return
    tail = _all_messages(session_id)[s["summarized_count"]:]
    chars = sum(len(m["content"]) for m in tail)
    if chars <= BUDGET_CHARS or len(tail) <= KEEP_RECENT:
        return
    to_fold = tail[:-KEEP_RECENT]  # 최근 KEEP_RECENT개는 원문 유지
    try:
        new_summary = llm.summarize(s["summary"], to_fold, provider)
    except Exception as exc:
        log.warning("[session] 요약 실패(압축 생략): %s", exc)
        return
    with get_engine().begin() as conn:
        conn.execute(
            text("UPDATE public.unimetric_chat_sessions "
                 "SET summary = :sm, summarized_count = :c, updated_at = now() WHERE id = :i"),
            {"sm": new_summary, "c": s["summarized_count"] + len(to_fold), "i": session_id},
        )
    log.info("[session] 압축: %d개 메시지를 요약에 접음(누적 %d)", len(to_fold),
             s["summarized_count"] + len(to_fold))
