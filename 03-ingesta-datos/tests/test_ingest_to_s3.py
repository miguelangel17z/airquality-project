import base64
import csv
import hashlib
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from datetime import date
from email.message import Message
from pathlib import Path
from unittest.mock import MagicMock, patch

import boto3
from botocore.exceptions import ClientError, EndpointConnectionError
from botocore.stub import ANY, Stubber


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import ingest_to_s3 as ingest


BUCKET = "test-bucket"
KEY = "raw/url/census/year=2024/co-est2024-alldata.csv"
CENSUS = "STATE,COUNTY,SUMLEV,POPESTIMATE2024,CTYNAME\r\n35,013,050,210000,Doña Ana\r\n".encode("cp1252")
NOAA = (",".join(ingest.NOAA_HEADERS) + "\r\ncty,01003,1003,Alabama,AL,Baldwin,,"
        " -999.99, -999.99, -999.99, -999.99\r\n").encode("utf-8")
EPA = ("Alabama", "Baldwin", "01", "003", date(2024, 1, 1), 42, "Good", "Ozone", "01-003-0010", 1)


class Response(io.BytesIO):
    status = 200

    def __init__(self, data=CENSUS, content_type="text/csv", length=None):
        super().__init__(data)
        self.headers = Message()
        self.headers["Content-Type"] = content_type
        self.headers["Content-Length"] = str(len(data) if length is None else length)

    def geturl(self):
        return ingest.CENSUS_URL


def missing():
    return ClientError({"Error": {"Code": "404"}, "ResponseMetadata": {"HTTPStatusCode": 404}}, "HeadObject")


def database_stub(rows):
    db = MagicMock()
    connection = db.return_value.__enter__.return_value
    setup, cursor = MagicMock(), MagicMock()
    setup_context, query_context = MagicMock(), MagicMock()
    setup_context.__enter__.return_value = setup
    query_context.__enter__.return_value = cursor
    connection.cursor.side_effect = [setup_context, query_context]
    cursor.description = [(name,) for name in ingest.AQI_COLUMNS]
    cursor.fetchmany.side_effect = [[row] for row in rows] + [[]]
    return db, setup, cursor


class MemoryS3:
    def __init__(self):
        self.objects = {}

    def head_object(self, Bucket, Key, ChecksumMode):
        if Key not in self.objects:
            raise missing()
        data = self.objects[Key]
        checksum = base64.b64encode(hashlib.sha256(data).digest()).decode("ascii")
        return {"ContentLength": len(data), "ChecksumSHA256": checksum, "ChecksumType": "FULL_OBJECT"}

    def put_object(self, **kwargs):
        key = kwargs["Key"]
        assert kwargs["IfNoneMatch"] == "*" and key not in self.objects
        data = kwargs["Body"].read()
        sha = hashlib.sha256(data).digest()
        assert kwargs["ChecksumSHA256"] == base64.b64encode(sha).decode("ascii")
        assert kwargs["Metadata"]["sha256"] == sha.hex() and kwargs["ContentLength"] == len(data)
        self.objects[key] = data


class LocalSourceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.csv = self.root / "data.csv"

    def test_rds_selects_ten_columns_read_only_in_batches(self):
        db, setup, cursor = database_stub([EPA])
        with patch.object(ingest, "database_connection", db):
            self.assertEqual(ingest.export_rds(self.csv, 2024, 1, 1), 1)
        setup.execute.assert_called_once_with("START TRANSACTION READ ONLY")
        cursor.execute.assert_called_once_with(ingest.SELECT_AQI, (date(2024, 1, 1), date(2025, 1, 1)))
        self.assertEqual([call.args for call in cursor.fetchmany.call_args_list], [(1,), (1,)])
        with self.csv.open(encoding="utf-8", newline="") as stream:
            header, record = list(csv.reader(stream))
        self.assertEqual(header, list(ingest.AQI_COLUMNS))
        self.assertEqual(record[2:5], ["01", "003", "2024-01-01"])

    def test_rds_count_mismatch_fails(self):
        db, _, _ = database_stub([EPA])
        with patch.object(ingest, "database_connection", db), self.assertRaisesRegex(ingest.LoadError, "esperaban 2"):
            ingest.export_rds(self.csv, 2024, 1, 2)

    def test_download_preserves_census_cp1252_bytes(self):
        args = ingest.parse_args(["--source", "url", "--dry-run"])
        item = next(ingest.source_plan(args, self.root))
        with patch.object(ingest, "urlopen", return_value=Response()):
            self.assertEqual(ingest.prepare_source(item, args), 1)
        self.assertEqual(item.path.read_bytes(), CENSUS)

    def test_census_rejects_html_missing_headers_and_truncation(self):
        args = ingest.parse_args(["--source", "url", "--dry-run"])
        item = next(ingest.source_plan(args, self.root))
        samples = ((Response(b"<html>", "text/html"), "HTML"),
                   (Response(b"STATE,COUNTY\n01,003\n"), "columnas"),
                   (Response(CENSUS, length=len(CENSUS) + 1), "Content-Length"))
        for response, message in samples:
            with self.subTest(message=message), patch.object(ingest, "urlopen", return_value=response):
                with self.assertRaisesRegex(ingest.LoadError, message):
                    ingest.prepare_source(item, args)

    def test_year_partitions_names_and_census_headers_follow_config(self):
        args = ingest.parse_args(["--source", "all", "--year", "2025", "--census-url", ingest.CENSUS_URL.replace("2024", "2025")])
        sources = list(ingest.source_plan(args, self.root))
        self.assertIsNone(args.expected_rds_rows)
        self.assertEqual(len(sources), 14)
        self.assertEqual(sources[1].key, "raw/ec2/noaa/year=2025/202501-scaled.csv")
        self.assertEqual(sources[-1].key, "raw/url/census/year=2025/co-est2025-alldata.csv")
        with patch.object(ingest, "urlopen", return_value=Response(CENSUS.replace(b"2024", b"2025"))):
            self.assertEqual(ingest.prepare_source(sources[-1], args), 1)


class S3Tests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / "raw.csv"
        self.path.write_bytes(CENSUS)
        self.sha = hashlib.sha256(CENSUS).hexdigest()
        self.checksum = base64.b64encode(bytes.fromhex(self.sha)).decode("ascii")
        client = boto3.client("s3", region_name="us-east-1", aws_access_key_id="testing", aws_secret_access_key="testing")
        self.s3 = client
        self.stub = Stubber(client)
        self.head = {"Bucket": BUCKET, "Key": KEY, "ChecksumMode": "ENABLED"}
        self.put = {"Bucket": BUCKET, "Key": KEY, "Body": ANY, "ContentLength": len(CENSUS), "ContentType": "text/csv",
                    "ChecksumSHA256": self.checksum, "Metadata": {"sha256": self.sha}, "IfNoneMatch": "*"}

    def publish(self):
        return ingest.publish(self.s3, BUCKET, KEY, self.path, len(CENSUS), self.sha, "text/csv")

    def test_upload_uses_conditional_put_and_checksum(self):
        self.stub.add_client_error("head_object", service_error_code="404", http_status_code=404, expected_params=self.head)
        self.stub.add_response("put_object", {}, self.put)
        with self.stub:
            self.assertEqual(self.publish(), "uploaded")
        self.stub.assert_no_pending_responses()

    def test_identical_full_checksum_skips_without_get_or_put(self):
        self.stub.add_response("head_object", {"ContentLength": len(CENSUS), "ChecksumSHA256": self.checksum,
                                               "ChecksumType": "FULL_OBJECT"}, self.head)
        with self.stub:
            self.assertEqual(self.publish(), "skipped_identical")

    def test_legacy_or_composite_checksum_uses_content_not_metadata(self):
        for sample, expected in ((CENSUS, "skipped_identical"), (b"x" + CENSUS[1:], "conflict")):
            self.stub.add_response("head_object", {"ContentLength": len(CENSUS), "ChecksumSHA256": self.checksum + "-2",
                                                   "ChecksumType": "COMPOSITE", "ETag": '"version"',
                                                   "Metadata": {"sha256": self.sha}}, self.head)
            self.stub.add_response("get_object", {"Body": io.BytesIO(sample)},
                                   {"Bucket": BUCKET, "Key": KEY, "IfMatch": '"version"'})
            with self.subTest(expected=expected), self.stub:
                if expected == "conflict":
                    with self.assertRaises(ingest.ContentConflict):
                        self.publish()
                else:
                    self.assertEqual(self.publish(), expected)

    def test_permission_error_is_never_treated_as_missing(self):
        self.stub.add_client_error("head_object", service_error_code="AccessDenied", http_status_code=403, expected_params=self.head)
        with self.stub, self.assertRaises(ClientError) as caught:
            self.publish()
        self.assertIn("HTTP 403", ingest.describe_error(caught.exception))

    def test_concurrent_writer_rechecked_after_412(self):
        self.stub.add_client_error("head_object", service_error_code="404", http_status_code=404, expected_params=self.head)
        self.stub.add_client_error("put_object", service_error_code="PreconditionFailed", http_status_code=412, expected_params=self.put)
        self.stub.add_response("head_object", {"ContentLength": len(CENSUS), "ChecksumSHA256": self.checksum}, self.head)
        with self.stub:
            self.assertEqual(self.publish(), "skipped_identical")
        self.stub.assert_no_pending_responses()


class PipelineTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        for month in range(1, 13):
            (self.root / f"2024{month:02d}-scaled.csv").write_bytes(NOAA)

    def args(self, *options):
        return ingest.parse_args(["--bucket", BUCKET, "--noaa-dir", str(self.root), "--manifest-dir", str(self.root / "logs"),
                                  "--expected-rds-rows", "1", *options])

    def run_all(self, s3, *options):
        db, _, _ = database_stub([EPA])
        with patch.object(ingest, "database_connection", db), patch.object(ingest, "urlopen", return_value=Response()):
            with redirect_stderr(io.StringIO()):
                return ingest.run(self.args(*options), s3)

    def test_three_sources_manifest_and_idempotent_replay(self):
        s3 = MemoryS3()
        first, path = self.run_all(s3)
        self.assertEqual(first["summary"]["uploaded"], 14)
        manifest_key = first["manifest_s3_uri"].split(f"s3://{BUCKET}/", 1)[1]
        self.assertTrue(manifest_key.startswith("manifests/year=2024/"))
        self.assertEqual(path.read_bytes(), s3.objects[manifest_key])
        self.assertEqual(json.loads(path.read_bytes()), first)
        self.assertEqual(s3.objects["raw/ec2/noaa/year=2024/202402-scaled.csv"], NOAA)
        self.assertEqual(s3.objects[KEY], CENSUS)
        self.assertEqual([item["records"] for item in first["objects"]], [1] * 14)
        self.assertEqual(first["objects"][0]["s3_uri"], f"s3://{BUCKET}/raw/rds/epa/year=2024/daily_aqi_by_county_2024.csv")
        second, second_path = self.run_all(s3)
        self.assertEqual((second["summary"]["skipped_identical"], second["summary"]["uploaded"]), (14, 0))
        self.assertNotEqual(path, second_path)

    def test_dry_run_never_creates_aws_client_and_keeps_manifest_local(self):
        with patch.object(ingest, "create_s3_client") as client:
            report, path = self.run_all(None, "--dry-run")
        client.assert_not_called()
        self.assertEqual(report["summary"]["validated"], 14)
        self.assertIsNone(report["manifest_s3_uri"])
        self.assertTrue(path.is_file())

    def test_missing_noaa_file_and_conflict_do_not_stop_other_inputs(self):
        (self.root / "202402-scaled.csv").unlink()
        s3 = MemoryS3()
        s3.objects[KEY] = b"another version"
        report, _ = self.run_all(s3)
        self.assertEqual(report["status"], "failed")
        self.assertEqual((report["summary"]["uploaded"], report["summary"]["error"], report["summary"]["conflict"]), (12, 1, 1))
        self.assertEqual(s3.objects[KEY], b"another version")

    def test_s3_connection_errors_and_manifest_failure_are_recorded_locally(self):
        s3 = MagicMock()
        s3.head_object.side_effect = EndpointConnectionError(endpoint_url="https://endpoint.example.invalid")
        report, path = self.run_all(s3, "--source", "url")
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["summary"]["error"], 1)
        self.assertIn("EndpointConnectionError", report["manifest_upload_error"])
        self.assertEqual(json.loads(path.read_bytes()), report)
        s3.put_object.assert_not_called()
