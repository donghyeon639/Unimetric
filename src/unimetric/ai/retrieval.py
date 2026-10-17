"""Public RAG interface used by routes, ingestion, and developer tools."""

from .rag.context import build_context
from .rag.index import index_project, index_project_async

__all__ = ["build_context", "index_project", "index_project_async"]
