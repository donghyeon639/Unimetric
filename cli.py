"""커맨드라인 진입점: serve | compile | ingest | project.

API 외에 CLI도 두는 이유:
- `compile`은 서버 없이도 지표의 정확한 SQL을 확인하게 해준다(정의 디버깅·검수용).
- `serve`/`ingest`는 API와 같은 동작을 스크립트로 돌릴 수 있게 한다.
"""

import argparse
import sys


def _cmd_serve(args: argparse.Namespace) -> int:
    """API 서버를 실행한다(uvicorn)."""
    import uvicorn

    uvicorn.run("unimetric.api:app", host=args.host, port=args.port, reload=args.reload)
    return 0


def _cmd_compile(args: argparse.Namespace) -> int:
    """지표를 SQL로 컴파일해 출력한다(DB 불필요)."""
    from .semantic import model as metrics

    model = metrics.load_model_file(args.file)
    print(model.compile(args.metric, group_by=args.group_by or []))
    return 0


def _cmd_ingest(args: argparse.Namespace) -> int:
    """파일을 프로젝트에 적재하고 적재된 테이블/행수를 출력한다."""
    from .data import ingest

    loaded = ingest.ingest_file(args.path, args.project)
    for item in loaded:
        print(f"{item['table']}: {item['rows']} rows")
    return 0


def _cmd_project(args: argparse.Namespace) -> int:
    """프로젝트를 생성/목록/삭제한다."""
    from .workspace import projects

    if args.action == "create":
        p = projects.create_project(args.key, args.name)
        print(f"created project '{p['key']}' (schema {p['schema_name']})")
    elif args.action == "list":
        for p in projects.list_projects():
            print(f"{p['key']}\t{p['name']}\t{p['schema_name']}")
    elif args.action == "delete":
        projects.delete_project(args.key)
        print(f"deleted project '{args.key}'")
    return 0


def main(argv: list[str] | None = None) -> int:
    """인자를 파싱해 해당 하위 명령을 실행한다."""
    parser = argparse.ArgumentParser(prog="unimetric")
    sub = parser.add_subparsers(dest="command", required=True)

    p_serve = sub.add_parser("serve", help="API 서버 실행")
    p_serve.add_argument("--host", default="0.0.0.0")
    p_serve.add_argument("--port", type=int, default=8000)
    p_serve.add_argument("--reload", action="store_true")
    p_serve.set_defaults(func=_cmd_serve)

    p_compile = sub.add_parser("compile", help="지표를 SQL로 컴파일")
    p_compile.add_argument("metric")
    p_compile.add_argument("-f", "--file", required=True, help="지표 정의 YAML 경로")
    p_compile.add_argument("-g", "--group-by", nargs="*", dest="group_by")
    p_compile.set_defaults(func=_cmd_compile)

    p_ingest = sub.add_parser("ingest", help="파일을 프로젝트에 적재")
    p_ingest.add_argument("path")
    p_ingest.add_argument("-p", "--project", required=True)
    p_ingest.set_defaults(func=_cmd_ingest)

    p_proj = sub.add_parser("project", help="프로젝트 관리")
    p_proj.add_argument("action", choices=["create", "list", "delete"])
    p_proj.add_argument("key", nargs="?")
    p_proj.add_argument("-n", "--name")
    p_proj.set_defaults(func=_cmd_project)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
