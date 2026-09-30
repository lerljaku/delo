"""Run ESL locally without AWS: serves frontend/ and the API from one process.

    py backend/local_server.py [--port 8000] [--data .localdata] [--seed [--source DIR]]

--seed loads raw-data-eventlink/ (Eventlink pastes, metadata in its events.json, which is
generated with guesses from the file names on first run) or sample-data/ if that folder is missing.

Authentication is faked: the frontend sends an `X-Dev-User` header
("admin" or "user:<email>") which is turned into Cognito-like claims.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from esl.api import App  # noqa: E402
from esl.parsers import split_eventlink  # noqa: E402
from esl.storage import LocalStorage  # noqa: E402

RAW_DATA = ROOT / "raw-data-eventlink"
MANIFEST = "events.json"

LOCAL_CONFIG = """window.ESL_CONFIG = {
  apiBase: "/api",
  githubRepo: "https://github.com/your-org/esl",
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


def guess_date(stem: str, year: int) -> str | None:
    """Day and month from names like '29.1. DC', 'Najada_10.2', 'gdc25_04', 'najada1606', 'dc176'."""
    candidates = []
    if m := re.search(r"(?<!\d)(\d{1,2})[._](\d{1,2})(?!\d)", stem):
        candidates.append((m.group(1), m.group(2)))
    elif m := re.search(r"(?<!\d)(\d\d)(\d\d)(?!\d)", stem):
        candidates.append((m.group(1), m.group(2)))
    elif m := re.search(r"(?<!\d)(\d)(\d)(\d)(?!\d)", stem):
        candidates += [(m.group(1) + m.group(2), m.group(3)), (m.group(1), m.group(2) + m.group(3))]
    for day, month in candidates:
        try:
            return date(year, int(month), int(day)).isoformat()
        except ValueError:
            continue
    return None


def build_manifest(source: Path, year: int) -> dict:
    """One entry per event; files with several events pasted together get 'file#1', 'file#2', ..."""
    manifest = {}
    for path in sorted(source.glob("*.txt")):
        parts = split_eventlink(path.read_text("utf-8-sig"))
        guessed = guess_date(path.stem, year)
        for n in range(1, len(parts) + 1):
            key = path.name if len(parts) == 1 else f"{path.name}#{n}"
            entry = {"name": path.stem.strip() if len(parts) == 1 else f"{path.stem.strip()} ({n})",
                     "date": guessed or f"{year}-01-01", "type": "rel"}
            if not guessed:
                entry["todo"] = "date not found in the file name, set it and delete this line"
            manifest[key] = entry
    return manifest


def seed_events(app: App, source: Path, claims: dict) -> None:
    """Upload Eventlink pastes from `source`, with names/dates/types from source/events.json."""
    manifest_path = source / MANIFEST
    if not manifest_path.exists():
        manifest_path.write_text(json.dumps(build_manifest(source, date.today().year), indent=2, ensure_ascii=False),
                                 "utf-8")
        print(f"wrote {manifest_path}: check the guessed names, dates and types there")
    manifest = json.loads(manifest_path.read_text("utf-8-sig"))
    for path in sorted(source.glob("*.txt")):
        parts = split_eventlink(path.read_text("utf-8-sig"))
        for n, content in enumerate(parts, start=1):
            key = path.name if len(parts) == 1 else f"{path.name}#{n}"
            meta = manifest.get(key)
            if meta is None or meta.get("skip"):
                print(f"seed {key}: skipped ({'not in ' + MANIFEST if meta is None else 'skip'})")
                continue
            if "todo" in meta:
                print(f"seed {key}: warning, {meta['todo']}")
            body = {"content": content, "format": "eventlink",
                    **{k: meta[k] for k in ("name", "date", "type") if k in meta}}
            status, payload = app.handle("POST", "/api/tournaments", {}, body, claims)
            print(f"seed {key}: {status} {payload.get('error') or payload.get('tournamentId')}")


def seed_samples(app: App, source: Path, claims: dict) -> None:
    for path in sorted(source.glob("*")):
        if path.suffix not in (".csv", ".json", ".txt"):
            continue
        meta_path = path.with_suffix(path.suffix + ".meta")
        meta = json.loads(meta_path.read_text("utf-8-sig")) if meta_path.exists() else {}
        body = {"content": path.read_text("utf-8-sig"), **meta}
        status, payload = app.handle("POST", "/api/tournaments", {}, body, claims)
        print(f"seed {path.name}: {status} {payload.get('error') or payload.get('tournamentId')}")


def seed(app: App, source: Path) -> None:
    claims = dev_claims("admin")
    if source.name == "sample-data":
        seed_samples(app, source, claims)
    else:
        seed_events(app, source, claims)
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
    parser.add_argument("--seed", action="store_true",
                        help="load tournaments (raw-data-eventlink/ if present, else sample-data/) and release notes")
    parser.add_argument("--source", help="folder to seed tournaments from")
    args = parser.parse_args()
    app = App(LocalStorage(args.data))
    if args.seed:
        source = Path(args.source) if args.source else RAW_DATA if RAW_DATA.is_dir() else ROOT / "sample-data"
        seed(app, source.resolve())
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(app))
    print(f"ESL running at http://127.0.0.1:{args.port}  (data: {args.data})")
    server.serve_forever()


if __name__ == "__main__":
    main()
