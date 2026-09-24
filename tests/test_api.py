import os
import re
import subprocess
import sys
import zipfile

import pytest

from conftest import APP_DIR, FIXTURES, fixture_text, local_file

CSV = {"name": "sample_qso_log.csv", "content": fixture_text("sample_qso_log.csv")}
CABRILLO = {"name": "sample_cabrillo.log", "content": fixture_text("sample_cabrillo.log")}
ALL_SINGLE = ["cabrillo", "summary", "station_report", "weekend_analysis",
              "comprehensive_analysis", "directional_viz", "directional_location"]


def names(body):
    return sorted(f["name"] for f in body["files"])


# ------------------------------------------------------------------ happy paths

def test_csv_log_all_outputs(call_api, output_dir):
    status, body = call_api({"files": [CSV], "callsign": "k2ua", "outputs": ALL_SINGLE})
    assert status == 200, body
    assert body["success"] and body["errors"] == []
    assert body["upstream"].startswith("v")
    assert names(body) == [
        "K2UA_ARRL_10GHZ.log",
        "K2UA_ARRL_10GHZ_Summary.txt",
        "K2UA_Comprehensive_Analysis_20260816.txt",
        "K2UA_Directional_Analysis_Day_1_20260816.png",
        "K2UA_Directional_Analysis_Day_2_20260816.png",
        "K2UA_EN90uv_direction_analysis_2026-08-15.png",
        "K2UA_EN91kt_direction_analysis_2026-08-15.png",
        "K2UA_EN91kt_direction_analysis_2026-08-16.png",
        "K2UA_Station_Report_20260816.txt",
        "K2UA_Weekend_Analysis_20260816.txt",
    ]
    log = body["logs"][0]
    assert (log["callsign"], log["qsos"], log["bands"]) == ("K2UA", 7, ["10 GHz", "24 GHz"])
    assert log["bandCategory"] == "ALL"

    for f in body["files"]:
        path = local_file(output_dir, f)
        assert path.stat().st_size == f["size"] > 0
        if f["kind"] == "image":
            assert path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"

    cabrillo = local_file(output_dir, next(f for f in body["files"] if f["output"] == "cabrillo")).read_text()
    summary = local_file(output_dir, next(f for f in body["files"] if f["output"] == "summary")).read_text()
    assert "CALLSIGN: K2UA" in cabrillo and "CATEGORY-BAND: ALL" in cabrillo
    assert cabrillo.count("\nQSO: ") == 7
    claimed = re.search(r"CLAIMED-SCORE: (\d+)", cabrillo).group(1)
    assert re.search(r"Score\s+(\d+)", summary).group(1) == claimed

    archive = zipfile.ZipFile(local_file(output_dir, body["archive"]))
    assert sorted(archive.namelist()) == sorted(names(body) + ["Processing_Notes.txt"])
    # Notes should mention the upload by name, never the server's temp path.
    assert body["notes"] and not any("/tmp" in n["text"] or "req-" in n["text"] for n in body["notes"])


def test_cabrillo_log_uses_header_callsign(call_api):
    status, body = call_api({"files": [CABRILLO], "outputs": ["station_report", "summary"]})
    assert status == 200, body
    assert body["logs"][0]["callsign"] == "K2UA"
    assert names(body) == ["K2UA_ARRL_10GHZ_Summary.txt", "K2UA_Station_Report_20260816.txt"]


def test_auto_band_category_10g_only(call_api, output_dir):
    ten_only = "\n".join(line for line in CSV["content"].splitlines() if "24 GHz" not in line)
    status, body = call_api({"files": [{"name": "ten.csv", "content": ten_only}],
                             "callsign": "W1AW", "outputs": ["cabrillo"]})
    assert status == 200, body
    assert body["logs"][0]["bandCategory"] == "10G"
    assert "CATEGORY-BAND: 10G" in local_file(output_dir, body["files"][0]).read_text()


def test_band_category_override(call_api, output_dir):
    status, body = call_api({"files": [CSV], "callsign": "K2UA", "bandCategory": "10G",
                             "outputs": ["cabrillo"]})
    assert status == 200
    assert "CATEGORY-BAND: 10G" in local_file(output_dir, body["files"][0]).read_text()


def test_comparison_of_two_logs(call_api):
    status, body = call_api({"files": [CSV, CABRILLO], "callsign": "K2UA", "outputs": ["comparison"]})
    assert status == 200, body
    assert [f["output"] for f in body["files"]] == ["comparison"]
    assert body["files"][0]["name"].startswith("Log_Comparison_")


def test_comparison_needs_two_logs(call_api):
    status, body = call_api({"files": [CABRILLO], "outputs": ["summary", "comparison"]})
    assert status == 200
    assert [e["output"] for e in body["errors"]] == ["comparison"]
    assert names(body) == ["K2UA_ARRL_10GHZ_Summary.txt"]


def test_same_callsign_in_two_logs_gets_unique_names(call_api):
    status, body = call_api({"files": [CSV, CABRILLO], "callsign": "K2UA", "outputs": ["summary"]})
    assert status == 200
    assert len(set(names(body))) == 2


def test_web_output_matches_cli(call_api, output_dir, tmp_path):
    """The whole point of vendoring: the web app must produce exactly what the
    CLI produces for the same log."""
    status, body = call_api({"files": [CSV], "callsign": "K2UA", "bandCategory": "ALL",
                             "outputs": ["cabrillo", "summary", "station_report"]})
    assert status == 200

    cli_dir = tmp_path / "cli"
    cli_dir.mkdir()
    env = dict(os.environ, MPLBACKEND="Agg")
    analyzer = os.path.join(APP_DIR, "analyzer")
    source = os.path.join(FIXTURES, "sample_qso_log.csv")
    subprocess.run([sys.executable, os.path.join(analyzer, "arrl_10ghz_cabrillo.py"), source],
                   input="K2UA\nALL\n", text=True, cwd=cli_dir, env=env, check=True, capture_output=True)
    subprocess.run([sys.executable, os.path.join(analyzer, "station_report.py"), source],
                   text=True, cwd=cli_dir, env=env, check=True, capture_output=True)

    web = {f["name"]: local_file(output_dir, f).read_text() for f in body["files"]}
    assert web["K2UA_ARRL_10GHZ.log"] == (cli_dir / "K2UA_ARRL_10GHZ.log").read_text()
    assert web["K2UA_ARRL_10GHZ_Summary.txt"] == (cli_dir / "K2UA_ARRL_10GHZ_Summary.txt").read_text()
    # The CLI names CSV-sourced reports UNKNOWN_...; the web app uses the form call sign.
    assert web["K2UA_Station_Report_20260816.txt"] == (cli_dir / "UNKNOWN_Station_Report_20260816.txt").read_text()


# ------------------------------------------------------------------ Google Sheets

class FakeResponse:
    def __init__(self, text, content_type):
        self.headers = {"Content-Type": content_type}
        self.content = text.encode()

    def raise_for_status(self):
        pass

    def iter_content(self, size):
        for i in range(0, len(self.content), size):
            yield self.content[i:i + size]


SHEET_URL = "https://docs.google.com/spreadsheets/d/1UFbxzWJBpPdUEkfLhNA6csKbHaNypDmGeWpaeP-bQyA/edit?usp=sharing"


def test_google_sheet(call_api, monkeypatch):
    import handler
    seen = {}

    def fake_get(url, **kwargs):
        seen["url"] = url
        return FakeResponse(CSV["content"], "text/csv; charset=utf-8")

    monkeypatch.setattr(handler.requests, "get", fake_get)
    status, body = call_api({"sheetsUrl": SHEET_URL, "callsign": "K2UA", "outputs": ["summary"]})
    assert status == 200, body
    assert seen["url"].endswith("/export?format=csv")
    assert body["logs"][0]["qsos"] == 7


def test_google_sheet_not_shared(call_api, monkeypatch):
    import handler
    monkeypatch.setattr(handler.requests, "get",
                        lambda url, **kw: FakeResponse("<html>Sign in</html>", "text/html"))
    status, body = call_api({"sheetsUrl": SHEET_URL, "callsign": "K2UA", "outputs": ["summary"]})
    assert status == 400
    assert "Anyone with the link" in body["message"]


# ------------------------------------------------------------------ validation

@pytest.mark.parametrize("request_body, message", [
    ({"files": [CSV], "callsign": "K2UA", "outputs": []}, "at least one output"),
    ({"files": [CSV], "callsign": "K2UA", "outputs": ["rm -rf"]}, "Unknown output"),
    ({"files": [CSV], "outputs": ["summary"]}, "Enter your call sign"),
    ({"files": [CSV], "callsign": "K2UA/R", "outputs": ["summary"]}, "/ suffix"),
    ({"files": [CSV], "callsign": "HELLO", "outputs": ["summary"]}, "valid call sign"),
    ({"files": [CSV], "callsign": "K2UA", "bandCategory": "24G", "outputs": ["summary"]}, "Band category"),
    ({"outputs": ["summary"]}, "Upload a log"),
    ({"files": [CSV] * 5, "callsign": "K2UA", "outputs": ["summary"]}, "at most 4"),
    ({"files": [{"name": "big.csv", "content": "x" * 1_000_001}], "outputs": ["summary"]}, "too large"),
    ({"files": [{"name": "notes.txt", "content": "just some text"}], "outputs": ["summary"]}, "doesn't look like"),
    ({"files": [{"name": "empty.csv", "content": "date,band,sourcegrid,time,call,grid\n"}],
      "callsign": "K2UA", "outputs": ["summary"]}, "no QSOs"),
    ({"sheetsUrl": "https://evil.example.com/spreadsheets/d/abc", "callsign": "K2UA",
      "outputs": ["summary"]}, "Google Sheets link"),
])
def test_rejects_bad_input(call_api, request_body, message):
    status, body = call_api(request_body)
    assert status == 400, body
    assert message in body["message"]


def test_non_json_body(output_dir):
    import handler
    result = handler.handler({"requestContext": {"http": {"method": "POST"}}, "body": "not json"})
    assert result["statusCode"] == 400


def test_get_not_allowed(output_dir):
    import handler
    assert handler.handler({"requestContext": {"http": {"method": "GET"}}})["statusCode"] == 405


def test_temp_files_cleaned_up(call_api, tmp_path, monkeypatch):
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.setenv("WORK_DIR", str(work))
    call_api({"files": [CSV], "callsign": "K2UA", "outputs": ["summary"]})
    call_api({"files": [CSV], "outputs": ["summary"]})  # a 400
    assert list(work.iterdir()) == []
