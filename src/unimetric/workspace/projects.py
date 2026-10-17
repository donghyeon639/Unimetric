"""프로젝트 = 격리 경계. 프로젝트 1개 = PostgreSQL 스키마 1개.

스키마를 프로젝트마다 분리하는 이유:
- 완전한 격리: 한 프로젝트의 테이블/지표가 다른 프로젝트에서 절대 안 보인다.
  쿼리 시 search_path를 그 스키마로 잡아 격리한다.
- 단순한 생명주기: 프로젝트 삭제 = DROP SCHEMA ... CASCADE.
- 깔끔한 이름: 업로드/가져온 orders가 그 스키마 안에서 그냥 `orders`가 되어
  지표 YAML(`table: orders`)과 컴파일된 SQL이 schema-less로 유지된다.
"""

import json
import re

from sqlalchemy import text

from ..core.db import get_engine

# 프로젝트로 내주면 안 되는 예약/시스템 스키마.
_RESERVED = {"public", "information_schema", "pg_catalog", "pg_toast"}


def safe_identifier(name: str) -> str:
    """임의 문자열을 안전한 식별자로 변환한다(소문자·영숫자/_·63자·숫자시작 금지)."""
    s = str(name).strip().lower()
    s = re.sub(r"[^0-9a-z_]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    if not s:
        s = "x"
    if s[0].isdigit():
        s = "t_" + s
    return s[:63]


def project_key(raw: str) -> str:
    """입력 키를 정규화하고 예약 스키마명을 거부한 뒤 유효한 키를 반환한다."""
    key = safe_identifier(raw)
    if key in _RESERVED or key.startswith("pg_"):
        raise ValueError(f"'{key}'는 예약된 스키마명입니다. 다른 키를 쓰세요")
    return key


def create_project(key: str, name: str | None = None, metrics_yaml: str = "") -> dict:
    """프로젝트를 생성한다: 전용 스키마를 만들고 레지스트리에 등록(멱등)."""
    key = project_key(key)
    name = name or key
    with get_engine().begin() as conn:
        # 스키마가 곧 격리 경계. 키는 검증되었으므로 따옴표로 감싸 안전하게 생성.
        conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{key}"'))
        conn.execute(
            text(
                """
                INSERT INTO public.unimetric_projects (key, name, schema_name, metrics_yaml)
                VALUES (:key, :name, :schema, :yaml)
                ON CONFLICT (key) DO UPDATE SET name = EXCLUDED.name
                """
            ),
            {"key": key, "name": name, "schema": key, "yaml": metrics_yaml},
        )
    return get_project(key)


def create_live_project(key: str, name: str | None, config: dict) -> dict:
    """라이브 프로젝트를 등록한다: 데이터를 복사하지 않고 소스 접속정보만 저장.

    조회/지표/AI 질의는 이 source_config로 소스에 직접 push-down 된다.
    """
    key = project_key(key)
    name = name or key
    with get_engine().begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO public.unimetric_projects
                    (key, name, schema_name, mode, source_config)
                VALUES (:key, :name, :schema, 'live', CAST(:cfg AS jsonb))
                ON CONFLICT (key) DO UPDATE
                    SET name = EXCLUDED.name, mode = 'live',
                        source_config = EXCLUDED.source_config
                """
            ),
            {"key": key, "name": name, "schema": key, "cfg": json.dumps(config)},
        )
    return get_project(key)


def list_projects() -> list[dict]:
    """등록된 모든 프로젝트를 생성순으로 반환한다."""
    with get_engine().connect() as conn:
        rows = conn.execute(
            text(
                "SELECT key, name, schema_name, mode, created_at "
                "FROM public.unimetric_projects ORDER BY created_at"
            )
        ).mappings()
        return [dict(r) for r in rows]


def get_project(key: str) -> dict:
    """프로젝트 한 건(지표 YAML·모드·소스설정 포함)을 반환한다. 없으면 KeyError."""
    key = project_key(key)
    with get_engine().connect() as conn:
        row = conn.execute(
            text(
                "SELECT key, name, schema_name, metrics_yaml, mode, source_config, created_at "
                "FROM public.unimetric_projects WHERE key = :key"
            ),
            {"key": key},
        ).mappings().first()
    if row is None:
        raise KeyError(f"프로젝트 '{key}'를 찾을 수 없습니다")
    return dict(row)


def is_live(key: str) -> bool:
    """프로젝트가 라이브 모드(소스 push-down)인지 여부."""
    return get_project(key).get("mode") == "live"


def source_config(key: str) -> dict:
    """라이브 프로젝트의 소스 접속 설정을 반환한다."""
    cfg = get_project(key).get("source_config")
    if not cfg:
        raise ValueError(f"프로젝트 '{key}'에 소스 접속정보가 없습니다")
    return cfg


def delete_project(key: str) -> None:
    """프로젝트의 스키마(모든 테이블)와 레지스트리 항목을 삭제한다."""
    key = project_key(key)
    with get_engine().begin() as conn:
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{key}" CASCADE'))
        conn.execute(
            text("DELETE FROM public.unimetric_projects WHERE key = :key"),
            {"key": key},
        )


def set_metrics_yaml(key: str, metrics_yaml: str) -> dict:
    """프로젝트에 저장된 지표 정의 YAML을 교체한다."""
    key = project_key(key)
    with get_engine().begin() as conn:
        result = conn.execute(
            text(
                "UPDATE public.unimetric_projects SET metrics_yaml = :yaml "
                "WHERE key = :key"
            ),
            {"key": key, "yaml": metrics_yaml},
        )
        if result.rowcount == 0:
            raise KeyError(f"프로젝트 '{key}'를 찾을 수 없습니다")
    return get_project(key)


def schema_of(key: str) -> str:
    """프로젝트 키에 해당하는(검증된) 스키마명을 반환한다."""
    return get_project(key)["schema_name"]
