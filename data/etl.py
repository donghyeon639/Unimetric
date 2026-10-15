"""견고한 ETL — 외부 DB의 테이블 전체를 우리 Postgres로 복사한다.

핵심 설계(사용자 요구 반영):
- **전체 복사**(샘플 아님). 테이블마다 청크(LIMIT/OFFSET) 단위로 읽어 적재한다.
- **테이블 단위 병렬**(ThreadPoolExecutor) — 테이블이 많을수록 빨라진다.
- **체크포인트**: 테이블별 last_offset/copied_rows/status를 DB에 기록한다.
  중간에 실패해도 그 지점부터 **재개**할 수 있다(append로 이어붙임 → 중복 없음).
- **실패 격리 + 로그**: 한 테이블이 실패해도 나머지는 계속. 어느 테이블에서
  무슨 오류가 났는지 import_logs/progress에 남긴다. 재시도 엔드포인트로 실패한
  테이블만 체크포인트부터 다시 돌린다.
- **검증**: 복사 후 소스 행수 vs 타깃 행수를 비교한다(checksum_ok).

의식적 보류(이유):
- 인덱스/제약 비활성화→재생성: 타깃 테이블을 새로 만들므로 첫 적재엔 인덱스가
  없어 효과가 없다.
- pg_dump -Z/gzip 압축: pg_dump 바이너리 의존 + 행 단위 타입 변환 파이프와
  충돌하고 MySQL 소스엔 부적용. 병렬 청크로 속도는 이미 확보.
- 청크 '내부'까지 병렬(PK 범위 분할)은 체크포인트 단순화를 위해 테이블 단위
  병렬 + 테이블 내 청크 순차로 했다(대부분의 '테이블 많은' 사례에 충분).
"""

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import text

from . import ingest, sources
from ..workspace import projects
from ..core.db import get_engine

CHUNK_SIZE = 5000      # 청크당 행수
MAX_WORKERS = 4        # 동시에 복사할 테이블 수


# --------------------------------------------------------------------------
# 메타(잡/진행/로그) 헬퍼
# --------------------------------------------------------------------------
def _log(job_id: str, table: str | None, level: str, message: str) -> None:
    """import_logs에 한 줄 남긴다(어디서 무슨 일이 있었는지 추적)."""
    with get_engine().begin() as conn:
        conn.execute(
            text(
                "INSERT INTO public.unimetric_import_logs (job_id, table_name, level, message) "
                "VALUES (:j, :t, :l, :m)"
            ),
            {"j": job_id, "t": table, "l": level, "m": message[:1000]},
        )


def _set_job(job_id: str, **fields) -> None:
    """잡 상태/메시지를 갱신한다."""
    sets, params = [], {"id": job_id}
    for k, v in fields.items():
        sets.append(f"{k} = :{k}")
        params[k] = v
    sets.append("updated_at = now()")
    with get_engine().begin() as conn:
        conn.execute(
            text(f"UPDATE public.unimetric_import_jobs SET {', '.join(sets)} WHERE id = :id"),
            params,
        )


def _set_progress(job_id: str, table: str, **fields) -> None:
    """테이블 진행/체크포인트를 갱신한다(없으면 생성)."""
    cols = ["job_id", "table_name"]
    vals = {"job_id": job_id, "table_name": table}
    for k, v in fields.items():
        cols.append(k)
        vals[k] = v
    placeholders = ", ".join(f":{c}" for c in cols)
    updates = ", ".join(f"{k} = EXCLUDED.{k}" for k in fields)
    updates = (updates + ", " if updates else "") + "updated_at = now()"
    with get_engine().begin() as conn:
        conn.execute(
            text(
                f"INSERT INTO public.unimetric_import_progress ({', '.join(cols)}) "
                f"VALUES ({placeholders}) "
                f"ON CONFLICT (job_id, table_name) DO UPDATE SET {updates}"
            ),
            vals,
        )


def _get_progress(job_id: str, table: str) -> dict:
    """테이블의 현재 진행/체크포인트를 읽는다(없으면 기본값)."""
    with get_engine().connect() as conn:
        row = conn.execute(
            text(
                "SELECT status, last_offset, copied_rows FROM public.unimetric_import_progress "
                "WHERE job_id = :j AND table_name = :t"
            ),
            {"j": job_id, "t": table},
        ).mappings().first()
    return dict(row) if row else {"status": "pending", "last_offset": 0, "copied_rows": 0}


def _target_count(project: str, table: str) -> int:
    """타깃(프로젝트 스키마) 테이블의 행수를 센다(검증용)."""
    schema = projects.schema_of(project)
    with get_engine().connect() as conn:
        return int(conn.execute(text(f'SELECT COUNT(*) FROM "{schema}"."{table}"')).scalar())


# --------------------------------------------------------------------------
# 한 테이블 복사 (체크포인트 기반 재개)
# --------------------------------------------------------------------------
def _copy_table(job_id: str, project: str, ext_engine, source_table: str) -> bool:
    """소스 테이블 하나를 청크 단위로 타깃에 복사한다. 성공 여부 반환."""
    target = projects.safe_identifier(source_table)
    try:
        src_rows = sources.row_count(ext_engine, source_table)
        pk = sources.primary_key(ext_engine, source_table)
        order_col = pk or sources.first_column(ext_engine, source_table)
        prog = _get_progress(job_id, source_table)
        offset = int(prog["last_offset"])
        copied = int(prog["copied_rows"])

        _set_progress(
            job_id, source_table, status="running", pk_column=pk,
            source_rows=src_rows, error="",
        )
        resume = " (재개)" if offset > 0 else ""
        if not pk:
            _log(job_id, source_table, "warn",
                 f"PK가 없어 '{order_col}' 기준 정렬로 복사합니다(대용량/변경 중이면 순서 주의).")
        _log(job_id, source_table, "info",
             f"시작{resume}: 소스 {src_rows}행, pk={pk}, offset={offset}")

        first = offset == 0
        while True:
            df = sources.read_chunk(ext_engine, source_table, order_col, offset, CHUNK_SIZE)
            if df.empty:
                if first:
                    # 빈 테이블이라도 타깃을 만들어 둔다(컬럼만).
                    import pandas as pd
                    cols = sources.column_names(ext_engine, source_table)
                    ingest.write_frame(pd.DataFrame(columns=cols), project, target, "replace")
                break
            mode = "replace" if first else "append"  # 첫 청크로 타입 추론 + 기존 데이터 정리
            ingest.write_frame(df, project, target, mode)
            first = False
            n = len(df)
            offset += n
            copied += n
            _set_progress(job_id, source_table, last_offset=offset, copied_rows=copied,
                          status="running")
            _log(job_id, source_table, "info", f"{copied}/{src_rows}행 복사")
            if n < CHUNK_SIZE:
                break

        # 검증: 소스 vs 타깃 행수 비교.
        tgt_rows = _target_count(project, target)
        ok = tgt_rows == src_rows
        _set_progress(job_id, source_table, status="done", copied_rows=copied,
                      checksum_ok=ok)
        _log(job_id, source_table, "info" if ok else "warn",
             f"완료: 타깃 {tgt_rows} vs 소스 {src_rows} -> {'일치' if ok else '불일치(검증 실패)'}")
        return True
    except Exception as exc:  # 한 테이블 실패가 전체를 막지 않게.
        _set_progress(job_id, source_table, status="failed", error=str(exc)[:500])
        _log(job_id, source_table, "error", f"실패: {exc}")
        return False


# --------------------------------------------------------------------------
# 잡 실행 (테이블 병렬)
# --------------------------------------------------------------------------
def _run_job(job_id: str, project: str, cfg: dict, tables: list[str]) -> None:
    """잡 본체: 테이블들을 병렬로 복사하고 최종 상태를 기록한다(스레드에서 실행)."""
    ext_engine = sources.external_engine(cfg)
    try:
        _set_job(job_id, status="running", message=f"{len(tables)}개 테이블 복사 시작")
        results: list[bool] = []
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            futures = [pool.submit(_copy_table, job_id, project, ext_engine, t) for t in tables]
            for f in futures:
                results.append(f.result())
        ok = sum(1 for r in results if r)
        failed = len(results) - ok
        status = "done" if failed == 0 else "failed"
        _set_job(job_id, status=status,
                 message=f"완료: 성공 {ok} / 실패 {failed} (총 {len(tables)})")
        _log(job_id, None, "info" if failed == 0 else "error",
             f"잡 종료: 성공 {ok}, 실패 {failed}")
        # 복사 완료 후 미리 벡터 인덱싱(백그라운드). 지연 import로 순환참조 회피.
        try:
            from ..ai import retrieval
            retrieval.index_project_async(project)
        except Exception:
            pass
    except Exception as exc:
        _set_job(job_id, status="failed", message=f"잡 오류: {exc}")
        _log(job_id, None, "error", f"잡 오류: {exc}")
    finally:
        ext_engine.dispose()


def start_job(project: str, cfg: dict, tables: list[str], name: str | None = None) -> str:
    """프로젝트를 만들고 import 잡을 등록한 뒤, 백그라운드 스레드로 복사를 시작한다."""
    projects.create_project(project, name)
    job_id = "job_" + uuid.uuid4().hex[:12]
    import json
    # 재시도 시 재접속해야 하므로 config를 저장한다(로컬 도구 기준; 비밀번호 포함).
    with get_engine().begin() as conn:
        conn.execute(
            text(
                "INSERT INTO public.unimetric_import_jobs (id, project_key, status, config, message) "
                "VALUES (:id, :p, 'running', CAST(:cfg AS jsonb), :m)"
            ),
            {"id": job_id, "p": projects.project_key(project),
             "cfg": json.dumps(cfg), "m": "대기 중"},
        )
        for t in tables:
            conn.execute(
                text(
                    "INSERT INTO public.unimetric_import_progress (job_id, table_name, status) "
                    "VALUES (:j, :t, 'pending') ON CONFLICT (job_id, table_name) DO NOTHING"
                ),
                {"j": job_id, "t": t},
            )
    threading.Thread(
        target=_run_job, args=(job_id, projects.project_key(project), cfg, tables),
        daemon=True,
    ).start()
    return job_id


def retry_job(job_id: str) -> bool:
    """잡에서 아직 'done'이 아닌 테이블만 체크포인트부터 다시 복사한다."""
    with get_engine().connect() as conn:
        job = conn.execute(
            text("SELECT project_key, config FROM public.unimetric_import_jobs WHERE id = :id"),
            {"id": job_id},
        ).mappings().first()
        if not job:
            raise KeyError(f"잡 '{job_id}'를 찾을 수 없습니다")
        rows = conn.execute(
            text(
                "SELECT table_name FROM public.unimetric_import_progress "
                "WHERE job_id = :j AND status <> 'done'"
            ),
            {"j": job_id},
        ).all()
    tables = [r[0] for r in rows]
    if not tables:
        return False
    cfg = job["config"]  # jsonb -> dict
    project = job["project_key"]
    _set_job(job_id, status="running", message=f"재시도: {len(tables)}개 테이블")
    threading.Thread(target=_run_job, args=(job_id, project, cfg, tables), daemon=True).start()
    return True


def job_status(job_id: str) -> dict:
    """잡의 전체 상태(요약 + 테이블별 진행 + 최근 로그)를 반환한다(UI 폴링용)."""
    with get_engine().connect() as conn:
        job = conn.execute(
            text(
                "SELECT id, project_key, status, message, created_at, updated_at "
                "FROM public.unimetric_import_jobs WHERE id = :id"
            ),
            {"id": job_id},
        ).mappings().first()
        if not job:
            raise KeyError(f"잡 '{job_id}'를 찾을 수 없습니다")
        progress = conn.execute(
            text(
                "SELECT table_name, status, source_rows, copied_rows, checksum_ok, error "
                "FROM public.unimetric_import_progress WHERE job_id = :j ORDER BY table_name"
            ),
            {"j": job_id},
        ).mappings().all()
        logs = conn.execute(
            text(
                "SELECT table_name, level, message, created_at FROM public.unimetric_import_logs "
                "WHERE job_id = :j ORDER BY id DESC LIMIT 50"
            ),
            {"j": job_id},
        ).mappings().all()
    return {
        "job": dict(job),
        "tables": [dict(r) for r in progress],
        "logs": [dict(r) for r in logs],
    }
