"""Lambda entry point: POST /api/process

Request body (JSON):
    {
      "files":        [{"name": "k2ua.csv", "content": "<file text>"}, ...],  # 1-4 logs
      "sheetsUrl":    "https://docs.google.com/spreadsheets/d/...",          # or this
      "callsign":     "K2UA",      # needed for CSV / Google Sheets logs
      "bandCategory": "AUTO",      # AUTO, 10G or ALL
      "outputs":      ["cabrillo", "summary", ...]
    }

Response: JSON with download URLs for every generated file plus a .zip of
everything, the processing notes each script printed, and per-output errors.
"""

import base64
import json
import logging
import os
import re
import shutil
import tempfile
import uuid
import zipfile

import requests

import runner
import storage

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
    }


def process(req, store, workdir):
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

    results = runner.run_outputs(sources, req["outputs"], out_dir, req["band_category"])

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
                    "kind": "image" if name.lower().endswith(".png") else "text",
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

    return {
        "success": bool(files),
        "upstream": runner.upstream_version().get("version", "unknown"),
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


def handler(event, context=None):
    method = (event.get("requestContext", {}).get("http", {}).get("method")
              or event.get("httpMethod") or "POST")
    if method == "OPTIONS":
        return {"statusCode": 204, "body": ""}
    if method != "POST":
        return _response(405, {"success": False, "message": "Use POST."})

    workdir = tempfile.mkdtemp(prefix="req-", dir=os.environ.get("WORK_DIR") or None)
    try:
        req = parse_request(event)
        store = storage.from_environment()
        return _response(200, process(req, store, workdir))
    except runner.InputError as e:
        return _response(400, {"success": False, "message": str(e)})
    except Exception:
        logger.exception("Unhandled error")
        return _response(500, {"success": False,
                               "message": "Something went wrong processing this log. "
                                          "Please try again, or report it if it keeps happening."})
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
