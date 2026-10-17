"""Replay the Korean regional-performance question against the real chat API."""

import argparse
import asyncio
from decimal import Decimal, ROUND_HALF_UP
import json
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".local/dongne-market/app.env", override=True)

from unimetric.ai import retrieval
from unimetric.api.app import app
from unimetric.data import query

PROJECT = "dongne_market_v1_live"
QUESTION = (
    "판매글 등록 지역별로 전체 판매글 수, 거래가 완료된 판매글 수, 거래 성사율, "
    "완료 거래금액을 보여줘. 성사율은 전체 판매글 중 완료 거래가 있는 판매글의 "
    "비율이고, 취소 거래는 제외해줘."
)


def expected_rows():
    """Calculate independently in Python from raw source records, without joins."""
    neighborhoods = query.run_select("SELECT id, name FROM neighborhoods", PROJECT, limit=100000)
    listings = query.run_select("SELECT id, neighborhood_id FROM listings", PROJECT, limit=100000)
    trades = query.run_select("SELECT listing_id, status, amount FROM transactions", PROJECT, limit=100000)
    by_listing = {row["listing_id"]: row for row in trades if row["status"] == "COMPLETED"}
    totals = {row["id"]: {"region": row["name"], "total_listings": 0,
                         "completed_listings": 0, "completed_amount": Decimal(0)}
              for row in neighborhoods}
    for listing in listings:
        result = totals[listing["neighborhood_id"]]
        result["total_listings"] += 1
        trade = by_listing.get(listing["id"])
        if trade:
            result["completed_listings"] += 1
            result["completed_amount"] += Decimal(str(trade["amount"]))
    for result in totals.values():
        result["sell_through_pct"] = (
            Decimal(100) * result["completed_listings"] / result["total_listings"]
        ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) if result["total_listings"] else None
    return sorted(totals.values(), key=lambda row: row["region"])


def compare_rows(actual, expected):
    # SQL aliases may differ; accept explicit aliases for each meaning only.
    aliases = {
        "region": ("region", "region_name", "neighborhood"),
        "total_listings": ("total_listings", "listing_count"),
        "completed_listings": ("completed_listings", "completed_listing_count"),
        "sell_through_pct": ("sell_through_pct", "sell_through", "conversion_rate"),
        "completed_amount": ("completed_amount", "amount", "completed_transaction_amount"),
    }
    def normalize(rows, accept_aliases):
        out = []
        for row in rows:
            values = []
            for field, names in aliases.items():
                matches = [name for name in (names if accept_aliases else (field,)) if name in row]
                if len(matches) != 1:
                    raise ValueError(f"Cannot identify exactly one output column for {field}")
                value = row[matches[0]]
                values.append(str(value) if field == "region" else
                              Decimal(str(value)).quantize(Decimal("0.01")) if value is not None else None)
            out.append(tuple(values))
        return sorted(out)
    return normalize(actual, True) == normalize(expected, False)


async def main(provider, base_url=None):
    context = retrieval.build_context(PROJECT, QUESTION)
    available = query.list_tables(PROJECT)
    selected = [table for table in available if f"테이블 {table}." in context]
    expected = expected_rows()
    async with httpx.AsyncClient(
        transport=None if base_url else httpx.ASGITransport(app=app),
        base_url=base_url or "http://test", timeout=180, trust_env=False,
    ) as client:
        response = await client.post("/api/chat", json={"project": PROJECT, "message": QUESTION, "provider": provider})
        response.raise_for_status()
        body = response.json()
    answer = body.get("answer") or body.get("result") or {}
    try:
        passed = bool(answer) and compare_rows(answer.get("rows", []), expected)
        comparison_error = None
    except (ValueError, ArithmeticError) as exc:
        passed, comparison_error = False, str(exc)
    result = {"question": QUESTION, "project": PROJECT, "provider": provider,
              "endpoint": base_url or "in-process ASGI",
              "selected_tables": selected, "required_tables_present":
              {"listings", "transactions", "neighborhoods"} <= set(selected),
              "context_chars": len(context), "expected": expected, "response": body,
              "pass": passed, "comparison_error": comparison_error}
    output = ROOT / ".local/reports/marketplace/regional-rag-regression.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=True, indent=2, default=str))
    if not passed or not result["required_tables_present"]:
        raise SystemExit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=["codex_cli", "claude_cli"], default="codex_cli")
    parser.add_argument("--base-url", help="Replay against a running server instead of in-process ASGI")
    args = parser.parse_args()
    asyncio.run(main(args.provider, args.base_url))
