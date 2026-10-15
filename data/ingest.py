"""엑셀/CSV/SQLite 파일을 해당 프로젝트의 저장소(스키마/파일)로 적재한다.

이유:
- 사용자가 올리는 파일은 지저분하므로 테이블/컬럼명을 모두 안전한 식별자로
  바꿔, 임의의 스프레드시트가 쿼리를 깨거나 SQL 인젝션을 유발하지 못하게 한다.
- 항상 특정 프로젝트의 저장소에 적재하며(격리), `if_exists="replace"`로
  같은 파일을 다시 올려도 멱등하게 동작한다.
"""

import json
import sqlite3
import tempfile
from pathlib import Path

import pandas as pd
from sqlalchemy import text

from ..core.db import get_engine
from ..workspace.projects import safe_identifier, schema_of

# DB 드라이버가 그대로 바인딩할 수 있는 파이썬 기본 타입들.
_BINDABLE = (str, int, float, bool, bytes)


def _sanitize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """데이터프레임의 모든 컬럼명을 안전한 식별자로 바꾸고 중복은 번호로 구분한다."""
    seen: dict[str, int] = {}
    new_cols = []
    for col in df.columns:
        base = safe_identifier(col)
        if base in seen:
            seen[base] += 1
            base = f"{base}_{seen[base]}"
        else:
            seen[base] = 0
        new_cols.append(base)
    df = df.copy()
    df.columns = new_cols
    return df


def _coerce_value(v):
    """DB가 바인딩 못 하는 값을 문자열/JSON으로 바꾼다(UUID·jsonb·Decimal 등)."""
    # 결측치는 NULL로.
    try:
        if pd.isna(v):
            return None
    except (ValueError, TypeError):
        pass  # 배열/리스트는 pd.isna가 모호하므로 통과
    if v is None or isinstance(v, _BINDABLE):
        return v
    # Postgres jsonb 등은 dict/list로 들어오므로 JSON 문자열로 직렬화.
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False, default=str)
    # UUID, Decimal, datetime 등 나머지는 문자열로.
    return str(v)


def _coerce_unsupported(df: pd.DataFrame) -> pd.DataFrame:
    """object 타입 컬럼에서 바인딩 불가 값들을 일괄 변환한다(외부 DB import 안전화)."""
    for col in df.columns:
        if df[col].dtype == object:
            df[col] = df[col].map(_coerce_value)
    return df


def _frames_from_file(path: Path) -> dict[str, pd.DataFrame]:
    """입력 파일에서 {테이블명: 데이터프레임} 사전을 만들어 반환한다."""
    ext = path.suffix.lower()
    if ext in (".xlsx", ".xls"):
        # sheet_name=None -> 모든 시트를 시트명 기준으로 읽어 시트당 테이블 1개.
        sheets = pd.read_excel(path, sheet_name=None)
        return {safe_identifier(name): df for name, df in sheets.items()}
    if ext == ".csv":
        return {safe_identifier(path.stem): pd.read_csv(path)}
    if ext in (".db", ".sqlite", ".sqlite3"):
        frames: dict[str, pd.DataFrame] = {}
        con = sqlite3.connect(str(path))
        try:
            names = pd.read_sql(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%'",
                con,
            )["name"].tolist()
            for tbl in names:
                frames[safe_identifier(tbl)] = pd.read_sql(f'SELECT * FROM "{tbl}"', con)
        finally:
            con.close()
        return frames
    raise ValueError(f"지원하지 않는 파일 형식입니다: {ext}")


def prepare_frame(df: pd.DataFrame) -> pd.DataFrame:
    """적재 직전 정리: 컬럼명 안전화 + 바인딩 불가 타입(UUID/jsonb/Decimal) 변환."""
    return _coerce_unsupported(_sanitize_columns(df))


def ensure_schema(project: str) -> str:
    """프로젝트 스키마가 있는지 보장하고 스키마명을 반환한다."""
    schema = schema_of(project)
    with get_engine().begin() as conn:
        conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
    return schema


def write_frame(df: pd.DataFrame, project: str, table: str, mode: str = "append") -> int:
    """데이터프레임을 프로젝트 스키마의 테이블에 적재한다(mode: replace|append).

    ETL이 청크마다 호출한다. 첫 청크는 replace(타입 추론 + 기존 데이터 정리),
    이후 청크는 append. 적재한 행수를 반환한다.
    """
    schema = ensure_schema(project)
    df = prepare_frame(df)
    df.to_sql(table, get_engine(), schema=schema, if_exists=mode, index=False)
    return int(len(df))


def ingest_frames(frames: dict[str, pd.DataFrame], project: str) -> list[dict]:
    """이미 만들어진 {테이블명: df}들을 프로젝트 스키마에 적재한다(데모 시드/파일 업로드)."""
    loaded: list[dict] = []
    for table, df in frames.items():
        rows = write_frame(df, project, table, mode="replace")
        loaded.append({"table": table, "rows": rows})
    return loaded


def ingest_file(path: str | Path, project: str) -> list[dict]:
    """파일 안의 모든 테이블을 프로젝트 저장소에 적재한다.

    반환: [{"table": 이름, "rows": 행수}, ...]
    """
    return ingest_frames(_frames_from_file(Path(path)), project)


def ingest_upload(filename: str, data: bytes, project: str) -> list[dict]:
    """업로드된 바이트를 임시 파일로 저장한 뒤 적재한다(API에서 사용)."""
    suffix = Path(filename).suffix or ".bin"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(data)
        tmp_path = Path(tmp.name)
    try:
        return ingest_file(tmp_path, project)
    finally:
        tmp_path.unlink(missing_ok=True)
