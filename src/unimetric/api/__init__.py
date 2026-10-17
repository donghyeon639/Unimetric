"""API 패키지. uvicorn은 `unimetric.api:app`으로 앱을 찾는다."""

from .app import app

__all__ = ["app"]
