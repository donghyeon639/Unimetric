"""Deterministic v1 marketplace fixture and independent Python answer oracle."""

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json
import random

from sqlalchemy import (CheckConstraint, Column, DateTime, ForeignKey, Integer,
                        MetaData, Numeric, String, Table, Text, UniqueConstraint)

KST = timezone(timedelta(hours=9))
UTC = timezone.utc
SEED = 20261017
REFERENCE = datetime(2026, 10, 17, tzinfo=KST)
TABLES = ("neighborhoods", "users", "categories", "listings", "offers", "transactions", "reviews")


def metadata() -> MetaData:
    m = MetaData()
    Table("neighborhoods", m, Column("id", Integer, primary_key=True), Column("name", String(40), nullable=False, unique=True))
    Table("users", m, Column("id", Integer, primary_key=True), Column("name", String(60), nullable=False, unique=True),
          Column("neighborhood_id", Integer, ForeignKey("neighborhoods.id"), nullable=False), Column("created_at", DateTime(timezone=True), nullable=False))
    Table("categories", m, Column("id", Integer, primary_key=True), Column("name", String(40), nullable=False, unique=True))
    Table("listings", m, Column("id", Integer, primary_key=True), Column("seller_id", Integer, ForeignKey("users.id"), nullable=False),
          Column("neighborhood_id", Integer, ForeignKey("neighborhoods.id"), nullable=False), Column("category_id", Integer, ForeignKey("categories.id"), nullable=False),
          Column("title", String(120), nullable=False), Column("asking_price", Numeric(12, 0), nullable=False), Column("status", String(20), nullable=False),
          Column("created_at", DateTime(timezone=True), nullable=False), CheckConstraint("asking_price > 0"), CheckConstraint("status IN ('OPEN','SOLD','HIDDEN')"))
    Table("offers", m, Column("id", Integer, primary_key=True), Column("listing_id", Integer, ForeignKey("listings.id"), nullable=False),
          Column("buyer_id", Integer, ForeignKey("users.id"), nullable=False), Column("offered_price", Numeric(12, 0), nullable=False),
          Column("status", String(20), nullable=False), Column("created_at", DateTime(timezone=True), nullable=False),
          UniqueConstraint("listing_id", "buyer_id"), CheckConstraint("offered_price > 0"), CheckConstraint("status IN ('PENDING','ACCEPTED','REJECTED','WITHDRAWN')"))
    Table("transactions", m, Column("id", Integer, primary_key=True), Column("listing_id", Integer, ForeignKey("listings.id"), nullable=False, unique=True),
          Column("offer_id", Integer, ForeignKey("offers.id"), nullable=False, unique=True), Column("buyer_id", Integer, ForeignKey("users.id"), nullable=False),
          Column("amount", Numeric(12, 0), nullable=False), Column("status", String(20), nullable=False),
          Column("created_at", DateTime(timezone=True), nullable=False), Column("completed_at", DateTime(timezone=True)),
          CheckConstraint("amount > 0"), CheckConstraint("status IN ('COMPLETED','CANCELLED')"),
          CheckConstraint("(status = 'COMPLETED') = (completed_at IS NOT NULL)"),
          CheckConstraint("completed_at IS NULL OR completed_at >= created_at"))
    Table("reviews", m, Column("id", Integer, primary_key=True), Column("transaction_id", Integer, ForeignKey("transactions.id"), nullable=False),
          Column("author_id", Integer, ForeignKey("users.id"), nullable=False), Column("rating", Integer, nullable=False), Column("body", Text, nullable=False),
          Column("created_at", DateTime(timezone=True), nullable=False), UniqueConstraint("transaction_id", "author_id"), CheckConstraint("rating BETWEEN 1 AND 5"))
    return m


def generate(listing_count: int = 1000, seed: int = SEED) -> dict[str, list[dict]]:
    if listing_count < 20:
        raise ValueError("listing_count must be at least 20")
    rng = random.Random(seed)
    regions = ["역삼동", "삼성동", "서초동", "잠실동", "성수동", "망원동", "연남동", "합정동", "정자동", "판교동"]
    category_names = ["가전", "가구", "의류", "잡화", "도서", "유아동", "취미", "스포츠", "디지털", "생활용품", "반려동물", "기타"]
    rows = {name: [] for name in TABLES}
    rows["neighborhoods"] = [{"id": i + 1, "name": name} for i, name in enumerate(regions)]
    rows["categories"] = [{"id": i + 1, "name": name} for i, name in enumerate(category_names)]
    user_count = max(10, listing_count * 3 // 10)
    base = datetime(2026, 1, 1, tzinfo=UTC)
    rows["users"] = [{"id": i, "name": f"테스트사용자{i:04d}", "neighborhood_id": (i - 1) % 10 + 1,
                      "created_at": base - timedelta(days=rng.randrange(90))} for i in range(1, user_count + 1)]
    sale_ids = sorted(rng.sample(range(1, listing_count + 1), listing_count * 6 // 10))
    sale_positions = {listing: i + 1 for i, listing in enumerate(sale_ids)}
    for i in range(1, listing_count + 1):
        seller = rng.randint(1, user_count)
        when = base + timedelta(days=rng.randrange(283), hours=rng.randrange(24))
        # Two completed trades straddle the September/October boundary in KST.
        if i in sale_ids[:2]:
            when = datetime(2026, 9, 29, 6, tzinfo=UTC)
        position = sale_positions.get(i)
        completed = position is not None and position % 10 != 0
        listing = {"id": i, "seller_id": seller, "neighborhood_id": (seller - 1) % 10 + 1,
                   "category_id": rng.randint(1, 12), "title": f"합성 중고 물품 {i:04d}", "asking_price": rng.randint(10, 2000) * 100,
                   "status": "SOLD" if completed else ("HIDDEN" if i % 7 == 0 else "OPEN"), "created_at": when}
        rows["listings"].append(listing)
        buyers = rng.sample([u for u in range(1, user_count + 1) if u != seller], 3)
        offers = []
        for j, buyer in enumerate(buyers):
            offer = {"id": (i - 1) * 3 + j + 1, "listing_id": i, "buyer_id": buyer,
                     "offered_price": listing["asking_price"] - rng.randint(0, listing["asking_price"] // 1000) * 100,
                     "status": "ACCEPTED" if position and j == 0 else ("REJECTED" if position else ("PENDING" if j < 2 else "WITHDRAWN")),
                     "created_at": when + timedelta(hours=j + 1)}
            rows["offers"].append(offer)
            offers.append(offer)
        if position:
            closed = when + timedelta(days=rng.randint(1, 5))
            if position == 1:
                closed = datetime(2026, 9, 30, 14, 59, tzinfo=UTC)
            elif position == 2:
                closed = datetime(2026, 9, 30, 15, 0, tzinfo=UTC)
            rows["transactions"].append({"id": position, "listing_id": i, "offer_id": offers[0]["id"], "buyer_id": buyers[0],
                                          "amount": offers[0]["offered_price"], "status": "COMPLETED" if completed else "CANCELLED",
                                          "created_at": when + timedelta(hours=4), "completed_at": closed if completed else None})
    possible_reviews = []
    by_listing = {row["id"]: row for row in rows["listings"]}
    for trade in rows["transactions"]:
        if trade["status"] == "COMPLETED":
            possible_reviews.extend([(trade, trade["buyer_id"]), (trade, by_listing[trade["listing_id"]]["seller_id"])])
    chosen = rng.sample(possible_reviews, min(listing_count * 8 // 10, len(possible_reviews)))
    rows["reviews"] = [{"id": i + 1, "transaction_id": trade["id"], "author_id": author,
                        "rating": rng.choice([3, 4, 4, 5, 5]), "body": "테스트용 가상 거래 후기",
                        "created_at": trade["completed_at"] + timedelta(hours=6)} for i, (trade, author) in enumerate(chosen)]
    return rows


def fingerprint(rows: dict) -> str:
    return hashlib.sha256(json.dumps(rows, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def validate(rows: dict) -> dict[str, bool]:
    users = {r["id"]: r for r in rows["users"]}
    listings = {r["id"]: r for r in rows["listings"]}
    offers = {r["id"]: r for r in rows["offers"]}
    trades = {r["id"]: r for r in rows["transactions"]}
    checks = {
        "primary_keys_unique": all(len({r["id"] for r in records}) == len(records) for records in rows.values()),
        "offer_buyers_are_not_sellers": all(r["buyer_id"] in users and r["buyer_id"] != listings[r["listing_id"]]["seller_id"] for r in rows["offers"]),
        "one_trade_per_listing": len({r["listing_id"] for r in trades.values()}) == len(trades),
        "trade_matches_accepted_offer": all(offers[t["offer_id"]]["listing_id"] == t["listing_id"] and offers[t["offer_id"]]["buyer_id"] == t["buyer_id"]
                                            and offers[t["offer_id"]]["status"] == "ACCEPTED" and offers[t["offer_id"]]["offered_price"] == t["amount"] for t in trades.values()),
        "only_participants_review": all(r["author_id"] in {trades[r["transaction_id"]]["buyer_id"], listings[trades[r["transaction_id"]]["listing_id"]]["seller_id"]} for r in rows["reviews"]),
        "only_completed_trades_reviewed": all(trades[r["transaction_id"]]["status"] == "COMPLETED" for r in rows["reviews"]),
        "review_authors_unique_per_trade": len({(r["transaction_id"], r["author_id"]) for r in rows["reviews"]}) == len(rows["reviews"]),
        "no_future_events": all(v < REFERENCE for records in rows.values() for r in records for k, v in r.items() if k.endswith("_at") and v is not None),
    }
    if not all(checks.values()):
        raise ValueError(f"Invalid fixture: {checks}")
    return checks


def _rounded(value) -> Decimal:
    return Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def cases(rows: dict) -> list[dict]:
    """Expectations use Python records, never the SQL under evaluation."""
    completed = [r for r in rows["transactions"] if r["status"] == "COMPLETED"]
    listings = {r["id"]: r for r in rows["listings"]}
    regions = {r["id"]: r["name"] for r in rows["neighborhoods"]}
    categories = {r["id"]: r["name"] for r in rows["categories"]}
    users = {r["id"]: r["name"] for r in rows["users"]}
    start, end = datetime(2026, 9, 1, tzinfo=KST), datetime(2026, 10, 1, tzinfo=KST)
    september = [t for t in completed if start <= t["completed_at"] < end]
    reviewed = {r["transaction_id"] for r in rows["reviews"]}
    completed_listing_ids = {r["listing_id"] for r in completed}
    offered_listing_ids = {r["listing_id"] for r in rows["offers"]}
    amounts_by_region, amounts_by_category, amounts_by_seller, amounts_by_month = defaultdict(list), defaultdict(list), defaultdict(list), defaultdict(list)
    for trade in completed:
        listing = listings[trade["listing_id"]]
        amounts_by_region[regions[listing["neighborhood_id"]]].append(trade["amount"])
        amounts_by_category[categories[listing["category_id"]]].append(trade["amount"])
        amounts_by_seller[users[listing["seller_id"]]].append(trade["amount"])
        amounts_by_month[trade["completed_at"].astimezone(KST).strftime("%Y-%m")].append(trade["amount"])
    out = []
    def add(id, question, sql, expected, tables):
        out.append({"id": id, "question": question, "sql": sql, "expected": expected, "required_tables": tables})
    add("users", "전체 사용자 수를 구해줘. 출력 컬럼은 value 하나야.", "SELECT COUNT(*) AS value FROM users", [{"value": len(users)}], ["users"])
    add("listings", "전체 판매글 수를 구해줘. 출력 컬럼은 value 하나야.", "SELECT COUNT(*) AS value FROM listings", [{"value": len(listings)}], ["listings"])
    add("completed_count", "status가 COMPLETED인 완료 거래 건수를 구해줘. 출력 컬럼은 value 하나야.", "SELECT COUNT(*) AS value FROM transactions WHERE status='COMPLETED'", [{"value": len(completed)}], ["transactions"])
    add("completed_amount", "완료 거래(status=COMPLETED)의 실제 거래금액 amount 합계를 구해줘. 판매글 asking_price와 구분하고 출력 컬럼은 value 하나야.", "SELECT SUM(amount) AS value FROM transactions WHERE status='COMPLETED'", [{"value": sum(t["amount"] for t in completed)}], ["transactions"])
    sept_filter = "status='COMPLETED' AND completed_at >= TIMESTAMPTZ '2026-09-01 00:00:00+09' AND completed_at < TIMESTAMPTZ '2026-10-01 00:00:00+09'"
    add("september_count", "한국시간 2026년 9월에 완료된 거래 건수를 구해줘. 완료 기준은 status=COMPLETED와 completed_at이고 출력 컬럼은 value 하나야.", "SELECT COUNT(*) AS value FROM transactions WHERE " + sept_filter, [{"value": len(september)}], ["transactions"])
    add("september_amount", "한국시간 2026년 9월 완료 거래(status=COMPLETED, completed_at 기준)의 amount 합계를 구해줘. 출력 컬럼은 value 하나야.", "SELECT SUM(amount) AS value FROM transactions WHERE " + sept_filter, [{"value": sum(t["amount"] for t in september)}], ["transactions"])
    add("regional_amount", "완료 거래(status=COMPLETED)의 판매글 등록 지역별 amount 합계를 구해줘. 현재 사용자 지역 대신 listings.neighborhood_id를 사용하고 결과 컬럼 순서는 region, amount야.",
        "SELECT n.name AS region, SUM(t.amount) AS amount FROM transactions t JOIN listings l ON l.id=t.listing_id JOIN neighborhoods n ON n.id=l.neighborhood_id WHERE t.status='COMPLETED' GROUP BY n.name",
        [{"region": k, "amount": sum(v)} for k, v in amounts_by_region.items()], ["transactions", "listings", "neighborhoods"])
    add("category_average", "완료 거래(status=COMPLETED)의 카테고리별 실제 평균 거래금액을 소수점 둘째자리까지 구해줘. 결과 컬럼 순서는 category, average야.",
        "SELECT c.name AS category, ROUND(AVG(t.amount),2) AS average FROM transactions t JOIN listings l ON l.id=t.listing_id JOIN categories c ON c.id=l.category_id WHERE t.status='COMPLETED' GROUP BY c.name",
        [{"category": k, "average": _rounded(Decimal(sum(v))/len(v))} for k, v in amounts_by_category.items()], ["transactions", "listings", "categories"])
    add("no_reviews", "완료 거래(status=COMPLETED) 중 후기가 한 건도 없는 거래 건수를 구해줘. 후기 행 수가 아니라 거래 수이며 출력 컬럼은 value 하나야.",
        "SELECT COUNT(*) AS value FROM transactions t WHERE t.status='COMPLETED' AND NOT EXISTS (SELECT 1 FROM reviews r WHERE r.transaction_id=t.id)",
        [{"value": sum(t["id"] not in reviewed for t in completed)}], ["transactions", "reviews"])
    add("unconverted_listings", "구매 제안을 받았지만 완료 거래(status=COMPLETED)가 없는 판매글 수를 구해줘. 취소 거래는 성사로 세지 말고 출력 컬럼은 value 하나야.",
        "SELECT COUNT(*) AS value FROM listings l WHERE EXISTS (SELECT 1 FROM offers o WHERE o.listing_id=l.id) AND NOT EXISTS (SELECT 1 FROM transactions t WHERE t.listing_id=l.id AND t.status='COMPLETED')",
        [{"value": len(offered_listing_ids - completed_listing_ids)}], ["listings", "offers", "transactions"])
    add("unique_buyers", "완료 거래의 고유 구매자 수를 구해줘. status=COMPLETED, buyer_id 기준이고 출력 컬럼은 value야.", "SELECT COUNT(DISTINCT buyer_id) AS value FROM transactions WHERE status='COMPLETED'", [{"value": len({t["buyer_id"] for t in completed})}], ["transactions"])
    add("reviews", "전체 후기 수를 구해줘. 출력 컬럼은 value야.", "SELECT COUNT(*) AS value FROM reviews", [{"value": len(rows["reviews"])}], ["reviews"])
    add("rating", "전체 후기의 평균 평점을 소수점 둘째자리까지 구해줘. 출력 컬럼은 value야.", "SELECT ROUND(AVG(rating),2) AS value FROM reviews", [{"value": _rounded(Decimal(sum(r["rating"] for r in rows["reviews"]))/len(rows["reviews"]))}], ["reviews"])
    add("cancelled", "status=CANCELLED인 취소 거래 수를 구해줘. 출력 컬럼은 value야.", "SELECT COUNT(*) AS value FROM transactions WHERE status='CANCELLED'", [{"value": len(rows["transactions"]) - len(completed)}], ["transactions"])
    add("listing_status", "판매글 상태별 개수를 구해줘. 결과 컬럼 순서는 status, count야.", "SELECT status,COUNT(*) AS count FROM listings GROUP BY status", [{"status": k, "count": v} for k, v in Counter(l["status"] for l in listings.values()).items()], ["listings"])
    add("offer_status", "구매 제안 상태별 개수를 구해줘. 결과 컬럼 순서는 status, count야.", "SELECT status,COUNT(*) AS count FROM offers GROUP BY status", [{"status": k, "count": v} for k, v in Counter(o["status"] for o in rows["offers"]).items()], ["offers"])
    seller_ids = {l["seller_id"] for l in listings.values()}
    add("users_without_listings", "판매글을 한 번도 등록하지 않은 사용자 수를 구해줘. 출력 컬럼은 value야.", "SELECT COUNT(*) AS value FROM users u WHERE NOT EXISTS (SELECT 1 FROM listings l WHERE l.seller_id=u.id)", [{"value": len(users) - len(seller_ids)}], ["users", "listings"])
    add("sell_through", "전체 판매글 대비 완료 거래가 있는 판매글의 비율을 퍼센트로 소수점 둘째자리까지 구해줘. 출력 컬럼은 value야.", "SELECT ROUND(100.0*(SELECT COUNT(DISTINCT listing_id) FROM transactions WHERE status='COMPLETED')/COUNT(*),2) AS value FROM listings", [{"value": _rounded(Decimal(100)*len(completed_listing_ids)/len(listings))}], ["transactions", "listings"])
    top = sorted(((k, sum(v)) for k, v in amounts_by_seller.items()), key=lambda item: (-item[1], item[0]))[:5]
    add("top_sellers", "완료 거래금액 합계가 높은 판매자 5명을 구해줘. 판매자는 listings.seller_id이고 금액 내림차순, 동률은 사용자 이름 오름차순이야. 결과 컬럼 순서는 seller, amount야.",
        "SELECT u.name AS seller,SUM(t.amount) AS amount FROM transactions t JOIN listings l ON l.id=t.listing_id JOIN users u ON u.id=l.seller_id WHERE t.status='COMPLETED' GROUP BY u.name ORDER BY amount DESC,u.name LIMIT 5", [{"seller": k, "amount": v} for k, v in top], ["transactions", "listings", "users"])
    add("monthly_amount", "한국시간 completed_at 기준 월별 완료 거래금액 합계를 구해줘. status=COMPLETED이고 결과 컬럼 순서는 month(YYYY-MM), amount야.", "SELECT to_char(completed_at AT TIME ZONE 'Asia/Seoul','YYYY-MM') AS month,SUM(amount) AS amount FROM transactions WHERE status='COMPLETED' GROUP BY 1", [{"month": k, "amount": sum(v)} for k, v in amounts_by_month.items()], ["transactions"])
    return out


def metrics_yaml(rows: dict) -> str:
    import yaml
    simple = {"name": "transactions", "table": "transactions", "dimensions": [{"name": "status"}],
              "measures": [{"name": "trade_count", "agg": "count", "expr": "id"}, {"name": "trade_amount", "agg": "sum", "expr": "amount"}],
              "metrics": [{"name": "completed_count", "type": "simple", "measure": "trade_count", "filter": "status='COMPLETED'", "description": "완료 거래 건수; 취소 제외", "certified": True},
                          {"name": "completed_amount", "type": "simple", "measure": "trade_amount", "filter": "status='COMPLETED'", "description": "완료 거래 실제 금액 합계 KRW; 플랫폼 매출 아님", "certified": True}]}
    sql_metrics = [{"name": c["id"], "type": "sql", "sql": c["sql"], "description": c["question"], "certified": True} for c in cases(rows) if c["id"] not in {"completed_count", "completed_amount"}]
    for metric in sql_metrics:
        metric.pop("type")
    return yaml.safe_dump({"entities": [simple], "sql_metrics": sql_metrics}, allow_unicode=True, sort_keys=False)
