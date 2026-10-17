"""단체(프로젝트) 라우트 — 생성/목록/조회/삭제 + 지표 카탈로그 YAML 저장."""

from fastapi import APIRouter, HTTPException

from ...semantic import builder
from ...semantic import model as metrics
from ...workspace import projects
from ..schemas import MetricsYaml, ProjectCreate

router = APIRouter(prefix="/api", tags=["projects"])


@router.get("/projects")
def list_projects() -> dict:
    """등록된 단체 목록을 반환한다."""
    return {"projects": projects.list_projects()}


@router.post("/projects")
def create_project(body: ProjectCreate) -> dict:
    """새 단체(스키마 포함)를 생성한다."""
    try:
        return {"project": projects.create_project(body.key, body.name, body.metrics_yaml)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/projects/{key}")
def get_project(key: str) -> dict:
    """단체 한 건(지표 YAML 포함)을 반환한다. 접속 비밀번호는 마스킹한다."""
    try:
        proj = projects.get_project(key)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    cfg = proj.get("source_config")
    if cfg:
        proj["source_config"] = {**cfg, "password": "***" if cfg.get("password") else None}
    return {"project": proj}


@router.delete("/projects/{key}")
def delete_project(key: str) -> dict:
    """단체와 그 안의 모든 데이터를 삭제한다."""
    projects.delete_project(key)
    return {"deleted": key}


@router.put("/projects/{key}/metrics-yaml")
def set_metrics_yaml(key: str, body: MetricsYaml) -> dict:
    """단체의 지표 카탈로그 YAML을 저장한다(저장 전 유효성 검증)."""
    try:
        metrics.load_model(body.metrics_yaml)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"잘못된 지표 YAML: {exc}")
    try:
        return {"project": projects.set_metrics_yaml(key, body.metrics_yaml)}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.post("/projects/{key}/metrics-yaml/add")
def add_metrics_yaml(key: str, body: MetricsYaml) -> dict:
    """지표 1개짜리 YAML 조각을 기존 카탈로그에 '병합'한다(누적, 기존 유지)."""
    try:
        proj = projects.get_project(key)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    try:
        merged = builder.merge_yaml(proj.get("metrics_yaml") or "", body.metrics_yaml)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"병합 실패: {exc}")
    return {"project": projects.set_metrics_yaml(key, merged)}
