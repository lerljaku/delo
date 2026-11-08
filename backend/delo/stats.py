"""Cookie-free visitor statistics for the admin page.

Each page load sends POST /api/visit {page, referrer}. We keep only daily totals: page views,
unique visitors, views per page and per referring site. A visitor is told apart within one day
by an anonymous code: HMAC(secret, day + IP + browser), so it changes every day, can't be turned
back into the IP without the secret, and is deleted after storage.VISITOR_KEEP_DAYS.
Bots, admins and browsers sending Do Not Track / Global Privacy Control are not counted.
"""
from __future__ import annotations

import hashlib
import hmac
import re

MAX_VIEWS_PER_VISITOR = 300  # per day; more is a script, not a person
_PAGE_RE = re.compile(r"[a-z][a-z0-9-]{0,30}\.html")
_HOST_RE = re.compile(r"[a-z0-9.-]{1,100}")
_BOT_RE = re.compile(r"bot|crawl|spider|slurp|headless|preview|monitor|curl|wget|python|httpclient|java/|go-http", re.I)


def visitor_code(secret: str, day: str, ip: str, user_agent: str) -> str:
    return hmac.new(secret.encode(), f"{day}|{ip}|{user_agent}".encode(), hashlib.sha256).hexdigest()[:20]


def is_bot(user_agent: str) -> bool:
    return not user_agent or bool(_BOT_RE.search(user_agent))


def page_name(value) -> str | None:
    """'player.html' from a page name or path; '/' is the leaderboard. None if not a site page."""
    name = str(value or "").rsplit("/", 1)[-1].split("?")[0].lower() or "index.html"
    return name if _PAGE_RE.fullmatch(name) else None


def referrer_host(value, own_hosts: set[str]) -> str | None:
    """Host of an external referring site, e.g. 'facebook.com'; None for our own site or junk."""
    from urllib.parse import urlsplit

    host = (urlsplit(str(value or "")).hostname or "").lower()
    host = host[4:] if host.startswith("www.") else host
    if not host or not _HOST_RE.fullmatch(host) or host in own_hosts:
        return None
    return host


def client_ip(headers: dict) -> str:
    """CloudFront puts the visitor's address first in X-Forwarded-For."""
    return (headers.get("x-forwarded-for") or "").split(",")[0].strip()
