"""읽기 전용 쿼리 실행 — 프로젝트 모드에 따라 라우팅.

두 가지 모드:
- **local**: 데이터가 우리 Postgres 프로젝트 스키마에 적재돼 있음(파일 업로드/복사).
  search_path를 프로젝트 스키마로 잡고 우리 Postgres에서 실행.
- **live**: 데이터를 복사하지 않고, 컴파일된 SQL을 외부 소스에 직접 push-down.
  소스에서 전체 데이터로 집계하므로 결과는 정확하고 항상 최신이다.

공통:
- `run_select`는 데이터가 나가는 유일한 통로. 단일 읽기 전용 SELECT/CTE만 허용.
- LIMIT로 결과 크기를 제한하고, 라이브는 statement_timeout으로 무거운 쿼리를 방어,
  같은 쿼리는 짧은 TTL 캐시로 소스 부하를 줄인다.
"""

import json
import re
import time

from sqlalchemy import create_engine, inspect, text

from ..workspace import projects
from . import sources
from ..core.db import get_engine

# 단일 SELECT 또는 CTE(WITH ...)만 허용. 그 외는 전부 거부.
_SELECT_RE = re.compile(r"^\s*(select|with)\b", re.IGNORECASE)

# 라이브 소스 엔진 캐시(접속정보별 1개).
_SOURCE_ENGINES: dict[str, object] = {}
# 라이브 결과 캐시: key=(project, sql, limit) -> (만료시각, rows).
_RESULT_CACHE: dict[tuple, tuple] = {}
_CACHE_TTL = 30.0           # 초
_LIVE_TIMEOUT_MS = 30000    # 라이브 쿼리 statement timeout


def _validate_select(sql: str) -> str:
    """단일 읽기 전용 SELECT/CTE인지 검증하고, 다듬은 SQL을 반환하거나 예외."""
    stripped = sql.strip().rstrip(";").strip()
    if not stripped:
        raise ValueError("빈 SQL입니다")
    if ";" in stripped:
        raise ValueError("단일 문장만 허용됩니다")
    if not _SELECT_RE.match(stripped):
        raise ValueError("읽기 전용 SELECT/CTE만 허용됩니다")
    return stripped


def _source_engine(project: str):
    """라이브 프로젝트의 소스 엔진을 만들어 캐시해 반환한다."""
    cfg = projects.source_config(project)
    key = json.dumps(cfg, sort_keys=True)
    if key not in _SOURCE_ENGINES:
        _SOURCE_ENGINES[key] = sources.external_engine(cfg)
    return _SOURCE_ENGINES[key]


def _apply_timeout(conn, dialect: str) -> None:
    """라이브 쿼리에 방언별 statement timeout을 건다(무거운 쿼리 방어, 실패 무시)."""
    try:
        if dialect == "postgresql":
            conn.execute(text(f"SET statement_timeout = {_LIVE_TIMEOUT_MS}"))
        elif dialect == "mysql":
            conn.execute(text(f"SET max_execution_time = {_LIVE_TIMEOUT_MS}"))
    except Exception:
        pass


# --------------------------------------------------------------------------
# 스키마/테이블 조회 (모드별)
# --------------------------------------------------------------------------
def list_tables(project: str) -> list[str]:
    """프로젝트의 테이블 목록(local: 우리 스키마 / live: 소스)을 반환한다."""
    if projects.is_live(project):
        return sources.list_tables(_source_engine(project))
    schema = projects.schema_of(project)
    with get_engine().connect() as conn:
        rows = conn.execute(
            text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = :schema AND table_type = 'BASE TABLE' "
                "ORDER BY table_name"
            ),
            {"schema": schema},
        )
        return [r[0] for r in rows]


def get_schema(project: str) -> dict[str, list[dict]]:
    """프로젝트 스키마(테이블별 컬럼/타입)를 반환한다(모드별)."""
    if projects.is_live(project):
        return sources.full_schema(_source_engine(project))
    schema = projects.schema_of(project)
    with get_engine().connect() as conn:
        rows = conn.execute(
            text(
                "SELECT table_name, column_name, data_type "
                "FROM information_schema.columns "
                "WHERE table_schema = :schema "
                "ORDER BY table_name, ordinal_position"
            ),
            {"schema": schema},
        )
        out: dict[str, list[dict]] = {}
        for table_name, column, dtype in rows:
            out.setdefault(table_name, []).append({"column": column, "type": dtype})
        return out


# --------------------------------------------------------------------------
# 쿼리 실행 (모드별)
# --------------------------------------------------------------------------
def run_select(sql: str, project: str, limit: int = 1000) -> list[dict]:
    """읽기 전용 SELECT를 모드에 맞는 대상에서 실행하고 limit행까지 반환한다."""
    stripped = _validate_select(sql)
    wrapped = f"SELECT * FROM ( {stripped} ) AS _q LIMIT {int(limit)}"

    if projects.is_live(project):
        return _run_live(project, wrapped, sql, limit)

    # local: 우리 Postgres 프로젝트 스키마.
    schema = projects.schema_of(project)
    with get_engine().connect() as conn:
        conn.execute(text(f'SET search_path TO "{schema}"'))
        result = conn.execute(text(wrapped))
        return [dict(r) for r in result.mappings()]


def _run_live(project: str, wrapped: str, raw_sql: str, limit: int) -> list[dict]:
    """라이브: 소스에 push-down 실행(타임아웃 + 짧은 TTL 캐시)."""
    cache_key = (project, raw_sql, limit)
    hit = _RESULT_CACHE.get(cache_key)
    if hit and hit[0] > time.time():
        return hit[1]

    engine = _source_engine(project)
    with engine.connect() as conn:
        _apply_timeout(conn, engine.dialect.name)
        result = conn.execute(text(wrapped))
        rows = [dict(r) for r in result.mappings()]

    _RESULT_CACHE[cache_key] = (time.time() + _CACHE_TTL, rows)
    return rows


def preview_table(project: str, table: str, offset: int = 0, limit: int = 200) -> dict:
    """DBeaver식 테이블 미리보기: SELECT * 페이지네이션(라이브/로컬 공통)."""
    # 식별자는 모드별 대상에서 안전하게 인용한다.
    if projects.is_live(project):
        engine = _source_engine(project)
        q = engine.dialect.identifier_preparer.quote(table)
        sql = f"SELECT * FROM {q} LIMIT {int(limit)} OFFSET {int(offset)}"
        with engine.connect() as conn:
            _apply_timeout(conn, engine.dialect.name)
            rows = [dict(r) for r in conn.execute(text(sql)).mappings()]
        return {"sql": sql, "rows": rows}
    # local: 프로젝트 스키마에서.
    schema = projects.schema_of(project)
    sql = f'SELECT * FROM "{schema}"."{table}" LIMIT {int(limit)} OFFSET {int(offset)}'
    with get_engine().connect() as conn:
        rows = [dict(r) for r in conn.execute(text(sql)).mappings()]
    return {"sql": sql, "rows": rows}


def table_columns(project: str, table: str) -> list[dict]:
    """테이블 '하나'의 컬럼 목록만 가볍게 반환한다(편집 모달 등 빠른 로드용).

    get_schema는 라이브 소스의 모든 테이블을 introspect해 느리므로, 단일 테이블만
    조회하는 가벼운 경로를 따로 둔다.
    """
    if projects.is_live(project):
        insp = inspect(_source_engine(project))
        return [{"column": c["name"], "type": str(c["type"])} for c in insp.get_columns(table)]
    schema = projects.schema_of(project)
    with get_engine().connect() as conn:
        rows = conn.execute(
            text(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_schema = :s AND table_name = :t ORDER BY ordinal_position"
            ),
            {"s": schema, "t": table},
        )
        return [{"column": r[0], "type": r[1]} for r in rows]


def foreign_keys(project: str) -> dict[str, list[dict]]:
    """테이블별 외래키 관계를 {table: [{column, ref_table, ref_column}]}로 반환한다.

    조인 경로를 AI에 알려주기 위함(라이브=소스, 로컬=프로젝트 스키마 introspection).
    """
    if projects.is_live(project):
        insp = inspect(_source_engine(project))
        schema = None
    else:
        insp = inspect(get_engine())
        schema = projects.schema_of(project)
    out: dict[str, list[dict]] = {}
    try:
        tables = insp.get_table_names(schema=schema)
    except Exception:
        return out
    for t in tables:
        rels = []
        try:
            for fk in insp.get_foreign_keys(t, schema=schema):
                cols = fk.get("constrained_columns") or []
                refs = fk.get("referred_columns") or []
                for c, rc in zip(cols, refs):
                    rels.append({"column": c, "ref_table": fk.get("referred_table"), "ref_column": rc})
        except Exception:
            pass
        if rels:
            out[t] = rels
    return out
