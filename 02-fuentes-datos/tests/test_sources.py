import csv
import io
import ssl
import sys
import tempfile
import unittest
from email.message import Message
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError
from zipfile import ZipFile


MODULE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE / "scripts"))

import check_url_source
import load_stations
import prepare_ec2_dataset
from profile_stations import profile_stations
from source_tools import SourceError
from stations_data import HEADERS, schema_sql, typed_row


def station_row(site="0001"):
    row = [""] * len(HEADERS)
    row[:6] = ["01", "003", site, "30.123456", "-87.123456", "WGS84"]
    row[9] = "2024-02-29"
    row[16] = "NNE"
    row[20] = " Dirección "
    row[21] = "02300"
    return row


class Response(io.BytesIO):
    def __init__(self, status, body=b"", content_type="application/zip"):
        super().__init__(body)
        self.status = status
        self.headers = Message()
        self.headers["Content-Type"] = content_type
        if body:
            self.headers["Content-Length"] = str(len(body))


class FixtureTest(unittest.TestCase):
    def setUp(self):
        workspace = tempfile.TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        self.workspace = Path(workspace.name)

    def station_zip(self, rows):
        buffer = io.StringIO(newline="")
        writer = csv.writer(buffer)
        writer.writerow(HEADERS)
        writer.writerows(rows)
        path = self.workspace / "aqs_sites.zip"
        with ZipFile(path, "w") as archive:
            archive.writestr("aqs_sites.csv", buffer.getvalue().encode("utf-8"))
        return path


class StationsTests(FixtureTest):
    def test_sql_matches_loader_and_preserves_raw_text(self):
        self.assertEqual((MODULE / "sql/create_schema.sql").read_text(encoding="utf-8"), schema_sql())
        record = typed_row(station_row(), 1)
        self.assertEqual(record[:3], ("01", "003", "0001"))
        self.assertIsNone(record[10])
        self.assertEqual(record[16], "NNE")
        self.assertEqual(record[20:22], (" Dirección ", "02300"))

    def test_profile_and_preflight_detect_duplicate_or_invalid_rows(self):
        first = station_row()
        changed = station_row()
        changed[9] = "2023-02-29"
        other = station_row("12x4")
        path = self.station_zip([first, first, changed, other])
        profile = profile_stations(path)
        self.assertEqual(profile["status"], "failed")
        self.assertEqual(profile["duplicate_key_rows"], 2)
        self.assertEqual(profile["identical_duplicate_rows"], 1)
        self.assertEqual(profile["invalid_code_rows"], 1)
        self.assertEqual(profile["columns"]["site_established_date"]["invalid_count"], 1)
        with ZipFile(path) as archive, self.assertRaisesRegex(SourceError, "repetida"):
            load_stations.preflight(archive)

    def test_preflight_rejects_dates_and_precision_loss(self):
        for index, value in ((9, "2023-02-29"), (3, "30.123456789")):
            row = station_row()
            row[index] = value
            with ZipFile(self.station_zip([row])) as archive, self.assertRaises(SourceError):
                load_stations.preflight(archive)

    def test_current_geography_flags_absent_pairs_without_changing_keys(self):
        historic = station_row("0002")
        historic[:2] = ["02", "232"]
        metadata = self.workspace / "codes.html"
        metadata.write_text("<table><tr><td>01</td><td>Alabama</td><td>AL</td>"
                            "<td>003</td><td>Baldwin</td><td>04</td></tr></table>", encoding="utf-8")
        report = profile_stations(self.station_zip([station_row(), historic]), metadata)
        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["distinct_station_keys"], 2)
        self.assertEqual(report["aqs_geography_reference"]["station_pairs_not_listed"], {"02-232": 1})

    def test_loader_counts_inserted_and_already_present(self):
        rows = [station_row("0001"), station_row("0002"), station_row("0003")]
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = ("InnoDB",)
        cursor.executemany.side_effect = [2, 0]
        cursor.fetchall.return_value = [typed_row(rows[2], 3)]
        with ZipFile(self.station_zip(rows)) as archive:
            self.assertEqual(load_stations.insert_stations(connection, archive, 2), (2, 1))
        self.assertEqual(cursor.execute.call_count, 2)
        self.assertEqual(cursor.execute.call_args.args[1], ("01", "003", "0003"))
        connection.commit.assert_called_once()
        connection.rollback.assert_not_called()

    def test_conflicting_key_causes_rollback(self):
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = ("InnoDB",)
        cursor.executemany.side_effect = [1, 0]
        cursor.fetchall.return_value = [typed_row(station_row(), 1)]
        with ZipFile(self.station_zip([station_row(), station_row("0002")])) as archive:
            with self.assertRaisesRegex(SourceError, "datos distintos"):
                load_stations.insert_stations(connection, archive, 1)
        connection.rollback.assert_called_once()
        connection.commit.assert_not_called()

    def test_loader_rejects_nontransactional_table(self):
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = ("MyISAM",)
        with ZipFile(self.station_zip([station_row()])) as archive:
            with self.assertRaisesRegex(SourceError, "InnoDB"):
                load_stations.insert_stations(connection, archive, 1)
        cursor.executemany.assert_not_called()
        connection.commit.assert_not_called()

    def test_connection_requires_configured_ca_and_verifies_hostname(self):
        driver = MagicMock()
        with patch.dict("os.environ", {}, clear=True), self.assertRaisesRegex(SourceError, "MARIADB_SSL_CA"):
            load_stations.connect(driver)
        driver.connect.assert_not_called()
        settings = {"MARIADB_HOST": "example.invalid", "MARIADB_USER": "fixture", "MARIADB_PASSWORD": "fixture",
                    "MARIADB_SSL_CA": "test-ca.pem"}
        context = ssl.create_default_context()
        with patch.dict("os.environ", settings, clear=True):
            with patch.object(load_stations.ssl, "create_default_context", return_value=context) as tls:
                load_stations.connect(driver)
        tls.assert_called_once_with(cafile="test-ca.pem")
        self.assertIs(driver.connect.call_args.kwargs["ssl"], context)
        self.assertTrue(context.check_hostname)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)


class HourlyTests(FixtureTest):
    def test_copies_exact_bytes_and_reuses_existing(self):
        contents = b"field,measurement\r\n001,0.01\r\n"
        path = self.workspace / "hourly.zip"
        with ZipFile(path, "w") as archive:
            archive.writestr(prepare_ec2_dataset.CSV_NAME, contents)
        with patch.object(prepare_ec2_dataset, "open_epa") as download:
            result = prepare_ec2_dataset.prepare(path, self.workspace, allow_download=True)
            repeated = prepare_ec2_dataset.prepare(path, self.workspace, allow_download=True)
        download.assert_not_called()
        self.assertEqual((self.workspace / prepare_ec2_dataset.CSV_NAME).read_bytes(), contents)
        self.assertEqual(result["csv_sha256"], repeated["csv_sha256"])
        self.assertEqual(repeated["preparation"], "reused")

    def test_low_space_and_conflicting_destinations(self):
        path = self.workspace / "hourly.zip"
        output = self.workspace / prepare_ec2_dataset.CSV_NAME
        with ZipFile(path, "w") as archive:
            archive.writestr(output.name, b"x,y\r\n")
        with patch.object(prepare_ec2_dataset.shutil, "disk_usage", return_value=SimpleNamespace(free=0)):
            with self.assertRaisesRegex(SourceError, "Espacio insuficiente"):
                prepare_ec2_dataset.prepare(path, self.workspace)
        self.assertFalse(output.exists())
        output.write_bytes(b"previous")
        with self.assertRaisesRegex(SourceError, "CSV distinto"):
            prepare_ec2_dataset.prepare(path, self.workspace)
        self.assertEqual(output.read_bytes(), b"previous")
        self.assertFalse(list(self.workspace.glob("*.part")))

    def test_invalid_zip_only_downloads_with_explicit_option(self):
        path = self.workspace / "hourly.zip"
        path.write_bytes(b"invalid")
        with self.assertRaisesRegex(SourceError, "ZIP local ausente o inv"):
            prepare_ec2_dataset.prepare(path, self.workspace)
        data = io.BytesIO()
        with ZipFile(data, "w") as archive:
            archive.writestr(prepare_ec2_dataset.CSV_NAME, b"x,y\r\n")
        with patch.object(prepare_ec2_dataset, "open_epa", return_value=Response(200, data.getvalue())) as request:
            report = prepare_ec2_dataset.prepare(path, self.workspace, allow_download=True)
        request.assert_called_once_with(prepare_ec2_dataset.URL, "GET", 30)
        self.assertEqual(report["archive_origin"], "download")


class URLTests(unittest.TestCase):
    def test_head_checks_type_and_http_status(self):
        with patch.object(check_url_source, "open_epa", return_value=Response(200)):
            self.assertEqual(check_url_source.check_url(5)["status"], "passed")
        with patch.object(check_url_source, "open_epa", return_value=Response(200, content_type="text/html")):
            self.assertEqual(check_url_source.check_url(5)["status"], "failed")

    def test_head_falls_back_to_get_range(self):
        http_error = HTTPError(check_url_source.URL, 405, "method", {}, None)
        with patch.object(check_url_source, "open_epa", side_effect=[http_error, Response(206, b"PK\x03\x04")]) as req:
            result = check_url_source.check_url(5)
        req.assert_called_with(check_url_source.URL, "GET", 5, {"Range": "bytes=0-3"})
        self.assertEqual((result["status"], result["http_status"]), ("passed", 206))

    def test_timeout_is_reported(self):
        with patch.object(check_url_source, "open_epa", side_effect=TimeoutError):
            result = check_url_source.check_url(5)
        self.assertEqual(result["status"], "failed")
        self.assertIn("tiempo de espera", result["error"])
