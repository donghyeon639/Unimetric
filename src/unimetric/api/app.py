"""FastAPI 앱 조립 — 정적 UI + 기능별 라우터 등록.

설계 의도:
- 엔드포인트는 기능별 라우터(routes/*)로 나뉘고, 여기서 모아 붙인다.
- 시작 시 DB가 차가워도 죽지 않는다: init_db()가 연결 오류를 삼켜 UI는 뜬다.
"""

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from ..core.db import get_engine, init_db
from .routes import builder, chat, data, metrics, projects, sources

app = FastAPI(title="unimetric", version="0.1.0")

# 웹 UI 디렉터리: unimetric/web (이 파일은 unimetric/api/app.py).
_WEB_DIR = Path(__file__).resolve().parents[1] / "web"
app.mount("/assets", StaticFiles(directory=_WEB_DIR / "assets"), name="assets")


@app.on_event("startup")
def _startup() -> None:
    """앱 기동 시 DB 부트스트랩(확장/메타 테이블)을 수행한다."""
    init_db()


@app.get("/")
def index() -> FileResponse:
    """비개발자용 단일 페이지 웹 UI를 반환한다."""
    return FileResponse(_WEB_DIR / "index.html")


@app.get("/api/health")
def health() -> dict:
    """헬스 체크."""
    return {"status": "ok"}


@app.get("/api/ready")
def ready():
    """Report database readiness separately from process liveness."""
    try:
        with get_engine().connect() as conn:
            table = conn.execute(text("SELECT to_regclass('public.unimetric_projects')")).scalar()
            if not table:
                return JSONResponse(status_code=503, content={"status": "not_ready", "database": "not_initialized"})
    except Exception:
        return JSONResponse(status_code=503, content={"status": "not_ready", "database": "unavailable"})
    return {"status": "ready", "database": "ok"}


# 기능별 라우터 등록.
for _router_module in (projects, data, sources, metrics, builder, chat):
    app.include_router(_router_module.router)
