"""지표 라우트 — 지표 목록/조회 + 자연어 질의(ask)."""

from fastapi import APIRouter, HTTPException

from ...ai import llm as text2sql
from ...data import query as engine
from ...semantic import builder
from ...workspace import projects
from ..deps import project_model, require_project
from ..schemas import Ask, MetricQuery

router = APIRouter(prefix="/api", tags=["metrics"])


@router.get("/metrics")
def metrics_list(project: str) -> dict:
    """단체에 정의된 지표 목록을 반환한다."""
    return {"metrics": project_model(project).list_metrics()}


@router.get("/metrics/detail")
def metric_detail(project: str, metric: str) -> dict:
    """지표 하나를 빌더 명세(spec)로 복원해 반환한다(편집 프리필용)."""
    try:
        return {"spec": project_model(project).metric_detail(metric)}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.delete("/metrics")
def metric_delete(project: str, metric: str) -> dict:
    """단체 카탈로그에서 지표 하나를 삭제한다."""
    proj = require_project(project)
    try:
        new_yaml = builder.remove_metric(proj.get("metrics_yaml") or "", metric)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    projects.set_metrics_yaml(project, new_yaml)
    return {"deleted": metric}


@router.post("/metrics/query")
def metrics_query(body: MetricQuery) -> dict:
    """지표를 SQL로 컴파일·실행하고 {sql, rows}를 반환한다."""
    model = project_model(body.project)
    try:
        sql = model.compile(body.metric, body.group_by, body.filters)
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    rows = engine.run_select(sql, body.project)
    return {"sql": sql, "rows": rows}


@router.post("/ask")
def ask(body: Ask) -> dict:
    """자연어 질문을 SQL로 변환·실행하고 {sql, rows}를 반환한다."""
    require_project(body.project)
    schema = engine.get_schema(body.project)
    if not schema:
        raise HTTPException(status_code=400, detail="이 단체에는 아직 데이터가 없습니다")
    try:
        sql = text2sql.question_to_sql(body.question, schema, body.provider)
    except RuntimeError as exc:  # provider 미설정
        raise HTTPException(status_code=400, detail=str(exc))
    try:
        rows = engine.run_select(sql, body.project)
    except ValueError as exc:  # 생성된 SQL이 안전한 SELECT가 아님
        raise HTTPException(status_code=400, detail=f"생성된 SQL이 거부됨: {exc}")
    return {"sql": sql, "rows": rows}
