"""테이블 컬럼 프로파일링 — 비개발자용 지표 빌더의 '자동 추천' 근거.

설계 의도:
- 사용자가 빈 화면을 마주하지 않도록, 테이블을 고르면 각 컬럼을 분석해
  "이건 차원감(group by), 이건 측정값감(합산/개수)"을 추천한다.
- 추천은 결정적 규칙(타입 + 고유값 수 + 컬럼명 패턴)으로 만든다. LLM 불필요.
"""

import re

from . import query as engine

# 숫자/날짜 타입 판별용 키워드(Postgres·SQLite 타입명 모두 커버).
_NUMERIC_HINTS = ("int", "numeric", "real", "double", "float", "dec", "money")
_DATE_HINTS = ("date", "time", "timestamp")


def _is_numeric(dtype: str) -> bool:
    """타입 문자열이 숫자형인지 판별한다."""
    d = (dtype or "").lower()
    return any(h in d for h in _NUMERIC_HINTS)


def _is_date(dtype: str) -> bool:
    """타입 문자열이 날짜/시간형인지 판별한다."""
    d = (dtype or "").lower()
    return any(h in d for h in _DATE_HINTS)


def _looks_like_id(col: str) -> bool:
    """컬럼명이 식별자(id) 같은지 판별한다(id 또는 *_id)."""
    c = col.lower()
    return c == "id" or c.endswith("_id")


def _classify(col: str, dtype: str, distinct: int, total: int) -> tuple[str, list[str]]:
    """컬럼의 역할과 추천 집계를 정한다 -> (role, suggested_aggs).

    role: 'measure'(수치 집계) | 'dimension'(쪼개기 축) | 'id'(식별자) | 'text'.
    """
    if _is_numeric(dtype) and not _looks_like_id(col):
        return "measure", ["sum", "avg", "min", "max"]
    if _looks_like_id(col):
        # 식별자는 보통 개수/고유개수 측정값. 고유값이 적으면 차원으로도 쓸 만하다.
        return "id", ["count", "count_distinct"]
    if _is_date(dtype):
        return "dimension", ["count"]
    # 낮은 카디널리티 텍스트 = 좋은 차원(상태/카테고리 등).
    if total and distinct and distinct <= 50 and distinct < total:
        return "dimension", ["count", "count_distinct"]
    return "text", ["count", "count_distinct"]


def profile_table(project: str, table: str) -> dict:
    """테이블의 각 컬럼을 프로파일링하고 차원/측정값 추천을 반환한다."""
    schema = engine.get_schema(project)
    if table not in schema:
        raise KeyError(f"테이블 '{table}'을(를) 찾을 수 없습니다")
    cols = schema[table]

    # 한 번의 쿼리로 전체 행수 + 컬럼별 (고유값 수, 비결측 수)를 구한다.
    parts = ["COUNT(*) AS _total"]
    for c in cols:
        name = c["column"]
        # 식별자는 검증된 컬럼명이지만 예약어 대비 따옴표로 감싼다.
        parts.append(f'COUNT(DISTINCT "{name}") AS "d_{name}"')
        parts.append(f'COUNT("{name}") AS "n_{name}"')
    sql = f'SELECT {", ".join(parts)} FROM "{table}"'
    row = engine.run_select(sql, project, limit=1)[0]
    total = int(row["_total"])

    columns = []
    dim_suggest: list[str] = []
    measure_suggest: list[dict] = []
    for c in cols:
        name = c["column"]
        dtype = c["type"]
        distinct = int(row.get(f"d_{name}") or 0)
        non_null = int(row.get(f"n_{name}") or 0)
        role, aggs = _classify(name, dtype, distinct, total)
        columns.append(
            {
                "column": name,
                "type": dtype,
                "distinct": distinct,
                "nulls": total - non_null,
                "role": role,
                "suggested_aggs": aggs,
            }
        )
        if role in ("dimension",) or (role == "id" and distinct <= 50 and distinct < total):
            dim_suggest.append(name)
        if role == "measure":
            measure_suggest.append({"expr": name, "agg": "sum"})
        if role == "id":
            measure_suggest.append({"expr": name, "agg": "count_distinct"})

    # 어떤 테이블이든 '행 개수'는 의미 있는 기본 측정값.
    pk = next((c["column"] for c in cols if c["column"].lower() == "id"), None)
    measure_suggest.insert(0, {"expr": pk or "*", "agg": "count"})

    return {
        "table": table,
        "row_count": total,
        "columns": columns,
        "suggestions": {"dimensions": dim_suggest, "measures": measure_suggest},
    }
