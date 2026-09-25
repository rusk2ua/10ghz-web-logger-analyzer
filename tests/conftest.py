import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_DIR = os.path.join(ROOT, "backend", "app")
FIXTURES = os.path.join(ROOT, "tests", "fixtures")
sys.path.insert(0, APP_DIR)


def fixture_text(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
        return f.read()


@pytest.fixture
def output_dir(tmp_path, monkeypatch):
    out = tmp_path / "output"
    monkeypatch.setenv("STORAGE_MODE", "local")
    monkeypatch.setenv("LOCAL_OUTPUT_DIR", str(out))
    return out


@pytest.fixture
def call_api(output_dir):
    """Submit an analysis like the browser does: POST /api/process, then (for a
    queued job -- run inline in local mode) GET /api/jobs/{id}. Returns
    (status, body) where body is the finished job's result, or its error."""
    import handler

    def _call(body):
        event = {"rawPath": "/api/process", "requestContext": {"http": {"method": "POST"}},
                 "body": json.dumps(body)}
        submitted = handler.handler(event)
        payload = json.loads(submitted["body"])
        if submitted["statusCode"] != 202:
            return submitted["statusCode"], payload
        job = get_job(payload["jobId"])
        assert job["state"] in ("done", "error"), job
        if job["state"] == "done":
            return job["statusCode"], job["result"]
        return job["statusCode"], {"success": False, "message": job["message"]}

    return _call


def get_job(job_id):
    import handler
    result = handler.handler({"rawPath": f"/api/jobs/{job_id}",
                              "requestContext": {"http": {"method": "GET"}}})
    return json.loads(result["body"])


def local_file(output_dir, file_entry):
    """Path of a generated file in local storage mode (url is /output/<key>)."""
    return output_dir / file_entry["url"][len("/output/"):]
