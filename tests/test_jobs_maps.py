import json
import time

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
    assert calls == [["--osm-basemap"], ["--paths", "--html"]]


def test_map_usage_is_recorded(call_api, output_dir):
    call_api({"files": [CABRILLO], "outputs": ["grid_paths"], "mapHtml": True})
    [rec] = [json.loads(p.read_text()) for p in (output_dir / "usage" / "raw").rglob("*.json")]
    assert rec["outputs"] == ["grid_paths"] and rec["mapHtml"] is True and rec["mapOsm"] is False
    assert rec["status"] == "ok" and rec["gridMapper"].startswith("v")
