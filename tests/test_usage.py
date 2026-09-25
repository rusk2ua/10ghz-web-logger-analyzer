import json
from datetime import datetime, timezone

import pytest

from conftest import fixture_text

CSV = {"name": "sample_qso_log.csv", "content": fixture_text("sample_qso_log.csv")}
CABRILLO = {"name": "sample_cabrillo.log", "content": fixture_text("sample_cabrillo.log")}
BROWSER = {"User-Agent": "Mozilla/5.0 (Macintosh) Safari/605.1.15"}


def raw_records(output_dir):
    return [json.loads(p.read_text()) for p in sorted((output_dir / "usage" / "raw").rglob("*.json"))]


def ping(ip="203.0.113.7", headers=BROWSER):
    import handler
    return handler.handler({"rawPath": "/api/ping", "headers": headers,
                            "requestContext": {"http": {"method": "POST", "sourceIp": ip}}})


def at(monkeypatch, when):
    import usage
    monkeypatch.setattr(usage, "_now", lambda: datetime.fromisoformat(when).replace(tzinfo=timezone.utc))


# ------------------------------------------------------------------ recording

def test_successful_analysis_is_recorded_without_call_signs(call_api, output_dir):
    status, _ = call_api({"files": [CSV, CABRILLO], "callsign": "K2UA",
                          "outputs": ["cabrillo", "summary", "comparison"]})
    assert status == 200
    [rec] = raw_records(output_dir)
    assert rec["type"] == "analysis" and rec["status"] == "ok"
    assert rec["source"] == "files" and rec["nLogs"] == 2
    assert rec["outputs"] == ["cabrillo", "summary", "comparison"]
    assert [log["format"] for log in rec["logs"]] == ["csv", "cabrillo"]
    assert rec["logs"][0]["qsos"] == 7 and rec["logs"][0]["year"] == 2026
    assert rec["logs"][0]["bands"] == ["10 GHz", "24 GHz"]
    assert rec["logs"][0]["grids"] == ["EN90", "EN91"]
    # Same call sign in both logs -> same anonymous id; the call sign itself is never stored.
    assert rec["logs"][0]["op"] == rec["logs"][1]["op"] and len(rec["logs"][0]["op"]) == 16
    assert "K2UA" not in json.dumps(rec).upper()
    assert rec["files"] > 0 and rec["duration_ms"] >= 0 and rec["upstream"].startswith("v")


def test_rejected_request_is_recorded_with_error_code(call_api, output_dir):
    status, _ = call_api({"files": [CSV], "outputs": ["summary"]})  # CSV needs a call sign
    assert status == 400
    [rec] = raw_records(output_dir)
    assert (rec["status"], rec["error"]) == ("input_error", "callsign")


@pytest.mark.parametrize("message, code", [
    ("'HELLO' doesn't look like a valid call sign.", "callsign"),
    ("x.csv: no QSOs found. Cabrillo logs need QSO: lines", "no_qsos"),
    ("Couldn't download that Google Sheet.", "google_sheets"),
    ("big.csv: too large (limit 1 MB per log).", "too_large"),
    ("notes.txt: this doesn't look like a Cabrillo log or a CSV file.", "unrecognized_format"),
    ("Something unexpected", "other"),
])
def test_error_codes(message, code):
    import usage
    assert usage.error_code(message) == code


def test_ping_counts_browsers_not_bots(output_dir):
    assert ping()["statusCode"] == 204
    assert ping(headers={"User-Agent": "Googlebot/2.1"})["statusCode"] == 204
    assert ping(headers={})["statusCode"] == 204
    records = raw_records(output_dir)
    assert [r["type"] for r in records] == ["visit"]
    assert "203.0.113.7" not in json.dumps(records)


def test_recording_failure_never_breaks_analysis(call_api, monkeypatch):
    import usage
    monkeypatch.setattr(usage, "_store", lambda: (_ for _ in ()).throw(RuntimeError("S3 down")))
    status, body = call_api({"files": [CABRILLO], "outputs": ["summary"]})
    assert status == 200 and body["success"]
    assert ping()["statusCode"] == 204


# ------------------------------------------------------------------ aggregation

def test_aggregate_compacts_and_publishes(call_api, output_dir, monkeypatch):
    import handler
    import usage

    at(monkeypatch, "2026-09-20T15:00:00")
    call_api({"files": [CSV], "callsign": "K2UA", "outputs": ["cabrillo", "directional_viz"]})
    ping()
    ping(ip="198.51.100.9")
    ping()  # same visitor again, same day

    at(monkeypatch, "2026-09-21T10:00:00")
    call_api({"files": [CABRILLO], "outputs": ["summary"]})         # same operator (K2UA)
    call_api({"files": [CSV], "outputs": ["summary"]})             # rejected: no call sign
    ping()

    at(monkeypatch, "2026-09-22T06:15:00")
    call_api({"files": [CABRILLO], "outputs": ["station_report"]})  # today: stays raw
    result = handler.handler({"action": "aggregate"})
    assert result == {"analyses": 4, "visits": 4}

    # Finished days were compacted and their raw events deleted; today's stay raw.
    assert sorted(p.name for p in (output_dir / "usage" / "daily").iterdir()) == \
        ["2026-09-20.json", "2026-09-21.json"]
    assert [p.name for p in (output_dir / "usage" / "raw").iterdir()] == ["2026-09-22"]

    stats = json.loads((output_dir / "stats" / "data.json").read_text())
    t = stats["totals"]
    assert (t["analyses"], t["ok"], t["inputErrors"], t["serverErrors"]) == (4, 3, 1, 0)
    assert (t["visits"], t["visitors"]) == (4, 3)   # 2 unique on the 20th, 1 on the 21st
    assert t["operators"] == 1 and t["logs"] == 3 and t["qsos"] == 21
    assert stats["firstDay"] == "2026-09-20"
    assert [d["date"] for d in stats["daily"]] == ["2026-09-20", "2026-09-21", "2026-09-22"]
    assert [d["analyses"] for d in stats["daily"]] == [1, 2, 1]
    assert stats["outputs"] == {"cabrillo": 1, "directional_viz": 1, "summary": 1, "station_report": 1}
    assert stats["formats"] == {"cabrillo": 2, "csv": 1}
    assert stats["errors"] == {"callsign": 1}
    assert stats["repeatOperators"] == {"1 analysis": 0, "2–3": 1, "4+": 0}
    assert dict(stats["grids"])["EN91"] == 3
    assert stats["years"] == {"2026": 3}
    assert stats["monthly"][0]["month"] == "2026-09"

    # Re-running is idempotent.
    handler.handler({"action": "aggregate"})
    again = json.loads((output_dir / "stats" / "data.json").read_text())
    assert again["totals"] == t


def test_aggregate_with_no_data(output_dir):
    import usage
    assert usage.aggregate() == {"analyses": 0, "visits": 0}
    stats = json.loads((output_dir / "stats" / "data.json").read_text())
    assert stats["totals"]["analyses"] == 0 and stats["firstDay"] is None
