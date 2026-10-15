"""라우터 공용 의존성(헬퍼)."""

from fastapi import HTTPException

from ..semantic import model as metrics
from ..workspace import projects


def require_project(key: str) -> dict:
    """단체(프로젝트) 존재를 보장하고 행을 반환한다(없으면 404)."""
    try:
        return projects.get_project(key)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


def project_model(key: str) -> "metrics.Model":
    """단체에 저장된 지표 카탈로그(YAML)를 시맨틱 모델로 로드한다."""
    proj = require_project(key)
    return metrics.load_model(proj.get("metrics_yaml") or "")
