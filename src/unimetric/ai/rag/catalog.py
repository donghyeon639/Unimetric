"""Build searchable table and metric documents from the project catalog."""

from ...data import query as dq
from ...semantic import model as metrics
from ...workspace import projects

def _trunc(v, n: int = 40) -> str:
    s = str(v)
    return s if len(s) <= n else s[:n] + "…"


def _table_documents(project: str, *, schema=None, fks=None, samples=True) -> list[tuple[str, str]]:
    """테이블별 (ref, content) 문서 목록. content = 컬럼+관계+샘플행."""
    schema = dq.get_schema(project) if schema is None else schema
    fks = dq.foreign_keys(project) if fks is None else fks
    docs = []
    for t, cols in schema.items():
        col_str = ", ".join(f"{c['column']}({c['type']})" for c in cols)
        doc = f"테이블 {t}. 컬럼: {col_str}."
        rel = fks.get(t)
        if rel:
            doc += " 관계: " + "; ".join(
                f"{r['column']}→{r['ref_table']}.{r['ref_column']}" for r in rel
            ) + "."
        if not samples:
            docs.append((t, doc))
            continue
        try:
            rows = dq.preview_table(project, t, 0, 2)["rows"]
            if rows:
                doc += " 예시행: " + " | ".join(
                    ", ".join(f"{k}={_trunc(v)}" for k, v in row.items()) for row in rows
                )
        except Exception:
            pass
        docs.append((t, doc))
    return docs


def _metric_documents(project: str) -> list[tuple[str, str]]:
    """지표별 (ref, content) 문서 목록(재사용 후보 검색용)."""
    proj = projects.get_project(project)
    model = metrics.load_model(proj.get("metrics_yaml") or "")
    docs = []
    for m in model.list_metrics():
        doc = f"지표 {m['name']}: {m.get('description') or ''} (엔티티 {m['entity']}, {m['type']})"
        docs.append((m["name"], doc))
    return docs
