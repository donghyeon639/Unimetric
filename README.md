# Unimetric

에픽카에서 인턴으로 일하며 비개발 팀의 데이터 요청을 받았습니다. 필요한 데이터와 집계 기준을 서로 확인해야 했고, 비개발자가 필요한 데이터를 직접 조회하기도 어려웠습니다.

그래서 비개발자도 사내 데이터베이스에 자연어로 질문하고, 원하는 결과를 꺼내 공통 지표로 저장할 수 있으면 좋겠다고 생각했습니다. **Unimetric**은 이 문제에서 시작한 프로젝트입니다. 질문을 SQL로 바꿔 조회 결과와 실행한 SQL을 함께 보여주고, 저장한 지표는 같은 계산 기준으로 다시 조회할 수 있습니다.

## 공통 준비

Python 3.10 이상과 PostgreSQL이 필요합니다. 프로젝트 폴더에서 아래 명령을 실행합니다.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

`.env`에 PostgreSQL 접속 정보를 입력합니다. Docker로 새 PostgreSQL을 띄우는 경우에는 `POSTGRES_PASSWORD`를 정한 뒤 `docker compose up -d postgres`를 실행합니다.

## CLI 방식

서버를 실행하는 계정에서 Codex CLI 또는 Claude Code를 설치하고 로그인합니다. 사용할 쪽의 명령만 실행합니다.

```powershell
# Codex CLI
codex login
.\.venv\Scripts\python.exe run.py serve --provider codex_cli

# Claude CLI
claude auth login
.\.venv\Scripts\python.exe run.py serve --provider claude_cli
```

[웹 화면](http://127.0.0.1:8000)에서 프로젝트를 만들고 데이터 소스를 연결한 뒤 **AI 채팅**에서 질문합니다. 화면 상단에서 AI 제공자를 바꿀 수 있습니다.

## API 방식

모델 API를 사용할 경우 `.env`에 제공자와 API 키를 설정합니다. 아래는 OpenAI API를 쓰는 예시입니다.

```dotenv
TEXT2SQL_PROVIDER=openai
OPENAI_API_KEY=your_api_key
```

Anthropic을 사용하려면 `TEXT2SQL_PROVIDER=anthropic`과 `ANTHROPIC_API_KEY`를 설정합니다. 키는 서버의 `.env`에 보관합니다.

```powershell
.\.venv\Scripts\python.exe run.py serve
```

웹 화면에서 질문하거나, 다른 프로그램에서 Unimetric의 HTTP API를 호출할 수 있습니다. 아래 예시의 `my_project`를 데이터가 연결된 프로젝트 키로 바꿉니다.

```powershell
$body = @{
    project = 'my_project'
    message = '이번 달 완료 거래 건수와 거래금액 합계를 알려줘.'
} | ConvertTo-Json

Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8000/api/chat' `
    -ContentType 'application/json; charset=utf-8' -Body $body
```

`POST /api/chat` 응답에서 실행 SQL과 결과 행을 확인할 수 있습니다. 이어서 질문할 때는 응답의 `session_id`를 다음 요청에 포함합니다. HTTP API는 CLI 제공자로 서버를 실행한 경우에도 사용할 수 있습니다. [API 명세](http://127.0.0.1:8000/docs)
