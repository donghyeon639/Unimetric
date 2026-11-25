"""지표 빌더 — 구조화된 명세(폼/AI 산출물)를 프로젝트 YAML로 병합한다.

설계 의도:
- 비개발자는 폼에서 "무엇을/어떻게 집계, 어떤 축으로 쪼갬"만 고르고, 실제
  YAML(엔티티/측정값/차원/지표) 생성은 여기서 일관되게 처리한다.
- 폼 빌더와 AI 자연어 생성이 '같은 명세 포맷'을 내놓게 해서, 저장 로직을
  한 곳에 모은다(중복·불일치 방지).

명세(spec) 포맷::

    {
      "name": "total_answers", "description": "...", "certified": false,
      "type": "simple",                       # 또는 "ratio"
      "measure": {"agg": "count", "expr": "id"},          # simple
      "numerator": {"agg": "sum", "expr": "amount"},      # ratio
      "denominator": {"agg": "count_distinct", "expr": "order_id"},
      "dimensions": ["feedback_status", "user_id"]
    }
"""

import yaml

from . import model as metrics
from ..workspace.projects import safe_identifier


def _measure_name(agg: str, expr: str) -> str:
    """집계+컬럼으로 측정값 이름을 만든다(예: sum_amount, count_distinct_user_id)."""
    base = "rows" if expr == "*" else safe_identifier(expr)
    return safe_identifier(f"{agg}_{base}")


def _ensure_entity(data: dict, table: str) -> dict:
    """해당 테이블의 엔티티를 찾고, 없으면 만들어 반환한다."""
    entities = data.setdefault("entities", [])
    for ent in entities:
        if ent.get("table") == table:
            # 누락 키 보정.
            ent.setdefault("dimensions", [])
            ent.setdefault("measures", [])
            ent.setdefault("metrics", [])
            return ent
    ent = {"name": safe_identifier(table), "table": table,
           "dimensions": [], "measures": [], "metrics": []}
    entities.append(ent)
    return ent


def _ensure_dimension(ent: dict, name: str) -> None:
    """엔티티에 차원을 등록한다(중복 방지)."""
    if not any(d.get("name") == name for d in ent["dimensions"]):
        ent["dimensions"].append({"name": name})


def _ensure_measure(ent: dict, agg: str, expr: str) -> str:
    """엔티티에 측정값을 등록하고 그 이름을 반환한다(같은 agg+expr는 재사용)."""
    for m in ent["measures"]:
        if m.get("agg") == agg and m.get("expr") == expr:
            return m["name"]
    name = _measure_name(agg, expr)
    # 이름 충돌 시 뒤에 숫자를 붙인다.
    existing = {m["name"] for m in ent["measures"]}
    final = name
    i = 2
    while final in existing:
        final = f"{name}_{i}"
        i += 1
    ent["measures"].append({"name": final, "agg": agg, "expr": expr})
    return final


def add_metric(yaml_text: str, table: str, spec: dict) -> str:
    """기존 YAML에 명세의 지표(+필요한 측정값/차원)를 추가해 새 YAML을 반환한다."""
    data = yaml.safe_load(yaml_text) or {}
    ent = _ensure_entity(data, table)

    for dim in spec.get("dimensions") or []:
        _ensure_dimension(ent, dim)

    metric = {
        "name": spec["name"],
        "type": spec.get("type", "simple"),
        "certified": bool(spec.get("certified", False)),
    }
    if spec.get("description"):
        metric["description"] = spec["description"]
    if spec.get("filter"):  # 지표 내장 WHERE 조건(예: 이씨만 -> name LIKE '이%')
        metric["filter"] = spec["filter"]

    if metric["type"] == "ratio":
        num = spec["numerator"]
        den = spec["denominator"]
        metric["numerator"] = _ensure_measure(ent, num["agg"], num["expr"])
        metric["denominator"] = _ensure_measure(ent, den["agg"], den["expr"])
    else:
        ms = spec["measure"]
        metric["measure"] = _ensure_measure(ent, ms["agg"], ms["expr"])

    # 같은 이름 지표가 있으면 교체(편집), 없으면 추가.
    ent["metrics"] = [m for m in ent["metrics"] if m.get("name") != metric["name"]]
    ent["metrics"].append(metric)

    new_yaml = yaml.dump(data, allow_unicode=True, sort_keys=False)
    # 검증: 깨진 모델이면 여기서 예외가 나서 저장/미리보기 전에 막힌다.
    metrics.load_model(new_yaml)
    return new_yaml


def add_sql_metric(yaml_text: str, spec: dict) -> str:
    """자유 SQL 지표(조인 등)를 카탈로그 YAML에 추가/교체한다(AI sql 답변 저장용)."""
    data = yaml.safe_load(yaml_text) or {}
    sql_metrics = data.setdefault("sql_metrics", [])
    entry = {
        "name": spec["name"],
        "sql": spec["sql"],
        "certified": bool(spec.get("certified", False)),
    }
    if spec.get("description"):
        entry["description"] = spec["description"]
    if spec.get("chart") is not None:
        entry["chart"] = spec["chart"]
    data["sql_metrics"] = [m for m in sql_metrics if m.get("name") != entry["name"]]
    data["sql_metrics"].append(entry)
    new_yaml = yaml.dump(data, allow_unicode=True, sort_keys=False)
    metrics.load_model(new_yaml)  # 검증
    return new_yaml


def merge_yaml(base_text: str, add_text: str) -> str:
    """기존 카탈로그(base)에 새 정의(add)를 '병합'한다 — 기존 지표는 유지.

    엔티티는 table 기준, 차원/측정값/지표·sql 지표는 name 기준으로 합치고,
    같은 이름이면 새 것으로 교체(편집), 없으면 추가(누적). '1개씩 정의 → 누적'.
    """
    base = yaml.safe_load(base_text) or {}
    add = yaml.safe_load(add_text) or {}
    base.setdefault("entities", [])
    base.setdefault("sql_metrics", [])

    for e in add.get("entities", []):
        existing = next((x for x in base["entities"] if x.get("table") == e.get("table")), None)
        if existing is None:
            base["entities"].append(e)
            continue
        for key in ("dimensions", "measures", "metrics"):
            existing.setdefault(key, [])
            for item in e.get(key, []):
                existing[key] = [x for x in existing[key] if x.get("name") != item.get("name")]
                existing[key].append(item)

    for sm in add.get("sql_metrics", []):
        base["sql_metrics"] = [x for x in base["sql_metrics"] if x.get("name") != sm.get("name")]
        base["sql_metrics"].append(sm)

    new_yaml = yaml.dump(base, allow_unicode=True, sort_keys=False)
    metrics.load_model(new_yaml)  # 검증
    return new_yaml


def remove_metric(yaml_text: str, name: str) -> str:
    """카탈로그 YAML에서 지표 하나를 제거한 새 YAML을 반환한다(일반/SQL 둘 다)."""
    data = yaml.safe_load(yaml_text) or {}
    found = False
    for ent in data.get("entities", []):
        before = ent.get("metrics", [])
        kept = [m for m in before if m.get("name") != name]
        if len(kept) != len(before):
            found = True
        ent["metrics"] = kept
    sql_before = data.get("sql_metrics", [])
    sql_kept = [m for m in sql_before if m.get("name") != name]
    if len(sql_kept) != len(sql_before):
        found = True
    data["sql_metrics"] = sql_kept
    if not found:
        raise KeyError(f"지표 '{name}'를 찾을 수 없습니다")
    return yaml.dump(data, allow_unicode=True, sort_keys=False)
