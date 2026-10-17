import pytest

from tools.marketplace import fixture


def test_seed_has_expected_scale_and_is_reproducible():
    first = fixture.generate()
    assert {table: len(rows) for table, rows in first.items()} == {
        "neighborhoods": 10, "users": 300, "categories": 12,
        "listings": 1000, "offers": 3000, "transactions": 600, "reviews": 800,
    }
    assert fixture.fingerprint(first) == fixture.fingerprint(fixture.generate())
    assert fixture.fingerprint(first) != fixture.fingerprint(fixture.generate(seed=123))
    assert all(fixture.validate(first).values())


def test_timezone_boundary_is_present_in_fixture():
    trades = fixture.generate()["transactions"]
    assert trades[0]["completed_at"].astimezone(fixture.KST).isoformat().startswith("2026-09-30T23:59")
    assert trades[1]["completed_at"].astimezone(fixture.KST).isoformat().startswith("2026-10-01T00:00")


def test_validation_rejects_nonparticipant_review():
    rows = fixture.generate(20)
    rows["reviews"][0]["author_id"] = -1
    with pytest.raises(ValueError, match="Invalid fixture"):
        fixture.validate(rows)


def test_validation_rejects_mismatched_offer():
    rows = fixture.generate(20)
    rows["transactions"][0]["amount"] += 1
    with pytest.raises(ValueError, match="Invalid fixture"):
        fixture.validate(rows)


def test_oracle_contains_twenty_cases_with_independent_values():
    rows = fixture.generate()
    cases = {c["id"]: c for c in fixture.cases(rows)}
    assert len(cases) == 20
    assert cases["completed_count"]["expected"] == [{"value": 540}]
    assert cases["cancelled"]["expected"] == [{"value": 60}]
    assert cases["unconverted_listings"]["expected"] == [{"value": 460}]
    assert cases["sell_through"]["expected"][0]["value"] == 54


def test_fixture_scales_without_breaking_relationships():
    assert all(fixture.validate(fixture.generate(100)).values())
