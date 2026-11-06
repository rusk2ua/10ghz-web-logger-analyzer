import json
import time

import pytest

from conftest import fixture_text, get_job, local_file

CSV = {"name": "sample_qso_log.csv", "content": fixture_text("sample_qso_log.csv")}
CABRILLO = {"name": "sample_cabrillo.log", "content": fixture_text("sample_cabrillo.log")}


def submit(body):
    import handler
    result = handler.handler({"rawPath": "/api/process", "requestContext": {"http": {"method": "POST"}},
                              "body": json.dumps(body)})
    return result["statusCode"], json.loads(result["body"])


# ------------------------------------------------------------------ jobs

def test_job_lifecycle_inline(output_dir):
    status, body = submit({"files": [CABRILLO], "outputs": ["summary"]})
    assert status == 202 and len(body["jobId"]) == 32
    job = get_job(body["jobId"])
    assert job["state"] == "done" and job["statusCode"] == 200
    assert [f["name"] for f in job["result"]["files"]] == ["K2UA_ARRL_10GHZ_Summary.txt"]
    # The uploaded log isn't kept once the job has run.
    assert not (output_dir / "jobs" / body["jobId"] / "request.json").exists()


def test_job_runs_in_background(output_dir, monkeypatch):
    import handler
    started = []
    monkeypatch.setenv("JOB_MODE", "async")
    monkeypatch.setattr(handler, "_start_async", started.append)

    status, body = submit({"files": [CABRILLO], "outputs": ["summary"]})
    assert status == 202 and started == [body["jobId"]]
    assert get_job(body["jobId"])["state"] == "queued"

    handler.handler({"action": "run_job", "jobId": body["jobId"]})  # what the async invoke does
    assert get_job(body["jobId"])["state"] == "done"


def test_validation_errors_are_immediate(output_dir):
    status, body = submit({"files": [CSV], "callsign": "K2UA", "outputs": []})
    assert status == 400 and "at least one output" in body["message"]
    assert not (output_dir / "jobs").exists()


def test_errors_found_while_running_come_back_through_the_job(output_dir):
    status, body = submit({"files": [{"name": "empty.csv", "content": "date,band,sourcegrid,time,call,grid\n"}],
                           "callsign": "K2UA", "outputs": ["summary"]})
    assert status == 202
    job = get_job(body["jobId"])
    assert (job["state"], job["statusCode"]) == ("error", 400) and "no QSOs" in job["message"]


def test_failure_to_start_job(output_dir, monkeypatch):
    import handler
    monkeypatch.setenv("JOB_MODE", "async")
    monkeypatch.setattr(handler, "_start_async", lambda job_id: 1 / 0)
    status, body = submit({"files": [CABRILLO], "outputs": ["summary"]})
    assert status == 500 and not body["success"]


def test_stale_jobs_report_an_error(output_dir):
    import handler
    import storage
    store = storage.from_environment()
    job_id = "a" * 32
    store.put_json(f"jobs/{job_id}/status.json", {"state": "running", "updated": time.time() - 3600})
    job = get_job(job_id)
    assert job["state"] == "error" and "didn't finish" in job["message"]
    store.put_json(f"jobs/{job_id}/status.json", {"state": "queued", "updated": time.time() - 60})
    assert get_job(job_id)["state"] == "queued"


def test_unknown_and_malformed_job_ids(output_dir):
    import handler
    for job_id in ("b" * 32, "../../etc/passwd", "not-a-job"):
        result = handler.handler({"rawPath": f"/api/jobs/{job_id}",
                                  "requestContext": {"http": {"method": "GET"}}})
        assert result["statusCode"] == 404


# ------------------------------------------------------------------ grid-mapper maps

def test_path_maps_from_csv(call_api, output_dir):
    """A CSV log is converted to Cabrillo, then mapped by grid-mapper."""
    status, body = call_api({"files": [CSV], "callsign": "K2UA", "outputs": ["grid_paths"]})
    assert status == 200, body
    assert body["errors"] == [] and body["gridMapper"].startswith("v")
    names = sorted(f["name"] for f in body["files"])
    # One path map per band per operating location (the sample is a rover log).
    assert names == [
        "K2UA_10G_EN90UV_northeastern_north_america_grid_paths_map.png",
        "K2UA_10G_EN91KT_northeastern_north_america_grid_paths_map.png",
        "K2UA_24G_EN90UV_northeastern_north_america_grid_paths_map.png",
    ]
    for f in body["files"]:
        assert local_file(output_dir, f).read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert any("longest path" in n["text"] for n in body["notes"])


def test_density_and_html_maps_from_cabrillo(call_api, output_dir):
    status, body = call_api({"files": [CABRILLO], "outputs": ["grid_density", "grid_paths"],
                             "mapHtml": True})
    assert status == 200, body
    by_output = {}
    for f in body["files"]:
        by_output.setdefault(f["output"], []).append(f["name"])
    assert len(by_output["grid_density"]) == 6 and len(by_output["grid_paths"]) == 6
    html = [f for f in body["files"] if f["name"].endswith(".html")]
    assert len(html) == 6 and all(f["kind"] == "html" for f in html)
    assert "leaflet" in local_file(output_dir, html[0]).read_text().lower()


def test_map_options_become_grid_mapper_switches(call_api, monkeypatch):
    import runner
    calls = []
    real = runner.run_script

    def spy(module_name, argv, workdir, **kw):
        if module_name == "maidenhead_map":
            calls.append(argv[1:])
            return [], "", ""
        return real(module_name, argv, workdir, **kw)

    monkeypatch.setattr(runner, "run_script", spy)
    call_api({"files": [CABRILLO], "outputs": ["grid_density"], "mapOsm": True})
    call_api({"files": [CABRILLO], "outputs": ["grid_paths"], "mapHtml": True})
    call_api({"files": [CABRILLO], "outputs": ["grid_movie"], "mapHtml": True, "mapOsm": True})
    assert calls == [["--osm-basemap"], ["--paths", "--html"], ["--animate-only", "--osm-basemap"]]


def test_map_usage_is_recorded(call_api, output_dir):
    call_api({"files": [CABRILLO], "outputs": ["grid_paths"], "mapHtml": True})
    [rec] = [json.loads(p.read_text()) for p in (output_dir / "usage" / "raw").rglob("*.json")]
    assert rec["outputs"] == ["grid_paths"] and rec["mapHtml"] is True and rec["mapOsm"] is False
    assert rec["status"] == "ok" and rec["gridMapper"].startswith("v")


def test_street_underlay_applies_to_every_microwave_band(call_api, output_dir):
    # grid-mapper v1.5.1: the --osm-basemap underlay used to be skipped for
    # Cabrillo labels such as 5.7G and 134G. 2 m is below 902 MHz: no underlay.
    log = "\n".join(
        ["START-OF-LOG: 3.0", "CALLSIGN: K2UA", "CONTEST: ARRL-10-GHZ", "CATEGORY-BAND: ALL"]
        + [f"QSO: {band} PH 2026-08-15 12{i:02d} K2UA FN12ab {call} {grid}"
           for i, (band, call, grid) in enumerate(
               (b, c, g) for b in ("144", "5.7G", "134G")
               for c, g in (("W1AAA", "FN32ab"), ("W1BBB", "FN33ab")))]
        + ["END-OF-LOG:"])
    status, body = call_api({"files": [{"name": "mixed_bands.log", "content": log}],
                             "outputs": ["grid_density"], "mapHtml": True, "mapOsm": True})
    assert status == 200, body
    html = {f["name"]: local_file(output_dir, f).read_text()
            for f in body["files"] if f["name"].endswith(".html")}
    by_band = {band: text for name, text in html.items()
               for band in ("2m", "5.7G", "134G") if f"K2UA_{band}_" in name}
    assert set(by_band) >= {"5.7G", "134G"}, list(html)
    for band in ("5.7G", "134G"):
        assert '"osm": {"opacity"' in by_band[band], band
    assert "2m" in by_band, list(html)  # the 144 MHz QSOs are labelled 2m in the file name
    assert '"osm": null' in by_band["2m"]


# ------------------------------------------------------------------ contest movie

def _movie_data(path):
    import re
    return json.loads(re.search(r"const DATA = (.*?);\n", path.read_text(encoding="utf-8")).group(1))


@pytest.fixture
def no_background(monkeypatch):
    """Skip the Natural Earth 10m background (a large first-time download);
    the movie itself is what's under test here."""
    import runner
    monkeypatch.setattr(runner._module("maidenhead_map"), "_animation_background", lambda *a, **k: {})


def test_contest_movie_from_cabrillo(call_api, output_dir, no_background):
    status, body = call_api({"files": [CABRILLO], "outputs": ["grid_movie"]})
    assert status == 200, body
    assert body["errors"] == []
    [movie] = body["files"]
    assert movie["name"] == "K2UA_contest_animation.html"
    assert movie["kind"] == "movie" and movie["output"] == "grid_movie"
    page = local_file(output_dir, movie).read_text(encoding="utf-8")
    assert "unpkg.com" not in page and "Leaflet 1.9.4" in page    # bundled: a saved copy plays offline
    data = _movie_data(local_file(output_dir, movie))
    assert data["title"] == "K2UA \u00b7 ARRL 10 GHz and Up 2026"
    assert data["totals"]["qsos"] > 0 and data["totals"]["skipped"] == 0
    assert all(a["t"] < b["t"] for a, b in zip(data["events"], data["events"][1:]))


def test_contest_movie_from_csv(call_api, output_dir, no_background):
    """A CSV log is converted to Cabrillo first, keeping every QSO's own operating grid."""
    status, body = call_api({"files": [CSV], "callsign": "K2UA", "outputs": ["grid_movie", "grid_paths"]})
    assert status == 200, body
    movies = [f for f in body["files"] if f["output"] == "grid_movie"]
    assert [f["name"] for f in movies] == ["K2UA_contest_animation.html"]
    assert len([f for f in body["files"] if f["output"] == "grid_paths"]) == 3   # unaffected
    data = _movie_data(local_file(output_dir, movies[0]))
    # The sample rover log goes back to EN91KT on day 2: a revisit is a new stop.
    assert [s["grid"] for s in data["stops"]] == ["EN91KT", "EN90UV", "EN91KT"]
    assert [d["label"] for d in data["days"]] == ["Sat Aug 15", "Sun Aug 16"]


def test_movie_plays_in_the_browser_other_files_download():
    import storage
    assert storage.content_disposition("K2UA_contest_animation.html") == \
        'inline; filename="K2UA_contest_animation.html"'
    for name in ("K2UA_ARRL_10GHZ.log", "K2UA_10G_FN32KP_northeastern_grid_paths_map.html", "x.png"):
        assert storage.content_disposition(name).startswith("attachment; ")


def test_movie_usage_counts_as_a_map_analysis(call_api, output_dir, no_background):
    import usage
    call_api({"files": [CABRILLO], "outputs": ["grid_movie"], "mapOsm": True})
    usage.aggregate()
    stats = json.loads((output_dir / "stats" / "data.json").read_text())
    assert stats["outputs"].get("grid_movie") == 1
    assert stats["mapAnalyses"] == 1 and stats["mapOptions"]["OpenStreetMap underlay"] == 1


@pytest.mark.parametrize("log", [CABRILLO, CSV])
def test_movie_score_matches_the_analyzer(call_api, output_dir, no_background, log):
    """The movie's running score must end on the analyzer's claimed score."""
    import contextlib
    import io
    import runner
    status, body = call_api({"files": [log], "callsign": "K2UA", "outputs": ["grid_movie"]})
    assert status == 200, body
    [movie] = body["files"]
    data = _movie_data(local_file(output_dir, movie))

    path = output_dir / ("check_" + log["name"])
    path.write_text(log["content"], encoding="utf-8")
    cabrillo, data_source = runner._module("arrl_10ghz_cabrillo"), runner._module("data_source")
    with contextlib.redirect_stdout(io.StringIO()):
        loaded = data_source.load_source_or_exit(str(path), verbose=False)
        claimed = cabrillo.calculate_score(loaded[0] if isinstance(loaded, tuple) else loaded)
    assert data["scoring"]["total"] == data["events"][-1]["sc"] == claimed

