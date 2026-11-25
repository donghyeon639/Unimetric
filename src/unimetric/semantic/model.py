"""시맨틱 레이어: 지표를 한 번 정의하면 SQL로 컴파일한다.

이유:
- 이것이 "데이터 일관성"의 핵심이다. `total_gmv` 같은 지표를 한 번만 정의하면
  (어느 테이블·어느 컬럼·어느 집계) 모든 호출자가 동일한 SQL을 만든다. 두
  대시보드가 "GMV"의 정의를 두고 어긋날 수 없다.
- 정의는 Pydantic으로 검증한다. 오타(공백 있는 지표명, 모르는 집계 등)는
  쿼리 시점이 아니라 로드 시점에 즉시 실패한다.
- 컴파일된 SQL은 의도적으로 schema-less다(`FROM orders`). 프로젝트 격리는
  실행 시점(search_path / 프로젝트 파일)에서 적용되어 정의가 이식 가능해진다.

Phase 0 지원 타입: `simple`(집계 측정값 1개), `ratio`(분자 측정값 / 분모 측정값).
"""

from __future__ import annotations

import re
from typing import Literal

import yaml
from pydantic import BaseModel, field_validator
from .visualization import ChartSpec

# 집계 종류 -> SQL 템플릿.
_AGG_SQL = {
    "sum": "SUM({})",
    "count": "COUNT({})",
    "count_distinct": "COUNT(DISTINCT {})",
    "avg": "AVG({})",
    "min": "MIN({})",
    "max": "MAX({})",
}

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _require_identifier(value: str, field: str) -> str:
    """공백 등이 섞여 식별자가 아닌 이름을 거부한다."""
    if not _IDENT_RE.match(value):
        raise ValueError(f"{field} '{value}'는 공백 없는 식별자여야 합니다")
    return value


class Dimension(BaseModel):
    """집계의 기준 축(차원). 예: region, order_date."""

    name: str

    @field_validator("name")
    @classmethod
    def _v(cls, v: str) -> str:
        """차원명이 유효한 식별자인지 검증한다."""
        return _require_identifier(v, "dimension")


class Measure(BaseModel):
    """집계 가능한 값(측정값). 예: gmv = sum(amount)."""

    name: str
    agg: Literal["sum", "count", "count_distinct", "avg", "min", "max"]
    expr: str

    @field_validator("name")
    @classmethod
    def _v(cls, v: str) -> str:
        """측정값명이 유효한 식별자인지 검증한다."""
        return _require_identifier(v, "measure")


class Metric(BaseModel):
    """사용자에게 노출되는 지표. simple(측정값 1개) 또는 ratio(분자/분모)."""

    name: str
    type: Literal["simple", "ratio"]
    measure: str | None = None             # type=simple 일 때
    numerator: str | None = None           # type=ratio 일 때
    denominator: str | None = None         # type=ratio 일 때
    filter: str | None = None              # 지표에 내장된 WHERE 조건(예: "name LIKE '이%'")
    description: str | None = None
    owner: str | None = None
    certified: bool = False

    @field_validator("name")
    @classmethod
    def _v(cls, v: str) -> str:
        """지표명이 유효한 식별자인지 검증한다."""
        return _require_identifier(v, "metric")


class Entity(BaseModel):
    """하나의 테이블과 그 차원/측정값/지표들을 묶는 단위."""

    name: str
    table: str
    dimensions: list[Dimension] = []
    measures: list[Measure] = []
    metrics: list[Metric] = []


class SqlMetric(BaseModel):
    """자유 SQL 지표 — 조인 등 단일테이블로 표현 못 하는 분석 답변을 그대로 저장.

    AI의 `sql` 답변(조인 쿼리)을 이름 붙여 재사용하기 위한 타입. compile하면
    저장된 SQL을 그대로 실행한다(읽기 전용 SELECT).
    """

    name: str
    sql: str
    chart: ChartSpec | None = None
    description: str | None = None
    owner: str | None = None
    certified: bool = False

    @field_validator("name")
    @classmethod
    def _v(cls, v: str) -> str:
        return _require_identifier(v, "metric")


class Model(BaseModel):
    """한 프로젝트의 전체 시맨틱 모델."""

    entities: list[Entity] = []
    sql_metrics: list[SqlMetric] = []   # 자유 SQL 지표(조인 등)

    def metric_chart(self, name: str) -> ChartSpec | None:
        metric = self._find_sql_metric(name)
        return metric.chart if metric else None

    # --- 조회 헬퍼 -----------------------------------------------------
    def _find_metric(self, name: str) -> tuple[Entity, Metric]:
        """이름으로 지표와 그 지표가 속한 엔티티를 찾는다."""
        for entity in self.entities:
            for metric in entity.metrics:
                if metric.name == name:
                    return entity, metric
        raise KeyError(f"지표 '{name}'를 찾을 수 없습니다")

    def _find_sql_metric(self, name: str) -> "SqlMetric | None":
        """이름으로 자유 SQL 지표를 찾는다(없으면 None)."""
        for sm in self.sql_metrics:
            if sm.name == name:
                return sm
        return None

    def _measure(self, entity: Entity, name: str) -> Measure:
        """엔티티 안에서 이름으로 측정값을 찾는다."""
        for m in entity.measures:
            if m.name == name:
                return m
        raise KeyError(f"엔티티 '{entity.name}'에 측정값 '{name}'가 없습니다")

    def _agg_expr(self, entity: Entity, measure_name: str) -> str:
        """측정값을 'AGG(expr)' 형태의 SQL 조각으로 만든다."""
        m = self._measure(entity, measure_name)
        return _AGG_SQL[m.agg].format(m.expr)

    def metric_detail(self, name: str) -> dict:
        """지표 하나를 빌더 명세(spec) 형태로 복원한다(편집 화면 프리필용)."""
        sm = self._find_sql_metric(name)
        if sm:
            return {"name": sm.name, "type": "sql", "sql": sm.sql,
                    "description": sm.description or "", "certified": sm.certified}
        entity, m = self._find_metric(name)

        def ref(measure_name: str) -> dict:
            ms = self._measure(entity, measure_name)
            return {"agg": ms.agg, "expr": ms.expr}

        spec = {
            "name": m.name,
            "type": m.type,
            "table": entity.table,
            "description": m.description or "",
            "certified": m.certified,
            "filter": m.filter or "",
            "dimensions": [d.name for d in entity.dimensions],
        }
        if m.type == "ratio":
            spec["numerator"] = ref(m.numerator)
            spec["denominator"] = ref(m.denominator)
        else:
            spec["measure"] = ref(m.measure)
        return spec

    # --- 공개 API ------------------------------------------------------
    def list_metrics(self) -> list[dict]:
        """정의된 모든 지표의 메타정보 목록을 반환한다(UI/카탈로그용)."""
        out = []
        for entity in self.entities:
            for m in entity.metrics:
                out.append(
                    {
                        "name": m.name,
                        "type": m.type,
                        "entity": entity.name,
                        "description": m.description,
                        "owner": m.owner,
                        "certified": m.certified,
                    }
                )
        for sm in self.sql_metrics:
            out.append({"name": sm.name, "type": "sql", "entity": "(sql)",
                        "description": sm.description, "owner": sm.owner,
                        "certified": sm.certified})
        return out

    def compile(
        self,
        metric: str,
        group_by: list[str] | None = None,
        filters: list[str] | None = None,
    ) -> str:
        """지표를 group_by/filters와 함께 실행 가능한 SELECT SQL로 컴파일한다."""
        group_by = group_by or []
        filters = filters or []

        # 자유 SQL 지표면 저장된 SQL을 그대로 실행한다(group_by/filters 무시).
        sm = self._find_sql_metric(metric)
        if sm:
            return sm.sql

        entity, m = self._find_metric(metric)

        # 요청한 차원이 엔티티에 실제 존재하는지 검증.
        valid_dims = {d.name for d in entity.dimensions}
        for g in group_by:
            if g not in valid_dims:
                raise ValueError(f"엔티티 '{entity.name}'에 없는 차원 '{g}'")

        if m.type == "simple":
            if not m.measure:
                raise ValueError(f"simple 지표 '{m.name}'에는 'measure'가 필요합니다")
            value_expr = f"{self._agg_expr(entity, m.measure)} AS {m.name}"
        elif m.type == "ratio":
            if not (m.numerator and m.denominator):
                raise ValueError(
                    f"ratio 지표 '{m.name}'에는 'numerator'와 'denominator'가 필요합니다"
                )
            num = self._agg_expr(entity, m.numerator)
            den = self._agg_expr(entity, m.denominator)
            # 분모 0 방지를 위해 NULLIF, 실수 나눗셈을 위해 ::float.
            value_expr = f"({num})::float / NULLIF({den}, 0) AS {m.name}"
        else:  # pragma: no cover - Pydantic이 이미 막음
            raise ValueError(f"지원하지 않는 지표 타입 '{m.type}'")

        select_cols = [f"{g} AS {g}" for g in group_by] + [value_expr]
        sql = f"SELECT {', '.join(select_cols)} FROM {entity.table}"
        # 지표에 내장된 필터(예: "name LIKE '이%'")와 조회 시 전달된 필터를 함께 적용.
        where_parts = ([m.filter] if m.filter else []) + list(filters)
        if where_parts:
            sql += " WHERE " + " AND ".join(where_parts)
        if group_by:
            sql += " GROUP BY " + ", ".join(group_by)
            sql += " ORDER BY " + ", ".join(group_by)
        return sql


def load_model(yaml_text: str) -> Model:
    """YAML 텍스트에서 시맨틱 모델을 파싱·검증해 반환한다."""
    data = yaml.safe_load(yaml_text) or {}
    return Model(**data)


def load_model_file(path: str) -> Model:
    """YAML 파일 경로에서 시맨틱 모델을 읽어 반환한다."""
    with open(path, "r", encoding="utf-8") as fh:
        return load_model(fh.read())
