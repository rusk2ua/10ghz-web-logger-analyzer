"""Anonymous usage statistics for the owner's dashboard (/stats/).

What is recorded -- never any log contents:
  * one record per analysis request: outputs chosen, input type/format,
    QSO counts, bands, contest year, 4-character operating grids, duration,
    outcome, and an anonymous operator id (a salted one-way hash of the call
    sign, used only to count unique and repeat operators)
  * one record per home-page view (POST /api/ping): an anonymous visitor id
    that is a salted hash of that day's IP + browser, so it can't be linked
    across days

Storage layout (the private results bucket, or LOCAL_OUTPUT_DIR locally):
  usage/raw/YYYY-MM-DD/<kind>-<time>-<id>.json   one object per event (today)
  usage/daily/YYYY-MM-DD.json                    that day's events, compacted

aggregate() runs once a day: it folds each finished day's raw events into
its daily file (then deletes them, so object counts stay small), and writes
the dashboard's stats/data.json from all daily files plus today's raw events.
"""

import hashlib
import hmac
import json
import logging
import os
import statistics
import uuid
from collections import Counter
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

RAW = "usage/raw/"
DAILY = "usage/daily/"
STATS_KEY = "stats/data.json"
DAILY_DAYS_SHOWN = 180
BOT_MARKERS = ("bot", "spider", "crawl", "slurp", "headless", "preview", "curl", "python", "wget")


# ------------------------------------------------------------------ storage
class _S3:
    def __init__(self, bucket, website_bucket):
        import boto3
        self.s3 = boto3.client("s3")
        self.bucket, self.website_bucket = bucket, website_bucket

    def put(self, key, obj):
        self.s3.put_object(Bucket=self.bucket, Key=key, Body=json.dumps(obj).encode(),
                           ContentType="application/json")

    def get(self, key):
        return json.loads(self.s3.get_object(Bucket=self.bucket, Key=key)["Body"].read())

    def list(self, prefix):
        keys = []
        for page in self.s3.get_paginator("list_objects_v2").paginate(Bucket=self.bucket, Prefix=prefix):
            keys += [o["Key"] for o in page.get("Contents", [])]
        return keys

    def delete(self, keys):
        for i in range(0, len(keys), 1000):
            self.s3.delete_objects(Bucket=self.bucket, Delete={
                "Objects": [{"Key": k} for k in keys[i:i + 1000]], "Quiet": True})

    def publish(self, obj):
        self.s3.put_object(Bucket=self.website_bucket, Key=STATS_KEY, Body=json.dumps(obj).encode(),
                           ContentType="application/json", CacheControl="max-age=300")


class _Local:
    def __init__(self, root):
        self.root = root

    def _path(self, key):
        return os.path.join(self.root, key)

    def put(self, key, obj):
        os.makedirs(os.path.dirname(self._path(key)), exist_ok=True)
        with open(self._path(key), "w") as f:
            json.dump(obj, f)

    def get(self, key):
        with open(self._path(key)) as f:
            return json.load(f)

    def list(self, prefix):
        base = self._path(prefix)
        keys = []
        for dirpath, _, files in os.walk(base):
            keys += [os.path.relpath(os.path.join(dirpath, f), self.root).replace(os.sep, "/")
                     for f in files]
        return sorted(keys)

    def delete(self, keys):
        for k in keys:
            os.remove(self._path(k))
            try:  # like S3, don't leave empty "folders" behind
                os.rmdir(os.path.dirname(self._path(k)))
            except OSError:
                pass

    def publish(self, obj):
        self.put(STATS_KEY, obj)


def _store():
    if os.environ.get("STORAGE_MODE", "s3") == "local":
        return _Local(os.environ.get("LOCAL_OUTPUT_DIR", os.path.abspath("local-output")))
    return _S3(os.environ["RESULTS_BUCKET"], os.environ.get("WEBSITE_BUCKET", ""))


def _now():
    return datetime.now(timezone.utc)


def _hash(*parts):
    salt = os.environ.get("STATS_SALT", "local-dev").encode()
    return hmac.new(salt, "|".join(parts).encode(), hashlib.sha256).hexdigest()[:16]


def _save_raw(kind, record):
    now = _now()
    record.setdefault("ts", now.strftime("%Y-%m-%dT%H:%M:%SZ"))
    key = f"{RAW}{now:%Y-%m-%d}/{kind}-{now:%H%M%S}-{uuid.uuid4().hex[:8]}.json"
    _store().put(key, record)


# ------------------------------------------------------------------ recording
def operator_id(callsign):
    """Anonymous, stable id for a call sign (never stored in the clear)."""
    if not callsign or callsign == "UNKNOWN":
        return None
    return _hash("op", callsign.upper())


ERROR_CODES = [  # (substring of the InputError message, short code)
    ("call sign", "callsign"),
    ("no qsos", "no_qsos"),
    ("missing column", "missing_columns"),
    ("google sheet", "google_sheets"),
    ("sheets link", "google_sheets"),
    ("too large", "too_large"),
    ("at most", "too_many_files"),
    ("doesn't look like a cabrillo", "unrecognized_format"),
    ("could not read", "unreadable_log"),
    ("output", "bad_outputs"),
    ("upload a log", "no_input"),
    ("band category", "bad_band_category"),
    ("json", "bad_request"),
]


def error_code(message):
    text = str(message).lower()
    return next((code for needle, code in ERROR_CODES if needle in text), "other")


def record_analysis(record):
    """Save one analysis record. Never raises -- stats must not break the app."""
    try:
        record.update(v=1, type="analysis")
        _save_raw("analysis", record)
    except Exception:
        logger.exception("Could not save usage record")


def ping(event):
    """POST /api/ping from the home page: count a visit. Always returns 204."""
    try:
        headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
        agent = headers.get("user-agent", "")
        if agent and not any(m in agent.lower() for m in BOT_MARKERS):
            ip = (event.get("requestContext", {}).get("http", {}).get("sourceIp")
                  or headers.get("x-forwarded-for", "").split(",")[0].strip())
            _save_raw("visit", {"v": 1, "type": "visit",
                                "visitor": _hash("visit", _now().strftime("%Y-%m-%d"), ip, agent)})
    except Exception:
        logger.exception("Could not record visit")
    return {"statusCode": 204, "headers": {"Cache-Control": "no-store"}, "body": ""}


# ------------------------------------------------------------------ aggregation
def _compact(store, today):
    """Fold finished days' raw events into usage/daily/<day>.json."""
    by_day = {}
    for key in store.list(RAW):
        day = key[len(RAW):].split("/", 1)[0]
        if day < today:
            by_day.setdefault(day, []).append(key)
    for day, keys in sorted(by_day.items()):
        daily_key = f"{DAILY}{day}.json"
        try:
            daily = store.get(daily_key)
        except Exception:
            daily = {"date": day, "analyses": [], "visitors": []}
        visitors = set(daily.get("visitors", []))
        for key in keys:
            rec = store.get(key)
            if rec.get("type") == "visit":
                daily["visits"] = daily.get("visits", 0) + 1
                visitors.add(rec.get("visitor"))
            else:
                daily["analyses"].append(rec)
        daily["visitors"] = sorted(v for v in visitors if v)
        store.put(daily_key, daily)
        store.delete(keys)


def _load_days(store, today):
    days = {}
    for key in store.list(DAILY):
        daily = store.get(key)
        days[daily["date"]] = daily
    live = {"date": today, "analyses": [], "visits": 0, "visitors": []}
    visitors = set()
    for key in store.list(f"{RAW}{today}/"):
        rec = store.get(key)
        if rec.get("type") == "visit":
            live["visits"] += 1
            visitors.add(rec.get("visitor"))
        else:
            live["analyses"].append(rec)
    live["visitors"] = sorted(v for v in visitors if v)
    if live["analyses"] or live["visits"]:
        days[today] = live
    return days


def _pct(values, q):
    if not values:
        return None
    values = sorted(values)
    return values[min(len(values) - 1, int(round(q * (len(values) - 1))))]


QSO_BUCKETS = [(1, 24), (25, 49), (50, 99), (100, 199), (200, 499), (500, None)]


def _qso_bucket(n):
    for lo, hi in QSO_BUCKETS:
        if hi is None or n <= hi:
            return f"{lo}+" if hi is None else f"{lo}–{hi}"
    return "?"


def build_stats(days, today):
    analyses = [a for d in sorted(days) for a in days[d]["analyses"]]
    served = [a for a in analyses if a.get("status") in ("ok", "partial")]
    logs = [log for a in served for log in a.get("logs", [])]
    ops = Counter(log["op"] for log in logs if log.get("op"))
    durations = [a["duration_ms"] for a in served if a.get("duration_ms") is not None]
    qsos = [log.get("qsos", 0) for log in logs]
    cutoff = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=29)).strftime("%Y-%m-%d")

    first = min(days) if days else today
    start = max(datetime.strptime(first, "%Y-%m-%d"),
                datetime.strptime(today, "%Y-%m-%d") - timedelta(days=DAILY_DAYS_SHOWN - 1))
    daily = []
    d = start
    while d.strftime("%Y-%m-%d") <= today:
        key = d.strftime("%Y-%m-%d")
        day = days.get(key, {})
        daily.append({"date": key, "analyses": len(day.get("analyses", [])),
                      "visits": day.get("visits", 0), "visitors": len(day.get("visitors", []))})
        d += timedelta(days=1)

    monthly = {}
    for key, day in days.items():
        m = monthly.setdefault(key[:7], {"month": key[:7], "analyses": 0, "visits": 0,
                                         "logs": 0, "qsos": 0, "_ops": set()})
        m["analyses"] += len(day.get("analyses", []))
        m["visits"] += day.get("visits", 0)
        for a in day.get("analyses", []):
            if a.get("status") in ("ok", "partial"):
                for log in a.get("logs", []):
                    m["logs"] += 1
                    m["qsos"] += log.get("qsos", 0)
                    if log.get("op"):
                        m["_ops"].add(log["op"])
    monthly_rows = []
    for m in sorted(monthly.values(), key=lambda r: r["month"]):
        m["operators"] = len(m.pop("_ops"))
        monthly_rows.append(m)

    visits = sum(day.get("visits", 0) for day in days.values())
    counts = lambda items: dict(Counter(items).most_common())  # noqa: E731
    return {
        "generated": _now().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "firstDay": first if days else None,
        "upstream": served[-1].get("upstream") if served else None,
        "totals": {
            "analyses": len(analyses),
            "analyses30d": sum(1 for d_, day in days.items() if d_ >= cutoff
                               for _ in day.get("analyses", [])),
            "ok": sum(1 for a in analyses if a.get("status") == "ok"),
            "partial": sum(1 for a in analyses if a.get("status") == "partial"),
            "inputErrors": sum(1 for a in analyses if a.get("status") == "input_error"),
            "serverErrors": sum(1 for a in analyses if a.get("status") == "server_error"),
            "visits": visits,
            "visitors": sum(len(day.get("visitors", [])) for day in days.values()),
            "operators": len(ops),
            "logs": len(logs),
            "qsos": sum(qsos),
            "medianQsos": statistics.median(qsos) if qsos else None,
            "medianMs": _pct(durations, 0.5),
            "p90Ms": _pct(durations, 0.9),
        },
        "daily": daily,
        "monthly": monthly_rows,
        "outputs": counts(o for a in served for o in a.get("outputs", [])),
        "source": counts(a.get("source") for a in served if a.get("source")),
        "formats": counts(log.get("format") for log in logs if log.get("format")),
        "logsPerRequest": counts(str(a.get("nLogs", len(a.get("logs", [])))) for a in served),
        "bandCategory": counts(a.get("bandCategory") for a in served if a.get("bandCategory")),
        "bands": counts(b for log in logs for b in log.get("bands", [])),
        "qsoBuckets": {label: sum(1 for n in qsos if _qso_bucket(n) == label)
                       for label in (_qso_bucket(lo) for lo, _ in QSO_BUCKETS)},
        "years": dict(sorted(Counter(str(log["year"]) for log in logs if log.get("year")).items())),
        "grids": Counter(g for log in logs for g in log.get("grids", [])).most_common(25),
        "gridsDistinct": len({g for log in logs for g in log.get("grids", [])}),
        "repeatOperators": {
            "1 analysis": sum(1 for n in ops.values() if n == 1),
            "2–3": sum(1 for n in ops.values() if 2 <= n <= 3),
            "4+": sum(1 for n in ops.values() if n >= 4),
        },
        "errors": counts(a.get("error") for a in analyses if a.get("error")),
        "failedOutputs": counts(o for a in served for o in a.get("failedOutputs", [])),
    }


def aggregate():
    """Daily job: compact finished days, then publish stats/data.json."""
    store = _store()
    today = _now().strftime("%Y-%m-%d")
    _compact(store, today)
    stats = build_stats(_load_days(store, today), today)
    store.publish(stats)
    logger.info("Published stats: %s analyses, %s visits",
                stats["totals"]["analyses"], stats["totals"]["visits"])
    return {"analyses": stats["totals"]["analyses"], "visits": stats["totals"]["visits"]}
