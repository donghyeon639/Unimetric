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
import subprocess
import tempfile

import httpx

_IS_WIN = os.name == "nt"

_SYSTEM = (
    "You translate natural-language questions into a SINGLE read-only SQL "
    "query for PostgreSQL. Rules: output ONLY the SQL, no prose, no markdown "
    "fences. Use only SELECT (or WITH ... SELECT). Never write INSERT/UPDATE/"
    "DELETE/DDL. Use only the tables and columns provided in the schema."
)


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
    """로컬에 설치된 Claude CLI(npx)를 호출해 생성된 텍스트를 반환한다.

    설치 경로에 의존하지 않도록 `npx -y @anthropic-ai/claude-code`로 실행하고,
    프롬프트는 인자 길이 제한·따옴표 문제를 피하려 임시 파일 + stdin 리다이렉션
    으로 전달한다. 로컬 테스트 전용이며, 추후 사용자 키/로컬 LLM provider로
    교체 가능하다.
    """
    # system 프롬프트를 합쳐 전달한다(CLI에는 별도 system 인자가 없으므로).
    full = f"{system}\n\n{prompt}"
    with tempfile.NamedTemporaryFile(
        mode="w+", suffix=".txt", delete=False, encoding="utf-8"
    ) as tf:
        tf.write(full)
        temp_path = tf.name
    try:
        npx_bin = "npx.cmd" if _IS_WIN else "npx"
        cmd = (
            f"{npx_bin} -y @anthropic-ai/claude-code --print"
            f" --output-format text --dangerously-skip-permissions"
            f' < "{temp_path}"'
        )
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            shell=True,
            timeout=int(os.getenv("CLAUDE_CLI_TIMEOUT", "180")),
        )
        if result.returncode != 0:
            raise RuntimeError(f"Claude CLI 실행 실패: {result.stderr[:500]}")
        return result.stdout
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)


def provider_name() -> str:
    """현재 설정된 LLM provider 이름을 반환한다."""
    return os.getenv("TEXT2SQL_PROVIDER", "none").lower()


def is_configured() -> bool:
    """LLM provider가 설정되어 있는지(none이 아닌지) 반환한다."""
    return provider_name() != "none"


def _complete(system: str, prompt: str) -> str:
    """provider 스위치. (system, prompt)를 받아 생성 텍스트를 반환한다.

    SQL 생성과 지표 자연어 생성이 이 한 곳을 공유한다.
    """
    provider = provider_name()
    if provider == "openai":
        return _ask_openai(system, prompt)
    if provider == "anthropic":
        return _ask_anthropic(system, prompt)
    if provider == "ollama":
        return _ask_ollama(system, prompt)
    if provider == "claude_cli":
        return _ask_claude_cli(system, prompt)
    # 미설정: 친절히 안내하고 RuntimeError로 막는다(지표 탭은 그대로 사용 가능).
    raise RuntimeError(
        "LLM이 설정되지 않았습니다. .env의 TEXT2SQL_PROVIDER를 "
        "openai/anthropic/ollama/claude_cli 중 하나로 지정하세요. 그 전까지는 "
        "'지표 빌더'로 직접 지표를 만들 수 있습니다."
    )


def question_to_sql(question: str, schema: dict[str, list[dict]]) -> str:
    """스키마와 질문으로 프롬프트를 구성해 provider별로 SQL을 생성·반환한다."""
    prompt = (
        f"Schema:\n{_schema_text(schema)}\n\n"
        f"Question: {question}\n\n"
        "Return only the SQL."
    )
    return _strip_fences(_complete(_SYSTEM, prompt))


# 지표 명세(JSON) 생성용 시스템 프롬프트.
_METRIC_SYSTEM = (
    "You design a single analytics metric for a given table and output ONLY a "
    "JSON object (no prose, no markdown fences). The JSON schema is:\n"
    '{"name": str (snake_case identifier), "description": str, '
    '"type": "simple"|"ratio", '
    '"measure": {"agg": one of sum|count|count_distinct|avg|min|max, "expr": column}, '
    '"numerator": {...}, "denominator": {...}, '
    '"filter": "<SQL WHERE condition>", '
    '"dimensions": [column, ...]}\n'
    "For type 'simple' include 'measure'. For type 'ratio' include 'numerator' "
    "and 'denominator' instead. 'expr' may be '*' only with agg 'count'. Use ONLY "
    "columns that exist in the provided schema. 'dimensions' are columns to allow "
    "grouping by (low-cardinality/date columns). When the request restricts rows "
    "(e.g. 'only surname Lee', 'status DONE', 'amount>0'), put that condition in "
    "'filter' as a raw SQL WHERE clause referencing real columns "
    "(e.g. \"name LIKE '이%'\" or \"feedback_status = 'DONE'\"); omit 'filter' otherwise."
)


def question_to_metric(question: str, table: str, columns: list[dict]) -> str:
    """질문 + 테이블 컬럼으로 지표 명세 JSON 텍스트를 생성해 반환한다."""
    col_text = ", ".join(f"{c['column']} {c['type']}" for c in columns)
    prompt = (
        f"Table: {table}\nColumns: {col_text}\n\n"
        f"User request: {question}\n\nReturn only the JSON metric spec."
    )
    return _strip_fences(_complete(_METRIC_SYSTEM, prompt))


# 대화형 지표 어시스턴트 시스템 프롬프트(action 기반).
_CHAT_SYSTEM = (
    "You are Unimetric's data/metric assistant for a 단체(organization). Reply in KOREAN, "
    "concise. You are given RAG CONTEXT: relevant existing metrics and relevant tables "
    "(columns, FK relations, sample rows). Use ONLY tables/columns that appear in the context.\n"
    "End your reply with EXACTLY ONE fenced ```json block whose object has an \"action\":\n"
    "1) AMBIGUOUS or missing key info (기간/대상/집계단위/상태 등) → ask with choices:\n"
    '   {"action":"clarify","question":"무엇을 기준으로 할까요?","options":["최근 7일","최근 30일","전체 기간"]}\n'
    "2) A reusable SINGLE-TABLE metric fits → propose:\n"
    '   {"action":"metric","spec":{"name":snake_case,"description":str,"table":str,'
    '"type":"simple"|"ratio","measure":{"agg":sum|count|count_distinct|avg|min|max,"expr":col_or_*},'
    '"numerator":{...},"denominator":{...},"filter":"<SQL WHERE>","dimensions":[col,...]}}\n'
    "3) Answering needs a JOIN or one-off analytical query → give SQL:\n"
    '   {"action":"sql","sql":"SELECT ... (read-only, joins allowed, real tables/cols)"}\n'
    "   SQL은 PostgreSQL. 식별자(테이블/컬럼)에 대문자가 있으면 반드시 큰따옴표로 감싼다 "
    "(Postgres는 따옴표 없는 이름을 소문자로 바꿈). 예: FROM \"ServiceEstimate\", "
    "컬럼도 \"OwnPart\".code 처럼. 컨텍스트에 적힌 정확한 대소문자를 그대로 사용.\n"
    "Rules: PREFER reusing a relevant existing metric (cite it) over inventing. Put row "
    "conditions in filter/WHERE (e.g. \"status = 'SELL'\", \"name LIKE '이%'\"). Use FK relations "
    "from context for joins. If the user already gave enough info, DON'T ask — act. "
    "Use the EARLIER SUMMARY + RECENT conversation together: follow-ups may refer to a "
    "previously proposed metric/SQL/result (e.g. '그걸 월별로', '거기서 SELL만'). "
    "Write a short Korean explanation BEFORE the json block."
)


def chat(messages: list[dict], context: str, summary: str = "") -> str:
    """RAG 컨텍스트 + (오래된 대화)요약 + 최근 대화로 어시스턴트 답변을 생성한다."""
    transcript = "\n".join(f"{m.get('role')}: {m.get('content')}" for m in messages)
    parts = [f"CONTEXT — 질문에 관련된 지표/테이블:\n{context}"]
    if summary:
        parts.append(f"EARLIER CONVERSATION SUMMARY(이전 대화 요약):\n{summary}")
    parts.append(f"RECENT CONVERSATION(최근 대화):\n{transcript}\n\nassistant:")
    return _complete(_CHAT_SYSTEM, "\n\n".join(parts))


_SUMMARIZE_SYSTEM = (
    "You compress a chat between a user and a metric assistant into concise KOREAN notes. "
    "Capture: the user's goals/questions, proposed metrics (name + definition), executed SQL "
    "and key results, decisions, and any unresolved clarifications. Information-dense, short. "
    "Output ONLY the updated summary text."
)


def summarize(prev_summary: str, messages: list[dict]) -> str:
    """기존 요약 + 새로 접을 메시지들을 합쳐 갱신된 요약을 만든다(컨텍스트 압축)."""
    transcript = "\n".join(f"{m.get('role')}: {m.get('content')}" for m in messages)
    prompt = (
        f"기존 요약:\n{prev_summary or '(없음)'}\n\n"
        f"새로 접을 대화:\n{transcript}\n\n위를 합쳐 갱신된 요약을 한국어로 작성:"
    )
    return _complete(_SUMMARIZE_SYSTEM, prompt)
