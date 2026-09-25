"""Lambda entry point.

  POST /api/process          validate a request and start a background job (202 + jobId)
  GET  /api/jobs/{jobId}     job state: queued, running, done (with result) or error
  POST /api/ping             count a home-page visit for the stats dashboard
  {"action": "run_job"}      (async self-invocation) run one analysis job
  {"action": "aggregate"}    (daily schedule) rebuild the stats dashboard data

Analyses can take minutes (grid-mapper maps for a rover log), longer than API
Gateway's 30 s limit, so /api/process only validates and queues. The Lambda
then invokes itself asynchronously to do the work, and the browser polls
/api/jobs/{jobId}. Job state lives in the results bucket under jobs/ (deleted
after a day). Locally (STORAGE_MODE=local) jobs run inline.

POST /api/process request body (JSON):
    {
      "files":        [{"name": "k2ua.csv", "content": "<file text>"}, ...],  # 1-4 logs
      "sheetsUrl":    "https://docs.google.com/spreadsheets/d/...",          # or this
      "callsign":     "K2UA",      # needed for CSV / Google Sheets logs
      "bandCategory": "AUTO",      # AUTO, 10G or ALL
      "outputs":      ["cabrillo", "summary", "grid_paths", ...],
      "mapHtml":      false,       # grid-mapper --html (interactive map files)
      "mapOsm":       false        # grid-mapper --osm-basemap (street underlay)
    }

A finished job's result: download URLs for every generated file plus a .zip of
everything, the processing notes each script printed, and per-output errors.
"""

import base64
import json
import logging
import os
import re
import shutil
import tempfile
import time
import uuid
import zipfile

import requests

import runner
import storage
import usage

logger = logging.getLogger()
logger.setLevel(logging.INFO)

MAX_BODY_BYTES = 3_000_000
MAX_FILE_BYTES = 1_000_000
MAX_FILES = 4
MAX_SHEET_BYTES = 2_000_000
# Upstream uses the call sign in output file names, so portable/rover
# suffixes like K2UA/R can't be used yet.
CALLSIGN_RE = re.compile(r"^(?=.*[0-9])(?=.*[A-Z])[A-Z0-9]{3,10}$")
SHEETS_RE = re.compile(r"^https://docs\.google\.com/spreadsheets/d/([A-Za-z0-9_-]{20,})")


def _response(status, payload):
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json", "Cache-Control": "no-store"},
        "body": json.dumps(payload),
    }


def _bad_callsign(callsign):
    if "/" in callsign:
        return (f"'{callsign}': call signs with a / suffix aren't supported yet -- "
                "enter the base call sign (e.g. K2UA).")
    return f"'{callsign}' doesn't look like a valid call sign."


def _safe_stem(name):
    stem = os.path.splitext(os.path.basename(str(name)))[0]
    stem = re.sub(r"[^A-Za-z0-9_-]+", "_", stem).strip("_")[:60]
    return stem or "log"


def _looks_like_cabrillo(text):
    return "START-OF-LOG" in text[:2000].upper() or any(
        line.lstrip().upper().startswith("QSO:") for line in text.splitlines()[:200]
    )


def _save_log(text, label, inputs_dir, index):
    """Write a log to disk with the extension the upstream loader keys off of:
    .log for Cabrillo, .csv for everything else."""
    ext = ".log" if _looks_like_cabrillo(text) else ".csv"
    if ext == ".csv" and "," not in text[:5000]:
        raise runner.InputError(
            f"{label}: this doesn't look like a Cabrillo log or a CSV file."
        )
    path = os.path.join(inputs_dir, f"{index}", _safe_stem(label) + ext)
    os.makedirs(os.path.dirname(path))
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    return path


def fetch_google_sheet(url):
    """Download a publicly shared Google Sheet as CSV text."""
    match = SHEETS_RE.match(url.strip())
    if not match:
        raise runner.InputError(
            "That doesn't look like a Google Sheets link "
            "(https://docs.google.com/spreadsheets/d/...)."
        )
    csv_url = f"https://docs.google.com/spreadsheets/d/{match.group(1)}/export?format=csv"
    try:
        resp = requests.get(csv_url, timeout=15, stream=True)
        resp.raise_for_status()
        body = b""
        for chunk in resp.iter_content(64 * 1024):
            body += chunk
            if len(body) > MAX_SHEET_BYTES:
                break
    except requests.RequestException as e:
        logger.warning("Google Sheet download failed: %s", e)
        raise runner.InputError(
            "Couldn't download that Google Sheet. Check the link, and that it's shared as "
            "'Anyone with the link can view'."
        ) from e
    if "text/csv" not in resp.headers.get("Content-Type", ""):
        raise runner.InputError(
            "Google didn't return CSV data. Make sure the sheet is shared as "
            "'Anyone with the link can view'."
        )
    if len(body) > MAX_SHEET_BYTES:
        raise runner.InputError("That Google Sheet is too large (limit 2 MB).")
    return body.decode("utf-8-sig", errors="replace")


def parse_request(event):
    body = event.get("body") or ""
    if event.get("isBase64Encoded"):
        body = base64.b64decode(body).decode("utf-8")
    if len(body) > MAX_BODY_BYTES:
        raise runner.InputError("Request is too large (limit about 3 MB in total).")
    try:
        req = json.loads(body)
    except json.JSONDecodeError as e:
        raise runner.InputError("Request body must be JSON.") from e
    if not isinstance(req, dict):
        raise runner.InputError("Request body must be a JSON object.")

    outputs = req.get("outputs") or []
    if not isinstance(outputs, list) or not outputs:
        raise runner.InputError("Choose at least one output.")
    unknown = [o for o in outputs if o not in runner.ALL_OUTPUTS]
    if unknown:
        raise runner.InputError(f"Unknown output(s): {', '.join(map(str, unknown))}.")

    callsign = str(req.get("callsign") or "").strip().upper()
    if callsign and not CALLSIGN_RE.match(callsign):
        raise runner.InputError(_bad_callsign(callsign))

    band_category = str(req.get("bandCategory") or "AUTO").upper()
    if band_category not in ("AUTO", "10G", "ALL"):
        raise runner.InputError("Band category must be AUTO, 10G or ALL.")

    files = req.get("files") or []
    sheets_url = str(req.get("sheetsUrl") or "").strip()
    if not isinstance(files, list):
        raise runner.InputError("'files' must be a list.")
    if not files and not sheets_url:
        raise runner.InputError("Upload a log file or enter a Google Sheets link.")
    if len(files) > MAX_FILES:
        raise runner.InputError(f"Upload at most {MAX_FILES} logs at a time.")
    for f in files:
        if not isinstance(f, dict) or not isinstance(f.get("content"), str):
            raise runner.InputError("Each file needs a name and text content.")
        if len(f["content"].encode("utf-8")) > MAX_FILE_BYTES:
            raise runner.InputError(f"{f.get('name', 'file')}: too large (limit 1 MB per log).")

    return {
        "files": files,
        "sheets_url": sheets_url,
        "callsign": callsign,
        "band_category": band_category,
        "outputs": list(dict.fromkeys(outputs)),
        "map_options": {"html": bool(req.get("mapHtml")), "osm": bool(req.get("mapOsm"))},
    }


def _file_kind(name):
    ext = os.path.splitext(name)[1].lower()
    return {".png": "image", ".html": "html"}.get(ext, "text")


def process(req, store, workdir, record):
    inputs_dir = os.path.join(workdir, "inputs")
    out_dir = os.path.join(workdir, "outputs")
    os.makedirs(inputs_dir)

    raw = [(str(f.get("name") or f"log{i + 1}"), f["content"]) for i, f in enumerate(req["files"])]
    if req["sheets_url"] and len(raw) < MAX_FILES:
        raw.append(("Google Sheet", fetch_google_sheet(req["sheets_url"])))

    sources = []
    for i, (label, text) in enumerate(raw):
        path = _save_log(text, label, inputs_dir, i)
        sources.append(runner.inspect_source(path, label, req["callsign"]))
    record["logs"] = [{
        "format": "cabrillo" if s.path.endswith(".log") else "csv",
        "qsos": s.qsos,
        "bands": s.bands,
        "year": int(s.first_date[:4]) if s.first_date[:4].isdigit() else None,
        "grids": s.grids,
        "op": usage.operator_id(s.callsign),
    } for s in sources]

    for s in sources:
        if s.callsign != "UNKNOWN" and not CALLSIGN_RE.match(s.callsign):
            raise runner.InputError(f"{s.label}: {_bad_callsign(s.callsign)}")

    single_log_outputs = [o for o in req["outputs"] if o in runner.SINGLE_LOG_OUTPUTS]
    missing_call = [s.label for s in sources if s.callsign == "UNKNOWN"]
    if single_log_outputs and missing_call:
        raise runner.InputError(
            "Enter your call sign -- CSV and Google Sheets logs don't include one "
            f"({', '.join(missing_call)})."
        )

    results = runner.run_outputs(sources, req["outputs"], out_dir, req["band_category"],
                                 req.get("map_options"))

    request_id = uuid.uuid4().hex
    prefix = f"results/{request_id}/"
    files, notes, errors, used_names = [], [], [], set()
    zip_path = os.path.join(workdir, "all-results.zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for result in results:
            if result.error:
                errors.append({"output": result.output, "label": runner.OUTPUT_LABELS[result.output],
                               "source": result.source, "message": result.error})
            text = result.notes.strip()
            for s in sources:  # show "k2ua.csv", not /tmp/req-.../inputs/0/k2ua.csv
                text = text.replace(s.path, s.label)
            if text and not any(n["source"] == result.source and n["text"] == text for n in notes):
                notes.append({"output": result.output, "label": runner.OUTPUT_LABELS[result.output],
                              "source": result.source, "text": text})
            for path in result.files:
                name = os.path.basename(path)
                if name in used_names:  # same file from a shared script run, or a name clash
                    if any(f["path"] == path for f in files):
                        continue
                    name = f"{_safe_stem(result.source)}_{name}"
                used_names.add(name)
                archive.write(path, name)
                files.append({
                    "path": path,
                    "name": name,
                    "output": result.output,
                    "label": runner.OUTPUT_LABELS[result.output],
                    "source": result.source,
                    "kind": _file_kind(name),
                    "size": os.path.getsize(path),
                    "url": store.save(path, prefix + name),
                })
        if notes:
            archive.writestr("Processing_Notes.txt", "\n\n".join(
                f"== {n['label']} ({n['source']}) ==\n{n['text']}" for n in notes))

    archive_info = None
    if files:
        call = sources[0].callsign if sources[0].callsign != "UNKNOWN" else "contest"
        zip_name = f"{call}_10GHz_results.zip"
        archive_info = {"name": zip_name, "url": store.save(zip_path, prefix + zip_name)}

    for f in files:
        del f["path"]
    record["files"] = len(files)
    record["failedOutputs"] = sorted({e["output"] for e in errors})
    record["status"] = "ok" if files and not errors else ("partial" if files else "input_error")
    if not files:
        record["error"] = "no_output"

    return {
        "success": bool(files),
        "upstream": runner.upstream_version().get("version", "unknown"),
        "gridMapper": runner.upstream_version(runner.GRIDMAPPER_DIR).get("version", "unknown"),
        "logs": [{
            "source": s.label, "callsign": s.callsign, "qsos": s.qsos, "bands": s.bands,
            "firstDate": s.first_date, "lastDate": s.last_date, "bandCategory": s.band_category,
        } for s in sources],
        "files": files,
        "archive": archive_info,
        "notes": notes,
        "errors": errors,
        "message": "" if files else "No files were generated -- see the errors below.",
    }


# ------------------------------------------------------------------ background jobs
JOB_ID_RE = re.compile(r"^[0-9a-f]{32}$")
# A job still "running" this long after it started has died (the Lambda timeout
# is 900 s); one still "queued" this long never started.
RUNNING_STALE_SECONDS = 960
QUEUED_STALE_SECONDS = 1800
SERVER_ERROR_MESSAGE = ("Something went wrong processing this log. "
                        "Please try again, or report it if it keeps happening.")


def _job_key(job_id, name):
    return f"jobs/{job_id}/{name}.json"


def _set_status(store, job_id, state, **fields):
    store.put_json(_job_key(job_id, "status"), {"state": state, "updated": time.time(), **fields})


def _new_record():
    return {"upstream": runner.upstream_version().get("version"),
            "gridMapper": runner.upstream_version(runner.GRIDMAPPER_DIR).get("version")}


def _start_async(job_id):
    import boto3
    boto3.client("lambda").invoke(
        FunctionName=os.environ["AWS_LAMBDA_FUNCTION_NAME"],
        InvocationType="Event",
        Payload=json.dumps({"action": "run_job", "jobId": job_id}).encode(),
    )


def submit(event):
    """POST /api/process: validate now (so mistakes come back instantly), then
    queue the analysis as a background job."""
    started = time.monotonic()
    record = _new_record()
    try:
        req = parse_request(event)
    except runner.InputError as e:
        record.update(status="input_error", error=usage.error_code(e),
                      duration_ms=int((time.monotonic() - started) * 1000))
        usage.record_analysis(record)
        return _response(400, {"success": False, "message": str(e)})

    record.update(
        source="sheets" if req["sheets_url"] else "files",
        nLogs=len(req["files"]) + (1 if req["sheets_url"] else 0),
        outputs=req["outputs"],
        bandCategory=req["band_category"],
        mapHtml=req["map_options"]["html"],
        mapOsm=req["map_options"]["osm"],
    )
    job_id = uuid.uuid4().hex
    store = storage.from_environment()
    store.put_json(_job_key(job_id, "request"), {"req": req, "record": record})
    _set_status(store, job_id, "queued")
    mode = os.environ.get("JOB_MODE") or ("inline" if os.environ.get("STORAGE_MODE") == "local" else "async")
    try:
        if mode == "inline":
            run_job(job_id)
        else:
            _start_async(job_id)
    except Exception:
        logger.exception("Could not start job %s", job_id)
        _set_status(store, job_id, "error", statusCode=500, message=SERVER_ERROR_MESSAGE)
        return _response(500, {"success": False, "message": SERVER_ERROR_MESSAGE})
    return _response(202, {"success": True, "jobId": job_id})


def run_job(job_id):
    """Run one queued analysis (async self-invocation). Writes the outcome to
    the job's status document and records anonymous usage."""
    store = storage.from_environment()
    job = store.get_json(_job_key(job_id, "request"))
    req, record = job["req"], job["record"]
    _set_status(store, job_id, "running")
    started = time.monotonic()
    workdir = tempfile.mkdtemp(prefix="req-", dir=os.environ.get("WORK_DIR") or None)
    try:
        result = process(req, store, workdir, record)
        _set_status(store, job_id, "done", statusCode=200, result=result)
    except runner.InputError as e:
        record.update(status="input_error", error=usage.error_code(e))
        _set_status(store, job_id, "error", statusCode=400, message=str(e))
    except Exception:
        logger.exception("Job %s failed", job_id)
        record.update(status="server_error", error="server_error")
        _set_status(store, job_id, "error", statusCode=500, message=SERVER_ERROR_MESSAGE)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
        store.delete(_job_key(job_id, "request"))  # the uploaded logs aren't kept
        record["duration_ms"] = int((time.monotonic() - started) * 1000)
        usage.record_analysis(record)
    return {"jobId": job_id}


def job_status(job_id):
    """GET /api/jobs/{jobId}."""
    if not JOB_ID_RE.match(job_id):
        return _response(404, {"state": "error", "message": "Unknown job."})
    try:
        status = storage.from_environment().get_json(_job_key(job_id, "status"))
    except KeyError:
        return _response(404, {"state": "error", "message": "Unknown or expired job."})
    age = time.time() - status.get("updated", 0)
    if (status["state"] == "running" and age > RUNNING_STALE_SECONDS) or \
            (status["state"] == "queued" and age > QUEUED_STALE_SECONDS):
        status = {"state": "error", "statusCode": 500,
                  "message": "This analysis didn't finish. Please try again with fewer outputs, "
                             "or report it if it keeps happening."}
    return _response(200, status)


def handler(event, context=None):
    action = event.get("action")
    if action == "aggregate":  # daily schedule
        return usage.aggregate()
    if action == "run_job":  # async self-invocation
        return run_job(event["jobId"])

    path = event.get("rawPath") or event.get("path") or ""
    method = (event.get("requestContext", {}).get("http", {}).get("method")
              or event.get("httpMethod") or "POST")
    if method == "OPTIONS":
        return {"statusCode": 204, "body": ""}
    if method == "GET" and path.startswith("/api/jobs/"):
        return job_status(path.rsplit("/", 1)[-1])
    if method != "POST":
        return _response(405, {"success": False, "message": "Use POST."})
    if path.endswith("/api/ping"):
        return usage.ping(event)
    return submit(event)
