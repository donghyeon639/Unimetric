"""Create isolated PostgreSQL fixtures and evaluate the real Unimetric pipeline."""

import argparse
import asyncio
from decimal import Decimal
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
from time import perf_counter

ROOT = Path(__file__).resolve().parents[2]

import httpx
import psycopg2
from psycopg2 import sql as pgsql
from sqlalchemy import URL, create_engine, inspect, select, text

from .fixture import TABLES, SEED, REFERENCE, cases, fingerprint, generate, metadata, metrics_yaml, validate

LOCAL = ROOT / ".local" / "dongne-market"
SOURCE_DB = "dongne_market_v1"
APP_DB = "unimetric_market_test"
OWNER = "unimetric_fixture_owner"
READER = "dongne_market_reader"
PROJECT_LIVE = "dongne_market_v1_live"
PROJECT_COPY = "dongne_market_v1_copy"


def write_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def command(args):
    # A background PostgreSQL server can inherit pipe handles on Windows. A file
    # avoids waiting for the long-lived server to close pg_ctl's captured pipes.
    log_path = LOCAL / "commands.log"
    with log_path.open("ab") as log:
        result = subprocess.run([str(a) for a in args], stdout=log, stderr=subprocess.STDOUT,
                                timeout=60, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    if result.returncode:
        raise RuntimeError(f"{Path(args[0]).name} failed; inspect the private .local/dongne-market/commands.log")


def start_cluster(pg_bin: Path, port: int):
    LOCAL.mkdir(parents=True, exist_ok=True)
    config_path = LOCAL / "connection.json"
    if config_path.exists():
        cfg = json.loads(config_path.read_text(encoding="utf-8"))
        if cfg.get("fixture") != "dongne-market-v1" or cfg["port"] != port:
            raise RuntimeError("Existing fixture configuration differs; refusing to overwrite it")
    else:
        cfg = {"fixture": "dongne-market-v1", "port": port, "owner_password": secrets.token_urlsafe(24), "reader_password": secrets.token_urlsafe(24)}
        write_json(config_path, cfg)
    cluster = LOCAL / "pgdata"
    if not (cluster / "PG_VERSION").exists():
        password_file = LOCAL / "init-password.txt"
        password_file.write_text(cfg["owner_password"], encoding="utf-8")
        try:
            command([pg_bin / "initdb.exe", "-D", cluster, "-U", OWNER, "-A", "scram-sha-256", "--pwfile", password_file, "-E", "UTF8", "--locale=C"])
        finally:
            password_file.unlink(missing_ok=True)
    status = subprocess.run([str(pg_bin / "pg_ctl.exe"), "-D", str(cluster), "status"], capture_output=True,
                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    if status.returncode:
        command([pg_bin / "pg_ctl.exe", "-D", cluster, "-l", LOCAL / "postgres.log", "-o",
                 f"-h 127.0.0.1 -p {port} -c shared_buffers=64MB -c max_connections=40", "-w", "start"])
    conn = psycopg2.connect(host="127.0.0.1", port=port, user=OWNER, password=cfg["owner_password"], dbname="postgres", connect_timeout=3)
    conn.autocommit = True
    try:
        with conn.cursor() as cursor:
            for database in [SOURCE_DB, APP_DB]:
                cursor.execute("SELECT 1 FROM pg_database WHERE datname=%s", (database,))
                if not cursor.fetchone():
                    cursor.execute(pgsql.SQL("CREATE DATABASE {} ENCODING 'UTF8'").format(pgsql.Identifier(database)))
            cursor.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (READER,))
            if not cursor.fetchone():
                cursor.execute(pgsql.SQL("CREATE ROLE {} LOGIN PASSWORD %s").format(pgsql.Identifier(READER)), (cfg["reader_password"],))
            cursor.execute(pgsql.SQL("ALTER ROLE {} SET default_transaction_read_only = on").format(pgsql.Identifier(READER)))
    finally:
        conn.close()
    return cfg


def engine_for(cfg, database):
    return create_engine(URL.create("postgresql+psycopg2", username=OWNER, password=cfg["owner_password"], host="127.0.0.1", port=cfg["port"], database=database),
                         connect_args={"connect_timeout": 3}, future=True)


def seed_database(engine, rows):
    m = metadata()
    digest = fingerprint(rows)
    with engine.begin() as conn:
        conn.execute(text("CREATE SCHEMA IF NOT EXISTS fixture_meta"))
        conn.execute(text("CREATE TABLE IF NOT EXISTS fixture_meta.manifest (version text PRIMARY KEY, fingerprint text NOT NULL)"))
        old = conn.execute(text("SELECT fingerprint FROM fixture_meta.manifest WHERE version='v1'")).scalar()
        if old and old != digest:
            raise RuntimeError("Different seed/size already exists. Use a separate fixture database; no data was overwritten")
        if not old:
            existing = inspect(conn).get_table_names(schema="public")
            if existing:
                raise RuntimeError("Source database is not empty and has no matching fixture manifest")
            m.create_all(conn)
            for name in TABLES:
                conn.execute(m.tables[name].insert(), rows[name])
            conn.execute(text("INSERT INTO fixture_meta.manifest(version,fingerprint) VALUES ('v1',:hash)"), {"hash": digest})
            comments = {
                "neighborhoods": "판매글 등록 지역; 사용자 현재 지역과 구분", "users": "가상 사용자; name은 테스트 식별용 표시명",
                "categories": "판매글 상품 분류", "listings": "판매글 한 건; asking_price는 희망가이고 실제 거래액이 아님",
                "offers": "구매 제안 한 건; 판매글당 여러 행", "transactions": "거래 한 건; COMPLETED만 성사 거래; amount는 원 단위; completed_at은 완료 시각",
                "reviews": "거래 참여자 한 명의 후기 한 건; 거래당 최대 두 건",
            }
            for table, comment in comments.items():
                conn.execute(text(f"COMMENT ON TABLE {table} IS '{comment}'"))
        conn.execute(text(f"GRANT CONNECT ON DATABASE {SOURCE_DB} TO {READER}"))
        conn.execute(text(f"GRANT USAGE ON SCHEMA public TO {READER}"))
        conn.execute(text(f"GRANT SELECT ON ALL TABLES IN SCHEMA public TO {READER}"))
    actual = {}
    with engine.connect() as conn:
        for name in TABLES:
            records = [dict(r) for r in conn.execute(select(m.tables[name]).order_by(m.tables[name].c.id)).mappings()]
            expected = sorted(rows[name], key=lambda r: r["id"])
            if records != expected:
                raise RuntimeError(f"Stored rows differ from the generated fixture: {name}")
            actual[name] = len(records)
    return actual


def normalized(records, reference=None):
    # FastAPI may encode Decimal as a JSON string. Coerce only columns whose
    # independent reference is numeric; never normalize text IDs such as 001.
    template = list(reference[0].values()) if reference else None
    def value(v, index):
        numeric = isinstance(template[index], (int, float, Decimal)) if template and index < len(template) else isinstance(v, (int, float, Decimal))
        if numeric and not isinstance(v, bool):
            try:
                return str(Decimal(str(v)).quantize(Decimal("0.01")))
            except ArithmeticError:
                return str(v)
        return str(v)
    return sorted(tuple(value(v, i) for i, v in enumerate(row.values())) for row in records)


async def evaluate(rows, cfg, provider, ai_ids, output):
    from unimetric.core.db import ensure_metadata, get_engine
    from unimetric.workspace import projects
    from unimetric.data import query, etl
    from unimetric.semantic.model import load_model
    from unimetric.ai import retrieval
    from unimetric.api.app import app

    ensure_metadata()
    source_cfg = {"kind": "postgres", "host": "127.0.0.1", "port": cfg["port"], "user": READER,
                  "password": cfg["reader_password"], "database": SOURCE_DB}
    # A fixed fixture-only project is reused only if it already points to this fixture.
    try:
        old = projects.get_project(PROJECT_LIVE)
    except KeyError:
        old = None
    if old and old.get("source_config") != source_cfg:
        raise RuntimeError("Existing live project points somewhere else")
    projects.create_live_project(PROJECT_LIVE, "동네마켓 v1 · 원본 라이브", source_cfg)
    catalog = metrics_yaml(rows)
    projects.set_metrics_yaml(PROJECT_LIVE, catalog)
    test_cases = cases(rows)
    results = {"reference_time": REFERENCE.isoformat(), "seed": SEED, "dataset_version": "v1", "fixture_fingerprint": fingerprint(rows),
               "database_engine": "PostgreSQL 18", "source_database": SOURCE_DB, "application_database": APP_DB, "port": cfg["port"],
               "table_counts": {k: len(v) for k, v in rows.items()}, "total_rows": sum(map(len, rows.values())), "relationship_checks": validate(rows),
               "retrieval_mode": "lexical (EMBED_PROVIDER=none)", "vector_search_tested": False, "provider": provider,
               "sql_checks": [], "api_metric_checks": [], "copy_metric_checks": [], "ai_checks": []}
    for case in test_cases:
        start = perf_counter()
        got = query.run_select(case["sql"], PROJECT_LIVE)
        results["sql_checks"].append({"id": case["id"], "question": case["question"], "pass": normalized(got, case["expected"]) == normalized(case["expected"]),
                                      "expected": case["expected"], "actual": got, "elapsed_ms": round((perf_counter() - start)*1000, 2)})

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://fixture", timeout=180) as client:
        for case in test_cases:
            response = await client.post("/api/metrics/query", json={"project": PROJECT_LIVE, "metric": case["id"]})
            body = response.json()
            results["api_metric_checks"].append({"id": case["id"], "pass": response.status_code == 200 and normalized(body.get("rows", []), case["expected"]) == normalized(case["expected"]), "actual": body})

        # Run the application's genuine source-import worker; no mock database calls.
        response = await client.post("/api/sources/import", json={"key": PROJECT_COPY, "name": "동네마켓 v1 · 복사본", "config": source_cfg, "tables": list(TABLES)})
        response.raise_for_status()
        job_id = response.json()["job_id"]
        deadline = asyncio.get_running_loop().time() + 60
        while True:
            job = (await client.get(f"/api/import/jobs/{job_id}")).json()
            if job["job"]["status"] != "running":
                break
            if asyncio.get_running_loop().time() > deadline:
                raise RuntimeError("Import job did not finish in 60 seconds")
            await asyncio.sleep(.2)
        results["import_job"] = job
        projects.set_metrics_yaml(PROJECT_COPY, catalog)
        for case in test_cases:
            start = perf_counter()
            try:
                got = query.run_select(load_model(catalog).compile(case["id"]), PROJECT_COPY)
                entry = {"id": case["id"], "pass": normalized(got, case["expected"]) == normalized(case["expected"]), "actual": got}
            except Exception as exc:
                entry = {"id": case["id"], "pass": False, "error": str(exc).split("\n")[0]}
            entry["elapsed_ms"] = round((perf_counter()-start)*1000, 2)
            results["copy_metric_checks"].append(entry)
        results["foreign_keys"] = {"source": sum(map(len, query.foreign_keys(PROJECT_LIVE).values())),
                                    "copy": sum(map(len, query.foreign_keys(PROJECT_COPY).values()))}
        results["column_types"] = {"source_transactions": query.table_columns(PROJECT_LIVE, "transactions"),
                                    "copy_transactions": query.table_columns(PROJECT_COPY, "transactions")}
        write_json(output / "results.json", results)
        if provider != "none":
            for case in test_cases:
                if case["id"] not in ai_ids:
                    continue
                context = retrieval.build_context(PROJECT_LIVE, case["question"])
                present = [table for table in TABLES if f"테이블 {table}." in context]
                start = perf_counter()
                print("AI", case["id"], flush=True)
                try:
                    response = await client.post("/api/chat", json={"project": PROJECT_LIVE, "message": case["question"], "provider": provider})
                    response.raise_for_status()
                    body = response.json()
                    answer = body.get("answer") or body.get("result") or {}
                    got = answer.get("rows", [])
                    entry = {"id": case["id"], "question": case["question"], "pass": bool(answer) and normalized(got, case["expected"]) == normalized(case["expected"]),
                             "expected": case["expected"], "actual": got, "sql": answer.get("sql"), "reply": body.get("reply"),
                             "error": body.get("answer_error") or body.get("result_error"), "clarification": body.get("clarify")}
                except Exception as exc:
                    entry = {"id": case["id"], "pass": False, "error": str(exc)}
                entry.update({"elapsed_ms": round((perf_counter()-start)*1000, 2), "context_chars": len(context), "selected_tables": present,
                              "required_tables_present": set(case["required_tables"]) <= set(present)})
                results["ai_checks"].append(entry)
                write_json(output / "results.json", results)
                print("AI_RESULT", case["id"], entry["pass"], flush=True)
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pg-bin", type=Path, default=Path("C:/Program Files/PostgreSQL/18/bin"))
    parser.add_argument("--port", type=int, default=55432)
    parser.add_argument("--listings", type=int, default=1000)
    parser.add_argument("--provider", choices=["none", "codex_cli", "claude_cli"], default="codex_cli")
    parser.add_argument("--ai-cases", default="completed_count,completed_amount,september_count,regional_amount,no_reviews,unconverted_listings,category_average,monthly_amount")
    parser.add_argument("--output", type=Path, default=ROOT / ".local" / "reports" / "marketplace")
    args = parser.parse_args()
    print("PREPARING_LOCAL_CLUSTER", flush=True)
    cfg = start_cluster(args.pg_bin, args.port)
    print("CLUSTER_READY", flush=True)
    rows = generate(args.listings)
    validate(rows)
    source = engine_for(cfg, SOURCE_DB)
    counts = seed_database(source, rows)
    source.dispose()
    print("SEEDED", sum(counts.values()), "rows", counts, flush=True)
    os.environ.update(POSTGRES_HOST="127.0.0.1", POSTGRES_PORT=str(cfg["port"]), POSTGRES_USER=OWNER,
                      POSTGRES_PASSWORD=cfg["owner_password"], POSTGRES_DB=APP_DB, EMBED_PROVIDER="none")
    # The launcher lets the UI connect to this isolated environment later.
    env_path = LOCAL / "app.env"
    env_path.write_text("\n".join(f"{k}={os.environ[k]}" for k in ["POSTGRES_HOST","POSTGRES_PORT","POSTGRES_USER","POSTGRES_PASSWORD","POSTGRES_DB","EMBED_PROVIDER"]) + f"\nTEXT2SQL_PROVIDER={args.provider}\n", encoding="utf-8")
    results = asyncio.run(evaluate(rows, cfg, args.provider, set(args.ai_cases.split(",")), args.output))
    write_json(args.output / "results.json", results)
    print("RESULTS", args.output / "results.json", flush=True)
    for key in ["sql_checks", "api_metric_checks", "copy_metric_checks", "ai_checks"]:
        print(key, sum(r["pass"] for r in results[key]), "/", len(results[key]), flush=True)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
