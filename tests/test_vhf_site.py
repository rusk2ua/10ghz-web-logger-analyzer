"""The VHF site (SITE_MODE=vhf): ARRL January/June/September VHF contests,
contest detection, rover categories, ADIF logs and non-contest logs."""
import json

import pytest

from conftest import fixture_text, local_file

ROVER = {"name": "sample_vhf_rover.log", "content": fixture_text("sample_vhf_rover.log")}
ROVER_ADIF = {"name": "sample_vhf_rover.adi", "content": fixture_text("sample_vhf_rover.adi")}
TEN_GHZ = {"name": "sample_cabrillo.log", "content": fixture_text("sample_cabrillo.log")}


@pytest.fixture
def vhf_site(monkeypatch):
    import runner
    monkeypatch.setattr(runner, "SITE_MODE", "vhf")
    # The movie, not its Natural Earth background (a large first-time download), is under test.
    monkeypatch.setattr(runner._module("maidenhead_map"), "_animation_background", lambda *a, **k: {})


def _movie_data(path):
    import re
    return json.loads(re.search(r"const DATA = (.*?);\n", path.read_text(encoding="utf-8")).group(1))


@pytest.mark.parametrize("log", [ROVER, ROVER_ADIF], ids=["cabrillo", "adif"])
def test_rover_log_is_detected_and_scored(vhf_site, call_api, output_dir, log):
    status, body = call_api({"files": [log], "outputs": ["cabrillo", "summary", "grid_movie"]})
    assert status == 200, body
    assert body["site"] == "vhf"
    info = body["logs"][0]
    assert (info["callsign"], info["contest"], info["category"]) == \
        ("K2UA/R", "ARRL June VHF Contest", "Classic Rover")
    names = sorted(f["name"] for f in body["files"])
    assert names == ["K2UA-R_ARRL_VHF_JUN.log", "K2UA-R_ARRL_VHF_JUN_Summary.txt",
                     "K2UA-R_contest_animation.html"]
    summary = next(f for f in body["files"] if f["name"].endswith("_Summary.txt"))
    assert "Score\t\t9,890" in local_file(output_dir, summary).read_text()
    movie = next(f for f in body["files"] if f["kind"] == "movie")
    data = _movie_data(local_file(output_dir, movie))
    assert data["scoring"] == {"rules": "ARRL June VHF", "total": 9890}
    assert [a["grid"] for a in data["activated"]] == ["FN12", "FN13", "FN03"]
    assert body["archive"]["name"] == "K2UA-R_VHF_results.zip"


def test_contest_and_category_can_be_chosen(vhf_site, call_api, output_dir):
    status, body = call_api({"files": [ROVER], "outputs": ["summary", "grid_movie"],
                             "contest": "ARRL-VHF-SEP", "category": "RL"})
    assert status == 200, body
    assert body["logs"][0]["contest"] == "ARRL September VHF Contest"
    assert body["logs"][0]["category"] == "Limited Rover"
    summary = next(f for f in body["files"] if f["name"] == "K2UA-R_ARRL_VHF_SEP_Summary.txt")
    text = local_file(output_dir, summary).read_text()
    # The June QSOs fall outside the September weekend, so nothing counts.
    assert "Score\t\t0" in text and "outside the contest period" in text
    movie = next(f for f in body["files"] if f["kind"] == "movie")
    assert _movie_data(local_file(output_dir, movie))["scoring"]["total"] == 0


def test_no_contest_maps_and_movie_without_a_score(vhf_site, call_api, output_dir):
    status, body = call_api({"files": [ROVER_ADIF], "outputs": ["summary", "grid_movie"],
                             "contest": "NONE"})
    assert status == 200, body
    assert body["logs"][0]["contest"] == "No contest"
    assert [e["output"] for e in body["errors"]] == ["summary"]
    assert "Choose a contest" in body["errors"][0]["message"]
    movie = next(f for f in body["files"] if f["kind"] == "movie")
    data = _movie_data(local_file(output_dir, movie))
    assert data["scoring"] is None and len(data["activated"]) == 3


def test_undetected_contest_explains_itself(vhf_site, call_api):
    log = {"name": "home.csv", "content": "date,band,sourcegrid,time,call,grid\n"
                                         "2026-03-03,144,FN12,1200,W2AAA,FN13\n"}
    status, body = call_api({"files": [log], "callsign": "K2UA", "outputs": ["cabrillo", "grid_movie"]})
    assert status == 200, body
    assert body["logs"][0]["contest"] == "No contest"
    assert "No contest was detected" in body["errors"][0]["message"]
    assert any(f["kind"] == "movie" for f in body["files"])


def test_ten_ghz_logs_still_score_on_the_vhf_site(vhf_site, call_api, output_dir):
    status, body = call_api({"files": [TEN_GHZ], "outputs": ["summary"]})
    assert status == 200, body
    assert body["logs"][0]["contest"] == "ARRL 10 GHz and Up Contest"
    assert [f["name"] for f in body["files"]] == ["K2UA_ARRL_10GHZ_Summary.txt"]


def test_ten_ghz_only_outputs_are_refused(vhf_site, call_api):
    status, body = call_api({"files": [ROVER], "outputs": ["weekend_analysis"]})
    assert status == 400 and "only available on the 10 GHz and Up site" in body["message"]


@pytest.mark.parametrize("field, value", [("contest", "CQ-WW"), ("category", "QRP")])
def test_bad_contest_or_category(vhf_site, call_api, field, value):
    status, body = call_api({"files": [ROVER], "outputs": ["summary"], field: value})
    assert status == 400 and "Unknown" in body["message"]


def test_ten_ghz_site_takes_adif_and_rover_calls(call_api, output_dir):
    status, body = call_api({"files": [ROVER_ADIF], "outputs": ["summary", "station_report"]})
    assert status == 200, body
    assert body["site"] == "10ghz" and "contest" not in body["logs"][0]
    names = sorted(f["name"] for f in body["files"])
    assert names[0] == "K2UA-R_ARRL_10GHZ_Summary.txt"
    assert body["logs"][0]["format"] == "adif"


@pytest.mark.parametrize("call, ok", [("K2UA/R", True), ("VE3/K2UA", True), ("K2UA/QRP", True),
                                      ("K2UA/R/R/R", False), ("R/R", False), ("K2UA//R", False)])
def test_callsign_pattern(call, ok):
    import handler
    assert bool(handler.CALLSIGN_RE.match(call)) is ok


def test_adif_without_my_grid_is_explained(call_api):
    log = {"name": "x.adi", "content": "<EOH>\n<CALL:5>W2AAA <QSO_DATE:8>20260613 <TIME_ON:4>1800 "
                                        "<BAND:2>2m <GRIDSQUARE:4>FN13 <EOR>\n"}
    status, body = call_api({"files": [log], "callsign": "K2UA", "outputs": ["summary"]})
    assert status == 400 and "MY_GRIDSQUARE" in body["message"]
