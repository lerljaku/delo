"""Run EloHell locally without AWS: serves frontend/ and the API from one process.

    py backend/local_server.py [--port 8000] [--data .localdata] [--seed]

Authentication is faked: the frontend sends an `X-Dev-User` header
("admin" or "user:<email>") which is turned into Cognito-like claims.
"""
from __future__ import annotations

import argparse
import json
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from elohell.api import App  # noqa: E402
from elohell.storage import LocalStorage  # noqa: E402

LOCAL_CONFIG = """window.ELOHELL_CONFIG = {
  apiBase: "/api",
  githubRepo: "https://github.com/your-org/elohell",
  auth: { mode: "local" }
};
"""


def dev_claims(header: str | None) -> dict:
    if not header:
        return {}
    if header == "admin":
        return {"sub": "dev-admin", "email": "admin@localhost", "cognito:groups": ["admin"]}
    if header.startswith("user:"):
        email = header[5:].strip() or "player@localhost"
        return {"sub": "dev-" + "".join(c if c.isalnum() else "-" for c in email), "email": email}
    return {}


def make_handler(app: App):
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(ROOT / "frontend"), **kwargs)

        def _api(self, method: str):
            url = urlsplit(self.path)
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length).decode("utf-8") if length else ""
            try:
                body = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                return self._json(400, {"error": "Body must be JSON"})
            status, payload = app.handle(method, url.path, dict(parse_qsl(url.query)), body,
                                         dev_claims(self.headers.get("X-Dev-User")))
            self._json(status, payload)

        def _json(self, status: int, payload) -> None:
            data = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            path = urlsplit(self.path).path
            if path.startswith("/api/"):
                return self._api("GET")
            if path == "/config.js":
                data = LOCAL_CONFIG.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/javascript")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                return self.wfile.write(data)
            return super().do_GET()

        def do_POST(self):
            self._api("POST")

        def do_DELETE(self):
            self._api("DELETE")

    return Handler


def seed(app: App) -> None:
    claims = dev_claims("admin")
    for path in sorted((ROOT / "sample-data").glob("*")):
        if path.suffix not in (".csv", ".json", ".txt"):
            continue
        meta_path = path.with_suffix(path.suffix + ".meta")
        meta = json.loads(meta_path.read_text("utf-8-sig")) if meta_path.exists() else {}
        body = {"content": path.read_text("utf-8-sig"), **meta}
        status, payload = app.handle("POST", "/api/tournaments", {}, body, claims)
        print(f"seed {path.name}: {status} {payload.get('error') or payload.get('tournamentId')}")
    for path in sorted((ROOT / "docs" / "release-notes").glob("*.md")):
        text = path.read_text("utf-8-sig")
        header, _, body = text.partition("\n---\n")
        meta = dict(line.split(":", 1) for line in header.splitlines() if ":" in line)
        meta = {k.strip(): v.strip() for k, v in meta.items()}
        status, payload = app.handle("POST", "/api/release-notes", {}, {**meta, "body": body.strip()}, claims)
        print(f"seed release note {path.name}: {status}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--data", default=str(ROOT / ".localdata"))
    parser.add_argument("--seed", action="store_true", help="load sample-data/ and docs/release-notes/")
    args = parser.parse_args()
    app = App(LocalStorage(args.data))
    if args.seed:
        seed(app)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(app))
    print(f"EloHell running at http://127.0.0.1:{args.port}  (data: {args.data})")
    server.serve_forever()


if __name__ == "__main__":
    main()
