"""외부 소스 라우트 — 연결 테스트, 라이브 단체 생성, 전체 복사(잡)/진행/재시도."""

from fastapi import APIRouter, HTTPException

from ...ai import retrieval
from ...data import etl
from ...data import sources
from ...workspace import projects
from ..schemas import LiveProjectCreate, SourceConfig, SourceImport

router = APIRouter(prefix="/api", tags=["sources"])


@router.post("/sources/test")
def sources_test(body: SourceConfig) -> dict:
    """외부 DB 접속을 테스트하고 가져올 수 있는 테이블 목록을 반환한다."""
    try:
        return {"tables": sources.test_connection(body.model_dump())}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"접속 실패: {exc}")


@router.post("/projects/live")
def create_live_project(body: LiveProjectCreate) -> dict:
    """라이브 단체를 만든다: 복사 없이 소스 접속정보만 저장(push-down)."""
    cfg = body.config.model_dump()
    try:
        sources.test_connection(cfg)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"접속 실패: {exc}")
    try:
        proj = projects.create_live_project(body.key, body.name, cfg)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    # 연동 시점에 미리 벡터 인덱싱(백그라운드) → 첫 채팅이 빠름.
    retrieval.index_project_async(proj["key"])
    return {"project": proj}


@router.post("/sources/import")
def sources_import(body: SourceImport) -> dict:
    """단체 생성 + 외부 DB 전체 복사 '잡'을 시작하고 job_id를 즉시 반환한다.

    실제 복사는 백그라운드 스레드에서 청크·병렬·체크포인트로 진행된다.
    """
    cfg = body.config.model_dump()
    try:
        tables = body.tables or sources.test_connection(cfg)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"접속/테이블 조회 실패: {exc}")
    if not tables:
        raise HTTPException(status_code=400, detail="가져올 테이블이 없습니다")
    try:
        job_id = etl.start_job(body.key, cfg, tables, body.name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"job_id": job_id, "project": projects.project_key(body.key), "tables": len(tables)}


@router.get("/import/jobs/{job_id}")
def import_job(job_id: str) -> dict:
    """import 잡의 진행 상황(요약 + 테이블별 + 최근 로그)을 반환한다(폴링용)."""
    try:
        return etl.job_status(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.post("/import/jobs/{job_id}/retry")
def import_retry(job_id: str) -> dict:
    """잡에서 아직 완료되지 않은 테이블만 체크포인트부터 다시 복사한다."""
    try:
        started = etl.retry_job(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {"job_id": job_id, "retrying": started}
