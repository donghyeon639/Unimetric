"""지표 빌더 라우트 — 컬럼 프로파일링, 폼 미리보기/저장, 단발성 AI 생성, LLM 상태."""

import json

from fastapi import APIRouter, HTTPException

from ...ai import llm as text2sql
from ...data import profiling as profile
from ...data import query as engine
from ...semantic import builder
from ...semantic import model as metrics
from ...workspace import projects
from ..deps import require_project
from ..schemas import AiMetricReq, BuilderReq

router = APIRouter(prefix="/api", tags=["builder"])


@router.get("/profile")
def api_profile(project: str, table: str) -> dict:
    """테이블을 프로파일링해 컬럼별 역할과 차원/측정값 추천을 반환한다."""
    require_project(project)
    try:
        return profile.profile_table(project, table)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.get("/llm-status")
def llm_status() -> dict:
    """현재 LLM provider 설정 여부(AI 기능 활성 판단용)."""
    return {"configured": text2sql.is_configured(), "provider": text2sql.provider_name(),
            "providers": text2sql.provider_options()}


@router.post("/builder/preview")
def builder_preview(body: BuilderReq) -> dict:
    """명세로 지표를 (저장 없이) 컴파일·실행해 SQL과 결과를 미리 보여준다."""
    proj = require_project(body.project)
    try:
        new_yaml = builder.add_metric(proj.get("metrics_yaml") or "", body.table, body.spec)
        model = metrics.load_model(new_yaml)
        sql = model.compile(body.spec["name"], body.group_by)
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    rows = engine.run_select(sql, body.project)
    return {"sql": sql, "rows": rows}


@router.post("/builder/save")
def builder_save(body: BuilderReq) -> dict:
    """명세로 만든 지표를 단체의 지표 카탈로그(YAML)에 병합·저장한다(일반/SQL 둘 다)."""
    proj = require_project(body.project)
    cur = proj.get("metrics_yaml") or ""
    try:
        if body.spec.get("type") == "sql":   # 자유 SQL 지표(AI 분석 답변 저장)
            new_yaml = builder.add_sql_metric(cur, body.spec)
        else:
            new_yaml = builder.add_metric(cur, body.table, body.spec)
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    projects.set_metrics_yaml(body.project, new_yaml)
    return {"project": body.project, "metrics": metrics.load_model(new_yaml).list_metrics()}


@router.post("/builder/ai")
def builder_ai(body: AiMetricReq) -> dict:
    """단발성 자연어 요청으로 지표 명세(JSON)를 생성해 검토용으로 반환한다."""
    require_project(body.project)
    schema = engine.get_schema(body.project)
    cols = schema.get(body.table)
    if not cols:
        raise HTTPException(status_code=404, detail=f"테이블 '{body.table}'에 컬럼이 없습니다")
    try:
        raw = text2sql.question_to_metric(body.question, body.table, cols, body.provider)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    try:
        spec = json.loads(raw)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail=f"AI 응답을 JSON으로 해석 못 함: {raw[:300]}")
    return {"spec": spec}
