"""외부 데이터베이스 소스 — 접속 / introspection / 청크 단위 읽기.

설계 의도:
- 외부 Postgres/MySQL/SQLite에 접속해 테이블 목록·PK·행수·컬럼을 조사하고,
  데이터를 '청크(LIMIT/OFFSET)' 단위로 읽는다. 실제 복사 오케스트레이션(병렬·
  체크포인트·검증)은 etl.py가 담당한다.
- 식별자 인용은 소스 방언에 맞춰 처리한다(MySQL은 백틱, Postgres는 큰따옴표).
  방언별 preparer를 써서 SELECT가 모든 소스에서 안전하게 동작하게 한다.
- 비밀번호 등 특수문자 이스케이프 문제를 피하려 URL은 SQLAlchemy URL.create로 만든다.
"""

import pandas as pd
from sqlalchemy import URL, create_engine, inspect, text
from sqlalchemy.engine import Engine

# 외부 DB 종류 -> (SQLAlchemy 드라이버, 기본 포트).
_DRIVERS = {
    "postgres": ("postgresql+psycopg2", 5432),
    "mysql": ("mysql+pymysql", 3306),
    "sqlite": ("sqlite", None),
}


def _external_url(cfg: dict) -> URL:
    """접속 설정(dict)을 SQLAlchemy URL 객체로 변환한다."""
    kind = (cfg.get("kind") or "postgres").lower()
    if kind not in _DRIVERS:
        raise ValueError(f"지원하지 않는 DB 종류입니다: {kind}")
    driver, default_port = _DRIVERS[kind]
    if kind == "sqlite":
        # SQLite는 호스트가 아니라 파일 경로(database)로 접속한다.
        return URL.create(driver, database=cfg["database"])
    port = cfg.get("port") or default_port
    return URL.create(
        driver,
        username=cfg.get("user"),
        password=cfg.get("password"),
        host=cfg.get("host"),
        port=int(port) if port else None,
        database=cfg.get("database"),
    )


def external_engine(cfg: dict) -> Engine:
    """접속 설정으로 외부 DB용 SQLAlchemy 엔진을 만든다(스레드 안전, 공유 가능)."""
    return create_engine(_external_url(cfg), pool_pre_ping=True, future=True)


def _quote(engine: Engine, name: str) -> str:
    """소스 방언에 맞는 식별자 인용(MySQL 백틱 / Postgres 큰따옴표)."""
    return engine.dialect.identifier_preparer.quote(name)


def test_connection(cfg: dict) -> list[str]:
    """외부 DB에 접속해 가져올 수 있는 테이블 이름 목록을 반환한다(연결 테스트 겸용)."""
    engine = external_engine(cfg)
    try:
        return sorted(inspect(engine).get_table_names())
    finally:
        engine.dispose()


def list_tables(engine: Engine) -> list[str]:
    """엔진으로 테이블 이름 목록을 조회한다."""
    return sorted(inspect(engine).get_table_names())


def primary_key(engine: Engine, table: str) -> str | None:
    """테이블의 단일 컬럼 PK를 반환한다(복합/없음이면 None — 정렬 안정성 용도)."""
    try:
        cols = inspect(engine).get_pk_constraint(table).get("constrained_columns") or []
    except Exception:
        cols = []
    return cols[0] if len(cols) == 1 else None


def first_column(engine: Engine, table: str) -> str | None:
    """테이블의 첫 컬럼명을 반환한다(PK가 없을 때 정렬 기준 대체용)."""
    cols = inspect(engine).get_columns(table)
    return cols[0]["name"] if cols else None


def column_names(engine: Engine, table: str) -> list[str]:
    """테이블의 컬럼명 목록을 반환한다."""
    return [c["name"] for c in inspect(engine).get_columns(table)]


def full_schema(engine: Engine) -> dict[str, list[dict]]:
    """소스 전체 스키마를 {테이블: [{column, type}, ...]}로 반환한다(라이브 모드용)."""
    insp = inspect(engine)
    out: dict[str, list[dict]] = {}
    for table in insp.get_table_names():
        out[table] = [
            {"column": c["name"], "type": str(c["type"])} for c in insp.get_columns(table)
        ]
    return out


def row_count(engine: Engine, table: str) -> int:
    """테이블의 전체 행수를 반환한다(검증용)."""
    with engine.connect() as conn:
        return int(conn.execute(text(f"SELECT COUNT(*) FROM {_quote(engine, table)}")).scalar())


def read_chunk(
    engine: Engine, table: str, order_col: str | None, offset: int, chunk_size: int
) -> pd.DataFrame:
    """테이블을 정렬해 offset부터 chunk_size행만 읽는다(체크포인트 기반 재개용).

    order_col로 정렬해 OFFSET 페이지네이션의 안정성을 확보한다. PK가 없어
    정렬 기준이 약하면 호출부가 경고를 남긴다.
    """
    sql = f"SELECT * FROM {_quote(engine, table)}"
    if order_col:
        sql += f" ORDER BY {_quote(engine, order_col)}"
    sql += f" LIMIT {int(chunk_size)} OFFSET {int(offset)}"
    with engine.connect() as conn:
        return pd.read_sql(text(sql), conn)
