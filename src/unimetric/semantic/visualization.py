"""Describe a chart using columns from a query result."""

from decimal import Decimal
from math import isfinite

from pydantic import BaseModel, Field, ValidationError


class ChartSpec(BaseModel):
    label_column: str = Field(min_length=1, max_length=128)
    value_column: str = Field(min_length=1, max_length=128)
    title: str | None = Field(default=None, max_length=100)
    unit: str | None = Field(default=None, max_length=16)


def query_result(sql: str, rows: list[dict], chart: object = None, *, require_chart=False) -> dict:
    """Attach chart instructions only when they fit the actual SQL output."""
    result = {"sql": sql, "rows": rows}
    if require_chart or chart is not None:
        result["chart"] = None
    if chart is None or not rows:
        return result
    try:
        spec = ChartSpec.model_validate(chart)
    except ValidationError:
        return result
    if spec.label_column == spec.value_column:
        return result
    if any(spec.label_column not in row or spec.value_column not in row for row in rows):
        return result
    values = [row[spec.value_column] for row in rows if row[spec.value_column] is not None]
    if values and all(
        isinstance(value, (int, float, Decimal)) and not isinstance(value, bool)
        and isfinite(value)
        for value in values
    ):
        result["chart"] = spec.model_dump(exclude_none=True)
    return result
