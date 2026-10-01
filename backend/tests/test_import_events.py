import io
import json
import os
import sys
import tempfile
import unittest
import zipfile
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from esl.storage import LocalStorage  # noqa: E402
from import_events import guess_date, import_events  # noqa: E402
from test_esl import EVENTLINK  # noqa: E402

SECOND_EVENT = "1\nZed\n1–0–0\n20\nYan\n0–1–0\n"


def write_source(root: Path) -> Path:
    source = root / "raw"
    source.mkdir()
    (source / "29.1. DC.txt").write_text(EVENTLINK, "utf-8")
    (source / "liga.txt").write_text(EVENTLINK.replace("7\n", "") + SECOND_EVENT, "utf-8")
    return source


def run(*args, **kwargs) -> str:
    out = io.StringIO()
    with redirect_stdout(out):
        import_events(*args, **kwargs)
    return out.getvalue()


class GuessDateTests(unittest.TestCase):
    def test_file_name_patterns(self):
        cases = {"29.1. DC": "2026-01-29", "Najada_10.2": "2026-02-10", "gdc25_04": "2026-04-25",
                 "najada1606": "2026-06-16", "dc176": "2026-06-17", "DC 24.1_víkend": "2026-01-24",
                 "Grail 4": None, "najada28": None}
        for stem, expected in cases.items():
            self.assertEqual(guess_date(stem, 2026), expected, stem)


class ImportEventsTests(unittest.TestCase):
    def make_storage(self):
        return LocalStorage(self.root / "data")

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.source = write_source(self.root)
        self.storage = self.make_storage()

    def test_manifest_split_and_idempotent(self):
        out = run(self.storage, self.source)
        self.assertIn("imported 3 events", out)
        manifest = json.loads((self.source / "events.json").read_text("utf-8"))
        self.assertEqual(sorted(manifest), ["29.1. DC.txt", "liga.txt#1", "liga.txt#2"])
        self.assertEqual(manifest["29.1. DC.txt"]["date"][5:], "01-29")
        self.assertIn("todo", manifest["liga.txt#2"])
        self.assertEqual(len(self.storage.leaderboard("rel")), 7)

        out = run(self.storage, self.source)
        self.assertIn("imported 0 events", out)
        self.assertEqual(len(self.storage.list_tournaments()), 3)

    def test_skip_replace_and_dry_run(self):
        run(self.storage, self.source)
        manifest_path = self.source / "events.json"
        manifest = json.loads(manifest_path.read_text("utf-8"))
        manifest["liga.txt#2"]["skip"] = True
        manifest["liga.txt#1"]["date"] = "2025-12-01"
        manifest_path.write_text(json.dumps(manifest), "utf-8")

        run(self.storage, self.source, dry_run=True)
        self.assertEqual(len(self.storage.list_tournaments()), 3)

        run(self.storage, self.source, replace=True)
        dates = sorted(t["date"] for t in self.storage.list_tournaments())
        self.assertEqual(len(dates), 2)
        self.assertEqual(dates[0], "2025-12-01")
        self.assertNotIn(None, [p.get("rating") for p in self.storage.leaderboard("rel")])


try:
    import boto3
    from moto import mock_aws
except ImportError:  # pragma: no cover
    mock_aws = None


@unittest.skipIf(mock_aws is None, "moto not installed")
class AwsImportEventsTests(ImportEventsTests):
    """Same tests against mocked AWS, with names read from the deployed Lambda's environment."""

    def make_storage(self):
        from import_events import aws_storage
        from test_aws_storage import ENV, create_resources

        creds = {k: ENV[k] for k in ("AWS_DEFAULT_REGION", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY")}
        patcher = mock.patch.dict(os.environ, creds)
        patcher.start()
        self.addCleanup(patcher.stop)
        aws = mock_aws()
        aws.start()
        self.addCleanup(aws.stop)
        create_resources()
        role = boto3.client("iam").create_role(RoleName="api", AssumeRolePolicyDocument="{}")["Role"]["Arn"]
        code = io.BytesIO()
        with zipfile.ZipFile(code, "w") as z:
            z.writestr("x.py", "")
        names = {k: v for k, v in ENV.items() if k.endswith(("_TABLE", "_BUCKET"))}
        boto3.client("lambda").create_function(
            FunctionName="esl-prod-api", Runtime="python3.13", Role=role, Handler="x.h",
            Code={"ZipFile": code.getvalue()}, Environment={"Variables": names})
        return aws_storage("esl-prod-api", None, None)


if __name__ == "__main__":
    unittest.main()
