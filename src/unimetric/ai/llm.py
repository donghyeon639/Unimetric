"""자연어 -> SQL. provider 교체식.

provider를 교체식으로 둔 이유:
- 팀마다 쓸 수 있는 LLM이 다르다(클라우드 OpenAI/Anthropic vs 로컬 Ollama vs 없음).
  provider는 환경변수로 고르고, 나머지 코드는 특정 벤더를 하드코딩하지 않는다.
- 기본값은 `none`: LLM 없이도 플랫폼은 완전히 동작한다(지표가 결정적인 주 경로).
  자연어 질의는 선택적 부가 기능일 뿐이다.
- LLM이 돌려준 SQL은 신뢰하지 않는다: 호출부가 engine.run_select로 다시 검증해
  읽기 전용 SELECT/CTE만 통과시킨다. 시스템 프롬프트도 SELECT만 지시하지만,
  실제 강제는 실행 시점에 한다.
"""

import os

import httpx

from .prompts import SQL_SYSTEM, METRIC_SYSTEM, CHAT_SYSTEM, SUMMARIZE_SYSTEM
from .cli_providers import ask_claude, ask_codex, cli_command

PROVIDERS = {
    "none": "AI 사용 안 함",
    "claude_cli": "Claude CLI",
    "codex_cli": "Codex CLI",
    "openai": "OpenAI API",
    "anthropic": "Anthropic API",
    "ollama": "Ollama",
}


def _schema_text(schema: dict[str, list[dict]]) -> str:
    """스키마 사전을 프롬프트에 넣을 한 줄짜리 텍스트들로 변환한다."""
    lines = []
    for table, cols in schema.items():
        col_str = ", ".join(f"{c['column']} {c['type']}" for c in cols)
        lines.append(f"{table}({col_str})")
    return "\n".join(lines)


def _strip_fences(sql: str) -> str:
    """모델이 붙였을 수 있는 ```sql ... ``` 코드펜스를 제거한다."""
    sql = sql.strip()
    if sql.startswith("```"):
        sql = sql.split("\n", 1)[-1] if "\n" in sql else sql
        sql = sql.rsplit("```", 1)[0]
    return sql.strip()


def _ask_openai(system: str, prompt: str) -> str:
    """OpenAI(호환 엔드포인트 포함)에 질의해 생성 텍스트를 반환한다."""
    key = os.environ["OPENAI_API_KEY"]
    base = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    resp = httpx.post(
        f"{base}/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0,
        },
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


def _ask_anthropic(system: str, prompt: str) -> str:
    """Anthropic(Claude)에 질의해 생성 텍스트를 반환한다."""
    key = os.environ["ANTHROPIC_API_KEY"]
    model = os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")
    resp = httpx.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": model,
            "max_tokens": 800,
            "system": system,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()["content"][0]["text"]


def _ask_ollama(system: str, prompt: str) -> str:
    """로컬 Ollama에 질의해 생성 텍스트를 반환한다(키 불필요)."""
    base = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    model = os.getenv("OLLAMA_MODEL", "llama3.1")
    resp = httpx.post(
        f"{base}/api/chat",
        json={
            "model": model,
            "stream": False,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
        },
        timeout=120,
    )
    resp.raise_for_status()
    return resp.json()["message"]["content"]


def _ask_claude_cli(system: str, prompt: str) -> str:
    """기존 Claude 선택값을 유지하는 전용 어댑터."""
    return ask_claude(system, prompt)


def _ask_codex_cli(system: str, prompt: str) -> str:
    """Codex 전용 어댑터. SQL·지표·채팅·요약이 같은 경로를 공유한다."""
    return ask_codex(system, prompt)


def provider_name() -> str:
    """현재 설정된 LLM provider 이름을 반환한다."""
    return os.getenv("TEXT2SQL_PROVIDER", "none").strip().lower()


def is_configured() -> bool:
    """LLM provider가 설정되어 있는지(none이 아닌지) 반환한다."""
    return provider_name() in PROVIDERS and provider_name() != "none"


def provider_options() -> list[dict]:
    """Availability is installation/config detection, not a paid inference probe."""
    options = []
    for name, label in PROVIDERS.items():
        available = True
        if name in ("codex_cli", "claude_cli"):
            available = cli_command(name) is not None
        elif name in ("openai", "anthropic"):
            available = bool(os.getenv("OPENAI_API_KEY" if name == "openai" else "ANTHROPIC_API_KEY"))
        options.append({"id": name, "label": label, "available": available})
    return options


def _complete(system: str, prompt: str, provider: str | None = None) -> str:
    """provider 스위치. (system, prompt)를 받아 생성 텍스트를 반환한다.

    SQL 생성과 지표 자연어 생성이 이 한 곳을 공유한다.
    """
    provider = provider if provider is not None else provider_name()
    if provider == "openai":
        return _ask_openai(system, prompt)
    if provider == "anthropic":
        return _ask_anthropic(system, prompt)
    if provider == "ollama":
        return _ask_ollama(system, prompt)
    if provider == "claude_cli":
        return _ask_claude_cli(system, prompt)
    if provider == "codex_cli":
        return _ask_codex_cli(system, prompt)
    # 미설정: 친절히 안내하고 RuntimeError로 막는다(지표 탭은 그대로 사용 가능).
    raise RuntimeError(
        "LLM이 설정되지 않았습니다. .env의 TEXT2SQL_PROVIDER를 "
        "openai/anthropic/ollama/claude_cli/codex_cli 중 하나로 지정하거나 화면에서 선택하세요. 그 전까지는 "
        "'지표 빌더'로 직접 지표를 만들 수 있습니다."
    )


def question_to_sql(question: str, schema: dict[str, list[dict]], provider: str | None = None) -> str:
    """스키마와 질문으로 프롬프트를 구성해 provider별로 SQL을 생성·반환한다."""
    prompt = (
        f"Schema:\n{_schema_text(schema)}\n\n"
        f"Question: {question}\n\n"
        "Return only the SQL."
    )
    return _strip_fences(_complete(SQL_SYSTEM, prompt, provider))


# 지표 명세(JSON) 생성용 시스템 프롬프트.


def question_to_metric(question: str, table: str, columns: list[dict], provider: str | None = None) -> str:
    """질문 + 테이블 컬럼으로 지표 명세 JSON 텍스트를 생성해 반환한다."""
    col_text = ", ".join(f"{c['column']} {c['type']}" for c in columns)
    prompt = (
        f"Table: {table}\nColumns: {col_text}\n\n"
        f"User request: {question}\n\nReturn only the JSON metric spec."
    )
    return _strip_fences(_complete(METRIC_SYSTEM, prompt, provider))


# 대화형 지표 어시스턴트 시스템 프롬프트(action 기반).


def chat(messages: list[dict], context: str, summary: str = "", provider: str | None = None) -> str:
    """RAG 컨텍스트 + (오래된 대화)요약 + 최근 대화로 어시스턴트 답변을 생성한다."""
    transcript = "\n".join(f"{m.get('role')}: {m.get('content')}" for m in messages)
    parts = [f"CONTEXT — 질문에 관련된 지표/테이블:\n{context}"]
    if summary:
        parts.append(f"EARLIER CONVERSATION SUMMARY(이전 대화 요약):\n{summary}")
    parts.append(f"RECENT CONVERSATION(최근 대화):\n{transcript}\n\nassistant:")
    return _complete(CHAT_SYSTEM, "\n\n".join(parts), provider)


def summarize(prev_summary: str, messages: list[dict], provider: str | None = None) -> str:
    """기존 요약 + 새로 접을 메시지들을 합쳐 갱신된 요약을 만든다(컨텍스트 압축)."""
    transcript = "\n".join(f"{m.get('role')}: {m.get('content')}" for m in messages)
    prompt = (
        f"기존 요약:\n{prev_summary or '(없음)'}\n\n"
        f"새로 접을 대화:\n{transcript}\n\n위를 합쳐 갱신된 요약을 한국어로 작성:"
    )
    return _complete(SUMMARIZE_SYSTEM, prompt, provider)
