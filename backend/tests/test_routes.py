"""API Gateway only passes the user's identity (JWT claims) to the Lambda on private routes.
Every App route that needs a signed-in user must therefore resolve to a private route in
infra/api.tf, or the Lambda answers "Sign in required" to signed-in users."""
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from delo.api import App  # noqa: E402


def gateway_routes() -> dict[str, list[str]]:
    tf = (ROOT / "infra" / "api.tf").read_text("utf-8")
    return {kind: re.findall(r'"([A-Z]+ /[^"]*)"', re.search(rf"{kind}_routes\s*=\s*\[(.*?)\]", tf, re.S).group(1))
            for kind in ("public", "private")}


def resolve(method: str, path: str, routes: dict[str, list[str]]) -> str | None:
    """Kind of the route API Gateway picks: an exact match first, else the greedy {proxy+}
    route with the longest prefix."""
    best = None
    for kind, keys in routes.items():
        for key in keys:
            m, route = key.split(" ", 1)
            if m != method:
                continue
            if route == path:
                return kind
            if route.endswith("/{proxy+}"):
                prefix = route[: -len("{proxy+}")]
                if path.startswith(prefix) and len(path) > len(prefix) and (not best or len(prefix) > best[0]):
                    best = (len(prefix), kind)
    return best and best[1]


def sample_path(pattern: str) -> str:
    """A concrete path for an App route regex, e.g. ^/api/me/decks/(?P<tid>[\\w-]+)$."""
    path = re.sub(r"\(\?P<\w+>[^)]*\)", "0123456789ab", pattern.strip("^$"))
    return path.replace("\\", "")


class GatewayRouteTests(unittest.TestCase):
    def test_signed_in_routes_are_private_and_public_routes_open(self):
        routes = gateway_routes()
        for method, pattern, _, auth in App(storage=None)._routes:
            path = sample_path(pattern.pattern)
            with self.subTest(f"{method} {path}"):
                self.assertEqual(resolve(method, path, routes), "private" if auth else "public")


if __name__ == "__main__":
    unittest.main()
