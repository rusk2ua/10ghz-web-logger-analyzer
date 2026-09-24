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
    """POST a JSON body to the Lambda handler; returns (status, body, output_dir)."""
    import handler

    def _call(body):
        event = {"requestContext": {"http": {"method": "POST"}}, "body": json.dumps(body)}
        result = handler.handler(event)
        return result["statusCode"], json.loads(result["body"])

    return _call


def local_file(output_dir, file_entry):
    """Path of a generated file in local storage mode (url is /output/<key>)."""
    return output_dir / file_entry["url"][len("/output/"):]
