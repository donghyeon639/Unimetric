"""대화형 데이터/지표 어시스턴트 — RAG 컨텍스트로 SQL 생성 + 결과까지 돌려준다.

AI 응답은 action 기반:
- clarify: 정보가 부족하면 선택지(options)로 되묻는다.
- metric : 단일테이블 재사용 지표 제안 → 즉시 컴파일·실행해 결과(rows)도 함께.
- sql    : 조인/일회성 분석 → 읽기 전용 SELECT 실행해 결과를 보여준다.
질문에 맞는 지표/테이블만 RAG로 추려 컨텍스트로 준다(전체 스키마 주입 아님).
"""

import json
import re

from fastapi import APIRouter, HTTPException

from ...ai import conversation
from ...ai import llm as text2sql
from ...ai import retrieval
from ...core.logconf import log
from ...data import query as engine
from ...semantic import builder
from ...semantic import model as metrics
from ..deps import require_project
from ..schemas import ChatReq

router = APIRouter(prefix="/api", tags=["chat"])


@router.get("/chat/sessions")
def chat_sessions(project: str) -> dict:
    """현재 단체의 채팅 세션 목록(최근순)."""
    require_project(project)
    return {"sessions": conversation.list_sessions(project)}


@router.get("/chat/session/{session_id}")
def chat_session(session_id: str) -> dict:
    """한 세션의 메시지들을 반환한다(불러오기용)."""
    return {"messages": conversation.session_messages(session_id)}


@router.delete("/chat/session/{session_id}")
def chat_session_delete(session_id: str) -> dict:
    """세션 삭제."""
    conversation.delete_session(session_id)
    return {"deleted": session_id}


@router.post("/index")
def reindex(project: str) -> dict:
    """단체의 카탈로그(테이블/지표)를 임베딩해 RAG 인덱스를 (재)구축한다."""
    require_project(project)
    try:
        return retrieval.index_project(project)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


def _extract_action(raw: str) -> tuple[str, dict | None]:
    """답변에서 ```json 액션 블록```을 분리해 (설명텍스트, action dict)로 반환."""
    m = re.search(r"```json\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if not m:
        m = re.search(r"(\{(?:[^{}]|\{(?:[^{}]|\{[^{}]*\})*\})*\})\s*$", raw.strip(), re.DOTALL)
    action = None
    reply = raw
    if m:
        try:
            action = json.loads(m.group(1))
            reply = raw[: m.start()].replace("```json", "").strip()
        except json.JSONDecodeError:
            action = None
    return reply.strip() or "…", action


def _action_note(out: dict) -> str:
    """assistant 턴을 세션에 기록할 때, 후속 질문이 참조하도록 행동 요약을 덧붙인다."""
    if out.get("spec"):
        sql = (out.get("result") or {}).get("sql", "")
        return f"\n[제안 지표: {out['spec'].get('name')} (table={out['spec'].get('table')}) sql={sql}]"
    if out.get("answer"):
        return f"\n[실행 SQL: {out['answer'].get('sql','')} → {len(out['answer'].get('rows',[]))}행]"
    if out.get("clarify"):
        return f"\n[되물음: {out['clarify'].get('question','')}]"
    return ""


@router.post("/chat")
def chat(body: ChatReq) -> dict:
    """세션 기반 대화: 기록·압축은 서버가, RAG로 관련 테이블/지표만 컨텍스트로."""
    proj = require_project(body.project)
    sid = conversation.ensure_session(body.session_id, body.project)
    conversation.add_message(sid, "user", body.message)
    summary, tail = conversation.history(sid)  # tail에 방금 사용자 메시지 포함
    log.info("[chat] '%s' session=%s 질문=%r (요약%d자, 최근%d턴)",
             body.project, sid, body.message[:60], len(summary), len(tail))
    try:
        context = retrieval.build_context(body.project, body.message)
        log.info("[chat] 컨텍스트 %d자 → LLM 호출", len(context))
        raw = text2sql.chat(tail, context, summary)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    reply, action = _extract_action(raw)
    kind = action.get("action") if action else None
    log.info("[chat] action=%s reply=%r", kind, reply[:60])
    out: dict = {"reply": reply, "session_id": sid}
    if action:
        if kind == "clarify":
            out["clarify"] = {"question": action.get("question", ""),
                              "options": action.get("options", [])}
        elif kind == "metric":
            spec = action.get("spec") or {}
            out["spec"] = spec
            try:
                new_yaml = builder.add_metric(proj.get("metrics_yaml") or "", spec["table"], spec)
                model = metrics.load_model(new_yaml)
                sql = model.compile(spec["name"], spec.get("dimensions") or [])
                rows = engine.run_select(sql, body.project)
                log.info("[chat] metric '%s' 실행 → %d행 | %s", spec.get("name"), len(rows), sql)
                out["result"] = {"sql": sql, "rows": rows}
            except Exception as exc:
                log.warning("[chat] metric 실행 실패: %s", exc)
                out["result_error"] = str(exc)
        elif kind == "sql":
            sql = action.get("sql", "")
            try:
                rows = engine.run_select(sql, body.project)
                log.info("[chat] sql 실행 → %d행 | %s", len(rows), sql[:200])
                out["answer"] = {"sql": sql, "rows": rows}
            except Exception as exc:
                log.warning("[chat] sql 실행 실패: %s", exc)
                out["answer_error"] = str(exc)

    # assistant 턴 기록(행동 요약 포함) 후 필요 시 압축.
    conversation.add_message(sid, "assistant", reply + _action_note(out))
    conversation.maybe_compact(sid)
    return out
