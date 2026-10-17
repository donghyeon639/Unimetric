"""Verify top sellers using source SQL and independent Python aggregation."""

from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import psycopg2
from psycopg2.extras import RealDictCursor

ROOT = Path(__file__).resolve().parents[2]
SQL = """SELECT
    u.id AS seller_id,
    u.name AS seller_name,
    COUNT(*) AS completed_count,
    SUM(t.amount) AS completed_amount
FROM transactions AS t
JOIN listings AS l ON l.id = t.listing_id
JOIN users AS u ON u.id = l.seller_id
WHERE t.status = 'COMPLETED'
GROUP BY u.id, u.name
HAVING COUNT(*) >= 3
ORDER BY completed_amount DESC, u.name ASC, u.id ASC
LIMIT 5;"""


def main():
    config = json.loads((ROOT / ".local/dongne-market/connection.json").read_text(encoding="utf-8"))
    conn = psycopg2.connect(host="127.0.0.1", port=config["port"], dbname="dongne_market_v1",
                            user="dongne_market_reader", password=config["reader_password"], connect_timeout=5)
    conn.set_session(isolation_level="REPEATABLE READ", readonly=True)
    try:
        with conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT current_database() AS database, current_user AS role, transaction_timestamp() AS snapshot_time")
                source = dict(cur.fetchone())
                cur.execute(SQL)
                actual = [dict(row) for row in cur.fetchall()]
                cur.execute("SELECT id, name FROM users ORDER BY id")
                users = {row["id"]: row["name"] for row in cur.fetchall()}
                cur.execute("SELECT id, seller_id FROM listings ORDER BY id")
                sellers = {row["id"]: row["seller_id"] for row in cur.fetchall()}
                cur.execute("SELECT id, listing_id, buyer_id, amount, status, completed_at FROM transactions ORDER BY id")
                trades = [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()

    # Do not reuse the SQL JOIN/GROUP BY result when constructing the oracle.
    by_seller = defaultdict(list)
    for trade in trades:
        if trade["status"] == "COMPLETED":
            by_seller[sellers[trade["listing_id"]]].append(trade)
    ranking = [dict(seller_id=sid, seller_name=users[sid], completed_count=len(records),
                    completed_amount=sum((row["amount"] for row in records), Decimal(0)))
               for sid, records in by_seller.items() if len(records) >= 3]
    ranking.sort(key=lambda row: (-row["completed_amount"], row["seller_name"], row["seller_id"]))
    expected = ranking[:5]
    assert actual == expected, "Source SQL disagrees with independent Python aggregation"
    evidence = []
    kst = ZoneInfo("Asia/Seoul")
    for seller in expected:
        sid = seller["seller_id"]
        included = [dict(trade, completed_at=trade["completed_at"].astimezone(kst))
                    for trade in by_seller[sid]]
        excluded = [trade for trade in trades if sellers[trade["listing_id"]] == sid and trade["status"] != "COMPLETED"]
        assert len(included) == seller["completed_count"]
        assert sum((trade["amount"] for trade in included), Decimal(0)) == seller["completed_amount"]
        evidence.append(dict(seller, included_transactions=included, excluded_transactions=excluded))

    result = dict(source=source, verified_at=datetime.now(timezone.utc), question=
                  "완료 거래가 3건 이상인 판매자 중 실제 거래금액 합계가 높은 5명: 판매자 이름, 완료 거래 수, 거래금액 합계",
                  sql=SQL, pass_check=True, oracle="Independent Python aggregation of raw source records in the same read-only repeatable-read snapshot",
                  total_transactions=len(trades), completed_transactions=sum(t["status"] == "COMPLETED" for t in trades),
                  cancelled_transactions=sum(t["status"] == "CANCELLED" for t in trades),
                  eligible_sellers=len(ranking), top_five=evidence, eligible_ranking=ranking)
    output = ROOT / ".local/reports/marketplace"
    output.mkdir(parents=True, exist_ok=True)
    (output / "top-sellers-evidence.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    (output / "top-sellers-query.sql").write_text(SQL + "\n", encoding="utf-8")
    print(json.dumps(dict(verified=True, source=source, top_five=expected, sixth=ranking[5] if len(ranking)>5 else None,
                          eligible_sellers=len(ranking), evidence=evidence), ensure_ascii=True, default=str, indent=2))


if __name__ == "__main__":
    main()
