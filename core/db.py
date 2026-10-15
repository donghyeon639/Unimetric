"""DB 연결 계층 — PostgreSQL 단독.

설계 의도:
- 타깃 저장소는 PostgreSQL 하나로 고정한다(SQLite 백엔드 제거). 프로젝트 격리는
  '프로젝트 1개 = 스키마 1개'로 구현하고, 쿼리 시 search_path로 한정한다.
- 단일 Engine(커넥션 풀)을 캐시해 전 모듈이 공유한다. pool_pre_ping으로 끊긴
  커넥션을 자동 복구한다.
- 부트스트랩(확장/메타테이블)은 멱등이며, DB가 아직 안 떠 있어도 앱이 죽지
  않도록 예외를 삼킨다(웹 UI는 떠야 하니까).
"""

import os
from functools import lru_cache

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine


def _database_url() -> str:
    """Postgres 연결 URL을 환경변수로 조립한다(기본값은 docker 컨테이너와 일치)."""
    user = os.getenv("POSTGRES_USER", "unimetric")
    password = os.getenv("POSTGRES_PASSWORD", "unimetric")
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = os.getenv("POSTGRES_PORT", "5432")
    db = os.getenv("POSTGRES_DB", "unimetric")
    return f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{db}"


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    """프로세스 전역 SQLAlchemy 엔진(커넥션 풀)을 한 번만 만들어 캐시해 반환한다."""
    return create_engine(
        _database_url(),
        pool_pre_ping=True,
        pool_size=10,
        max_overflow=20,  # ETL 병렬 워커가 동시에 커넥션을 쓰므로 여유를 둔다.
        future=True,
    )


def get_project_engine(key: str) -> Engine:
    """프로젝트 데이터용 엔진. Postgres 단독이라 메인 엔진을 공유한다(스키마로 격리)."""
    return get_engine()


def ensure_pgvector() -> None:
    """pgvector 확장을 활성화한다(반복 호출 안전, 확장이 없으면 무시)."""
    try:
        with get_engine().begin() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    except Exception as exc:  # plain postgres이거나 DB 미기동일 수 있으니 크래시 금지
        print(f"[unimetric] ensure_pgvector skipped: {exc}")


def ensure_metadata() -> None:
    """레지스트리/임포트 잡/체크포인트 메타 테이블을 생성한다(public 스키마)."""
    ddls = [
        # 프로젝트 레지스트리.
        """
        CREATE TABLE IF NOT EXISTS public.unimetric_projects (
            key          text PRIMARY KEY,
            name         text NOT NULL,
            schema_name  text NOT NULL UNIQUE,
            metrics_yaml text NOT NULL DEFAULT '',
            created_at   timestamptz NOT NULL DEFAULT now()
        )
        """,
        # 라이브 모드 지원: 프로젝트가 외부 소스에 push-down 쿼리하는지(live) 또는
        # 우리 Postgres에 적재된 데이터를 쓰는지(local). source_config는 라이브 접속정보.
        "ALTER TABLE public.unimetric_projects ADD COLUMN IF NOT EXISTS mode text NOT NULL DEFAULT 'local'",
        "ALTER TABLE public.unimetric_projects ADD COLUMN IF NOT EXISTS source_config jsonb",
        # 임포트 잡(외부 DB -> 우리 Postgres 전체 복사 1회 단위).
        """
        CREATE TABLE IF NOT EXISTS public.unimetric_import_jobs (
            id          text PRIMARY KEY,
            project_key text NOT NULL,
            status      text NOT NULL,            -- running|done|failed
            config      jsonb,                    -- 소스 접속정보(비밀번호 제외 권장)
            message     text DEFAULT '',
            created_at  timestamptz NOT NULL DEFAULT now(),
            updated_at  timestamptz NOT NULL DEFAULT now()
        )
        """,
        # 테이블별 진행/체크포인트(재시도의 단위).
        """
        CREATE TABLE IF NOT EXISTS public.unimetric_import_progress (
            job_id        text NOT NULL,
            table_name    text NOT NULL,
            status        text NOT NULL,          -- pending|running|done|failed
            pk_column     text,                   -- 청크 분할 기준 PK(없으면 NULL=offset 방식)
            source_rows   bigint,                 -- 소스 전체 행수(검증용)
            copied_rows   bigint NOT NULL DEFAULT 0,
            last_pk       text,                   -- 체크포인트: 마지막으로 복사한 PK 값
            last_offset   bigint NOT NULL DEFAULT 0,  -- PK 없을 때 체크포인트
            checksum_ok   boolean,                -- 검증 통과 여부
            error         text DEFAULT '',
            updated_at    timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (job_id, table_name)
        )
        """,
        # 임포트 로그(어느 테이블/청크에서 무슨 일이 있었는지 추적).
        """
        CREATE TABLE IF NOT EXISTS public.unimetric_import_logs (
            id         bigserial PRIMARY KEY,
            job_id     text NOT NULL,
            table_name text,
            level      text NOT NULL DEFAULT 'info',  -- info|warn|error
            message    text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now()
        )
        """,
        # 채팅 세션 — 대화 상태를 서버가 소유. summary=오래된 턴 압축,
        # summarized_count=요약에 접힌 메시지 수(그 이후만 원문으로 보냄).
        """
        CREATE TABLE IF NOT EXISTS public.unimetric_chat_sessions (
            id               text PRIMARY KEY,
            project          text NOT NULL,
            summary          text NOT NULL DEFAULT '',
            summarized_count int  NOT NULL DEFAULT 0,
            created_at       timestamptz NOT NULL DEFAULT now(),
            updated_at       timestamptz NOT NULL DEFAULT now()
        )
        """,
        # 세션의 메시지(원문 기록).
        """
        CREATE TABLE IF NOT EXISTS public.unimetric_chat_messages (
            id         bigserial PRIMARY KEY,
            session_id text NOT NULL,
            role       text NOT NULL,            -- user | assistant
            content    text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now()
        )
        """,
        "CREATE INDEX IF NOT EXISTS unimetric_chat_msg_sess ON public.unimetric_chat_messages (session_id, id)",
    ]
    try:
        with get_engine().begin() as conn:
            for ddl in ddls:
                conn.execute(text(ddl))
    except Exception as exc:
        print(f"[unimetric] ensure_metadata skipped: {exc}")

    # 카탈로그 임베딩 테이블(pgvector). 확장이 없으면 실패하므로 별도 트랜잭션으로
    # 분리해, 위의 핵심 테이블 생성이 같이 롤백되지 않게 한다.
    edim = int(os.getenv("EMBED_DIM", "1024"))
    emb_ddls = [
        f"""
        CREATE TABLE IF NOT EXISTS public.unimetric_embeddings (
            id        bigserial PRIMARY KEY,
            project   text NOT NULL,
            kind      text NOT NULL,        -- table | metric | glossary
            ref       text NOT NULL,        -- 테이블명/지표명 등
            content   text NOT NULL,
            embedding vector({edim})
        )
        """,
        "CREATE INDEX IF NOT EXISTS unimetric_emb_proj ON public.unimetric_embeddings (project, kind)",
    ]
    try:
        with get_engine().begin() as conn:
            for ddl in emb_ddls:
                conn.execute(text(ddl))
    except Exception as exc:
        print(f"[unimetric] embeddings table skipped (pgvector 미설치?): {exc}")


def init_db() -> None:
    """앱 시작 시 한 번 호출되는 부트스트랩(확장 + 메타 테이블)."""
    ensure_pgvector()
    ensure_metadata()
