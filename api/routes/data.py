"""데이터 라우트 — 테이블/스키마 조회, 파일 업로드, 미리보기."""

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from ...ai import retrieval
from ...data import ingest
from ...data import query as engine
from ..deps import require_project

router = APIRouter(prefix="/api", tags=["data"])


@router.get("/tables")
def tables(project: str) -> dict:
    """단체의 테이블 목록을 반환한다."""
    require_project(project)
    return {"tables": engine.list_tables(project)}


@router.get("/schema")
def schema(project: str) -> dict:
    """단체의 스키마(테이블별 컬럼/타입)를 반환한다."""
    require_project(project)
    return {"schema": engine.get_schema(project)}


@router.get("/columns")
def columns(project: str, table: str) -> dict:
    """테이블 '하나'의 컬럼만 가볍게 반환한다(편집 모달 빠른 로드용)."""
    require_project(project)
    try:
        return {"columns": engine.table_columns(project, table)}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/upload")
async def upload(project: str = Form(...), file: UploadFile = File(...)) -> dict:
    """업로드된 파일을 단체에 적재하고 적재 결과를 반환한다."""
    require_project(project)
    data = await file.read()
    try:
        loaded = ingest.ingest_upload(file.filename, data, project)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    retrieval.index_project_async(project)  # 적재 후 미리 벡터 인덱싱(백그라운드)
    return {"loaded": loaded}


@router.get("/preview")
def preview(project: str, table: str, offset: int = 0, limit: int = 200) -> dict:
    """테이블 데이터를 페이지네이션으로 미리 본다(라이브/로컬 공통, DBeaver식)."""
    require_project(project)
    try:
        return engine.preview_table(project, table, offset, limit)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))
