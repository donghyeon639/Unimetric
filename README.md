# Unimetric

공통 지표를 정의하고 파일·DB 데이터를 자연어로 조회하는 Python/FastAPI 프로젝트입니다.

## 현재 실행 조건

- Python 3.10 이상
- PostgreSQL. 벡터 검색을 사용하려면 pgvector 확장이 필요합니다.
- AI를 사용할 경우 선택한 CLI의 설치·로그인 또는 API 설정

실행 코드는 `src/unimetric`에 모아 두었습니다. 실행과 연결되지 않은 Java/Gradle 초기 코드는 제거했습니다.

## 설치 및 실행 — PowerShell

프로젝트 폴더에서 실행합니다.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
Copy-Item .env.example .env
```

이미 .env가 있다면 복사 명령으로 덮어쓰지 말고 기존 파일을 편집하세요. .env의 POSTGRES_PASSWORD를 정하고 DB 연결정보를 확인합니다.

Docker Desktop의 Linux 엔진을 시작한 후 로컬 PostgreSQL을 준비할 수 있습니다. 기존 PostgreSQL을 사용한다면 이 단계는 생략합니다.

```powershell
docker compose up -d postgres
.\.venv\Scripts\python.exe run.py serve --provider codex_cli
```

브라우저에서 http://127.0.0.1:8000 을 엽니다. 의존성이 이미 설치된 환경에서는 `python run.py serve`로도 실행할 수 있습니다. 패키지 설치 후에는 `python -m unimetric serve` 또는 `unimetric serve`도 지원합니다.

첫 실행 시 프로젝트와 대화 기록용 테이블이 자동 생성됩니다. .env 변경은 서버를 재시작해야 적용됩니다. Docker 볼륨을 처음 만들 때 정한 DB 비밀번호는 .env만 바꿔도 기존 DB에 자동 반영되지 않습니다.

## Claude CLI와 Codex CLI 선택

서버를 실행하는 OS 계정으로 먼저 로그인합니다.

```powershell
# Codex
npm install -g @openai/codex
codex login
codex login status

# Claude Code가 설치되어 있다면
claude auth login
claude auth status
```

기본 제공자는 실행 옵션 또는 .env로 지정합니다. 실행 옵션이 .env보다 우선합니다.

```powershell
python run.py serve --provider codex_cli
python run.py serve --provider claude_cli
```

```dotenv
TEXT2SQL_PROVIDER=codex_cli
CODEX_CLI_TIMEOUT=180
# 선택: 사용할 수 있는 정확한 모델 ID. 비워두면 CLI 기본 모델 사용.
CODEX_MODEL=
```

상단 **AI 선택 메뉴**에서 Claude CLI·Codex CLI 등을 선택하면 다음 질문부터 적용됩니다. 선택은 해당 브라우저에 저장되며, 서버 전체 환경설정을 바꾸지 않습니다. 다른 사용자의 선택과 섞이지 않도록 각 API 요청에 제공자를 전달합니다. 메뉴의 설치·설정 감지는 로그인 유효성이나 사용량까지 확인하지 않습니다.

API에서도 `/api/chat`, `/api/ask`, `/api/builder/ai` 요청에 `"provider": "codex_cli"` 또는 `"provider": "claude_cli"`를 보낼 수 있습니다. 생략하면 서버 기본값을 따릅니다. SQL 생성·지표 생성·채팅·대화 요약에 동일한 선택이 적용됩니다.

CLI가 PATH에 없으면 .env에 실행 파일의 경로만 지정합니다. 명령 인자를 함께 넣지 않습니다.

```dotenv
# CODEX_CLI_BIN=C:/path/to/codex.exe
# CLAUDE_CLI_BIN=C:/path/to/claude.cmd
```

Codex 어댑터는 `codex exec`에 표준입력으로 프롬프트를 전달하고 `--output-last-message` 파일에서 최종 응답만 읽습니다. 임시 폴더, 읽기 전용 샌드박스, 명령 실행 도구 비활성화, 사용자 설정 미로딩, 세션 비저장 옵션을 사용합니다. 이 플래그를 지원하는 Codex CLI가 필요하며 개발 환경에서는 0.148.0의 도움말로 지원을 확인했습니다.

Claude 어댑터도 표준입력을 사용하고 도구·MCP·사용자 설정·사용자 훅·세션 저장을 제한합니다. 기존 `--dangerously-skip-permissions` 옵션과 실행 시 자동 npx 다운로드를 제거했습니다. CLI를 직접 설치해야 합니다.

CLI 제공자는 서버 계정의 로그인·사용량을 공유하는 **로컬 개발용 연동**입니다. 실제 인증정보를 저장소에 복사하지 마세요. 여러 사용자가 사용하는 운영 서비스에는 별도 인증·권한과 계정별 호출 정책이 필요합니다.

Codex/Claude 선택은 텍스트 생성 경로를 바꿉니다. 임베딩 모델은 별도 설정입니다. `EMBED_PROVIDER=none`이면 기존 어휘 검색으로 동작합니다.

공식 참고: [Codex 비대화형 실행](https://developers.openai.com/codex/noninteractive), [CLI 옵션](https://developers.openai.com/codex/cli/reference), [Codex 인증](https://developers.openai.com/codex/auth).

## 실행 상태 확인

- `/api/health`: 웹 프로세스의 생존 상태
- `/api/ready`: DB 접속과 프로젝트 메타테이블 준비 상태. 미준비 시 HTTP 503
- `/api/llm-status`: 기본 제공자 및 선택 가능한 제공자·설치 감지 상태

DB가 꺼져 있어도 웹 화면과 AI 제공자 정보는 열릴 수 있습니다. 프로젝트 생성·조회·업로드·채팅은 DB가 준비되어야 동작합니다. DB 시작 후 서버를 다시 실행해 초기화를 수행하세요.

## 검증

```powershell
python -m pytest -q
```

테스트는 CLI 호출 인자·표준입력·최종 답변 처리·오류·타임아웃·제공자별 요청 전달·동시 요청 분리를 검사합니다. 실제 모델 호출은 포함하지 않아 테스트가 계정 사용량을 소모하지 않습니다.

## 코드 구조

```text
src/unimetric/
  api/              HTTP 라우트·요청 형식·정적 파일 제공
  ai/
    rag/catalog.py  테이블·지표 설명 생성
    rag/search.py   어휘·벡터·키워드 검색
    rag/index.py    임베딩 저장·백그라운드 인덱싱
    rag/context.py  검색 결과로 AI 컨텍스트 조립
    retrieval.py    RAG 공개 진입점
    prompts.py      모델 지시문
    llm.py          제공자 선택·모델 요청
    cli_providers.py  Codex·Claude CLI 실행
  data/             파일 적재·외부 DB·조회·ETL
  semantic/         지표 정의·SQL 컴파일·빌더
  workspace/        프로젝트와 지표 카탈로그 저장
  core/             DB 초기화·로그
  web/
    index.html      화면 구조
    assets/app.css  공통 스타일
    assets/js/      화면·채팅·결과 표시·공유 상태 ES 모듈
tools/              개발용 감사·가상 데이터·실제 DB 검증 명령
tests/              유니매트릭스 단위·API 회귀 테스트
```

화면 모듈은 `state.js`의 공유 상태와 이벤트로 연결합니다. API는 `/assets` 아래의 CSS·JavaScript를 제공하며, 설치 패키지에도 이 파일들을 포함합니다. 기능을 수정할 때는 해당 화면 모듈 또는 서버 계층을 변경합니다.

## 동네마켓 테스트 환경

PC에 PostgreSQL이 설치되어 있다면 7개 테이블·5,722건의 가상 데이터로 실제 DB와 지표 API를 검증할 수 있습니다. 기본 실행기는 Windows PostgreSQL 18 설치 경로를 사용하며 `--pg-bin`으로 변경할 수 있습니다.

```powershell
# 모델 호출 없이 DB 준비·SQL·지표·복사 적재 검증
python -m tools.marketplace.run --provider none

# 기존 테스트 DB로 웹 실행
python run.py serve --env-file .local/dongne-market/app.env

# 같은 검증에 Codex 자연어 질문 포함: 모델 사용량 발생
python -m tools.marketplace.run --provider codex_cli

# 실행 중인 서버에 한국어 지역별 질문 검증: 모델 사용량 발생
python -m tools.marketplace.check_regional --base-url http://127.0.0.1:8000

# 원본 거래로 판매자 순위 교차 검증: 모델 호출 없음
python -m tools.marketplace.verify_sellers

# DB·모델 호출 없이 기존 파이프라인의 한계 재현
python -m tools.audit_data_pipeline --output .local/reports/pipeline-audit.json
```

원본 데이터는 같은 생성 시드·크기일 때만 재사용하며 다른 원본을 덮어쓰지 않습니다. 복사 프로젝트는 테스트 전용이며 재실행 시 다시 적재합니다. 알려진 복사 적재의 금액 타입·외래키 손실은 이 구조 정리에서 변경하지 않았습니다.

DB 상태·접속 설정은 `.local/dongne-market`, 자동 생성한 검증 결과는 `.local/reports`에 저장되고 Git에서 제외됩니다. 검증 도구는 Markdown 보고서를 만들지 않습니다. 설명용 검토·일일 기록은 [노션 프로젝트 문서](https://app.notion.com/p/3fa7fb739314812aa306f1c10c6be68f)에 보관합니다. 비밀번호나 접속 설정을 노션에 복사하지 않습니다.
