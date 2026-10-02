"""Run Delo locally without AWS: serves frontend/ and the API from one process.

    py backend/local_server.py [--port 8000] [--data .localdata] [--seed [--source DIR]]

--seed loads the release notes and the Eventlink pastes in raw-data-eventlink/ (or --source)
using import_events.py, which explains events.json.

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

from delo.api import App  # noqa: E402
from delo.storage import LocalStorage  # noqa: E402
from import_events import RAW_DATA, import_events  # noqa: E402

LOCAL_CONFIG = """window.DELO_CONFIG = {
  apiBase: "/api",
  githubRepo: "https://github.com/your-org/delo",
  operatorName: "Local Developer",
  privacyContact: "privacy@example.com",
  dataRegion: "eu-west-1",
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


def seed(app: App, source: Path) -> None:
    claims = dev_claims("admin")
    if source.is_dir():
        import_events(app.storage, source, uploaded_by=claims["email"])
    else:
        print(f"no tournaments seeded: {source} does not exist")
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
    parser.add_argument("--seed", action="store_true", help="load tournaments and release notes")
    parser.add_argument("--source", default=str(RAW_DATA), help="folder of Eventlink pastes to seed from")
    args = parser.parse_args()
    app = App(LocalStorage(args.data))
    if args.seed:
        seed(app, Path(args.source).resolve())
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(app))
    print(f"Delo running at http://127.0.0.1:{args.port}  (data: {args.data})")
    server.serve_forever()


if __name__ == "__main__":
    main()
