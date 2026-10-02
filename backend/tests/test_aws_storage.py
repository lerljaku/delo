"""Runs the API test suite against AwsStorage using moto (mocked S3 + DynamoDB).

Skipped unless `moto` is installed: pip install -r backend/requirements-dev.txt
Table definitions mirror infra/data.tf.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    import boto3
    from moto import mock_aws
except ImportError:  # pragma: no cover
    mock_aws = None

from test_delo import ADMIN, ApiTests  # noqa: E402

ENV = {
    "AWS_DEFAULT_REGION": "eu-central-1", "AWS_ACCESS_KEY_ID": "test", "AWS_SECRET_ACCESS_KEY": "test",
    "DATA_BUCKET": "delo-test-data", "TOURNAMENTS_TABLE": "t-tournaments", "PLAYERS_TABLE": "t-players",
    "ACCOUNTS_TABLE": "t-accounts", "RELEASE_NOTES_TABLE": "t-release-notes",
}


def create_resources():
    ddb = boto3.client("dynamodb")
    s3 = boto3.client("s3")
    s3.create_bucket(Bucket=ENV["DATA_BUCKET"], CreateBucketConfiguration={"LocationConstraint": ENV["AWS_DEFAULT_REGION"]})

    def table(name, keys, attrs, **extra):
        ddb.create_table(
            TableName=name, BillingMode="PAY_PER_REQUEST",
            KeySchema=[{"AttributeName": k, "KeyType": t} for k, t in keys],
            AttributeDefinitions=[{"AttributeName": a, "AttributeType": t} for a, t in attrs], **extra,
        )

    table(ENV["TOURNAMENTS_TABLE"], [("tournamentId", "HASH")], [("tournamentId", "S")])
    table(ENV["ACCOUNTS_TABLE"], [("sub", "HASH")], [("sub", "S")])
    table(ENV["RELEASE_NOTES_TABLE"], [("version", "HASH")], [("version", "S")])
    table(
        ENV["PLAYERS_TABLE"], [("playerId", "HASH"), ("SK", "RANGE")],
        [("playerId", "S"), ("SK", "S"), ("ladder", "S"), ("rating", "N")],
        GlobalSecondaryIndexes=[{
            "IndexName": "leaderboard",
            "KeySchema": [{"AttributeName": "ladder", "KeyType": "HASH"}, {"AttributeName": "rating", "KeyType": "RANGE"}],
            "Projection": {"ProjectionType": "INCLUDE", "NonKeyAttributes": ["displayName", "hidden", "membership", "summary"]},
        }],
    )


@unittest.skipIf(mock_aws is None, "moto not installed")
class AwsApiTests(ApiTests):
    def make_storage(self):
        from delo.storage import AwsStorage

        patcher = unittest.mock.patch.dict(os.environ, ENV)
        patcher.start()
        self.addCleanup(patcher.stop)
        mock = mock_aws()
        mock.start()
        self.addCleanup(mock.stop)
        create_resources()
        return AwsStorage()

    def test_erase_leaves_no_old_s3_versions(self):
        from test_delo import CSV, upload
        from delo.parsers import player_id

        upload(self.app, CSV)
        storage = self.app.storage
        storage.s3.put_bucket_versioning(Bucket=storage.bucket, VersioningConfiguration={"Status": "Enabled"})
        tid = storage.list_tournaments()[0]["tournamentId"]
        self.app.handle("POST", "/api/tournaments/" + tid, {}, {"link": "https://example.com/x"}, ADMIN)  # 2nd version
        self.app.handle("POST", f"/api/admin/players/{player_id('Alice')}/erase", {}, {}, ADMIN)
        versions = storage.s3.list_object_versions(Bucket=storage.bucket)["Versions"]
        for v in versions:
            body = storage.s3.get_object(Bucket=storage.bucket, Key=v["Key"], VersionId=v["VersionId"])["Body"].read()
            self.assertNotIn(b"Alice", body, v["Key"])


del ApiTests  # don't run the local-storage suite twice

if __name__ == "__main__":
    unittest.main()
