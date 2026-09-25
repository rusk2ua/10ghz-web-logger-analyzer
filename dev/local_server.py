#!/usr/bin/env python3
"""Run the whole app locally -- no AWS needed.

    source .venv/bin/activate
    python dev/local_server.py            # then open http://localhost:8000

Serves frontend/ as the website, sends POST /api/process and /api/ping to the
same Lambda handler that runs in AWS (backend/app/handler.py), and keeps
generated files and usage records in ./local-output/ (served at /output/)
instead of S3. /stats/data.json is rebuilt on every request, so the stats
dashboard at http://localhost:8000/stats/ is always current.
"""

import argparse
import http.server
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRONTEND = os.path.join(ROOT, "frontend")
os.environ.setdefault("STORAGE_MODE", "local")
os.environ.setdefault("LOCAL_OUTPUT_DIR", os.path.join(ROOT, "local-output"))
OUTPUT = os.environ["LOCAL_OUTPUT_DIR"]
sys.path.insert(0, os.path.join(ROOT, "backend", "app"))

import handler  # noqa: E402
import usage  # noqa: E402


class Handler(http.server.SimpleHTTPRequestHandler):
    def translate_path(self, path):
        clean = path.split("?", 1)[0].split("#", 1)[0]
        if clean == "/stats/data.json":
            usage.aggregate()
            return os.path.join(OUTPUT, "stats", "data.json")
        if clean.startswith("/output/"):
            rel = os.path.normpath(clean[len("/output/"):]).lstrip(os.sep)
            return os.path.join(OUTPUT, rel)
        return super().translate_path(path)

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if path not in ("/api/process", "/api/ping"):
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length") or 0)
        if length > handler.MAX_BODY_BYTES + 1000:
            self.send_error(413, "Request too large")
            return
        body = self.rfile.read(length).decode("utf-8", errors="replace")
        event = {"rawPath": path, "body": body, "headers": dict(self.headers),
                 "requestContext": {"http": {"method": "POST", "sourceIp": self.client_address[0]}}}
        result = handler.handler(event)
        payload = result["body"].encode("utf-8")
        self.send_response(result["statusCode"])
        for key, value in result.get("headers", {}).items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()

    os.makedirs(OUTPUT, exist_ok=True)

    def make_handler(*a, **kw):
        return Handler(*a, directory=FRONTEND, **kw)

    # Single-threaded on purpose: the upstream scripts are run by changing
    # the working directory, exactly one request at a time, as in Lambda.
    server = http.server.HTTPServer((args.host, args.port), make_handler)
    print(f"ARRL 10 GHz log analyzer running at http://{args.host}:{args.port}  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
