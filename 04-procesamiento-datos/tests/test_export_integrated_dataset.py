import csv
import hashlib
import io
import json
import os
import secrets
import sys
import tempfile
import unittest
import uuid
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch


MODULE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE / "scripts"))
import export_integrated_dataset as exporter
from load_daily_aqi import LoadError, connection_options, database_connection


def sample(day=date(2024, 2, 29), county="003"):
    return ("Alabama", 'Condado, "Ñ"', "01", county, day, 42, "Good", "Ozone", "01-003-0010", 2,
            Decimal("15.00"), Decimal("-2.30"), Decimal("6.35"), Decimal("0.00"))


class Cursor:
    def __init__(self, rows, columns=exporter.COLUMNS):
        self.description = [(column,) for column in columns]
        self.rows = iter(rows)
        self.batch_sizes = []

    def fetchmany(self, size):
        from itertools import islice

        self.batch_sizes.append(size)
        return list(islice(self.rows, size))


def fake_stream(rows):
    def run(output, batch_size, options):
        with output.open("w", encoding="utf-8", newline="") as stream:
            return exporter.write_csv(Cursor(rows), stream, batch_size)
    return run


class ValidationTests(unittest.TestCase):
    def test_batches_preserve_values_and_track_date_extrema(self):
        rows = [sample(date(2024, 12, 31)), sample(date(2024, 1, 1), "005")]
        cursor = Cursor(rows)
        text = io.StringIO(newline="")
        counts = exporter.write_csv(cursor, text, 1)
        self.assertEqual(counts, (2, date(2024, 1, 1), date(2024, 12, 31)))
        self.assertEqual(cursor.batch_sizes, [1, 1, 1])
        header, first, second = list(csv.reader(io.StringIO(text.getvalue(), newline="")))
        self.assertEqual(header, list(exporter.COLUMNS))
        self.assertEqual(first[1:7], ['Condado, "Ñ"', "01", "003", "2024-12-31", "42", "Good"])
        self.assertEqual(first[10:], ["15.00", "-2.30", "6.35", "0.00"])
        self.assertEqual(second[2:5], ["01", "005", "2024-01-01"])

    def test_duplicate_and_unsorted_keys_fail_across_batches(self):
        for records, message in (([sample(), sample()], "duplicada"),
                                 ([sample(county="005"), sample()], "orden")):
            with self.subTest(message=message), self.assertRaisesRegex(LoadError, message):
                exporter.write_csv(Cursor(records), io.StringIO(), 1)

    def test_all_columns_are_required(self):
        for index, field in enumerate(exporter.COLUMNS):
            row = list(sample())
            row[index] = None
            with self.subTest(field=field), self.assertRaisesRegex(LoadError, field):
                exporter.validate_record(row, 1)
        row = list(sample())
        row[6] = "  "
        with self.assertRaisesRegex(LoadError, "category"):
            exporter.validate_record(row, 1)

    def test_unexpected_types_and_code_formats_fail(self):
        for index, value in ((2, "1"), (2, "０１"), (3, 3), (4, "2024-02-29"), (4, date(2025, 1, 1)),
                             (5, True), (9, -1), (10, 15.0), (13, Decimal("NaN"))):
            row = list(sample())
            row[index] = value
            with self.subTest(index=index, value=value), self.assertRaisesRegex(LoadError, exporter.COLUMNS[index]):
                exporter.validate_record(row, 1)

    def test_changed_column_order_fails(self):
        with self.assertRaisesRegex(LoadError, "columnas"):
            exporter.write_csv(Cursor([sample()], exporter.COLUMNS[::-1]), io.StringIO(), 2)


class ArtifactTests(unittest.TestCase):
    def setUp(self):
        workspace = tempfile.TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        self.directory = Path(workspace.name)
        self.output = self.directory / "aqi_weather_2024.csv"
        self.manifest = self.output.with_suffix(".manifest.json")

    def test_manifest_matches_written_csv(self):
        with patch.object(exporter, "stream_view", side_effect=fake_stream([sample()])):
            result = exporter.export_dataset(self.output, batch_size=1, expected_rows=1)
        payload = self.output.read_bytes()
        self.assertEqual(result, json.loads(self.manifest.read_text(encoding="utf-8")))
        self.assertEqual(result["row_count"], 1)
        self.assertEqual(result["date_min"], result["date_max"])
        self.assertEqual(result["date_min"], "2024-02-29")
        self.assertEqual(result["size_bytes"], len(payload))
        self.assertEqual(result["sha256"], hashlib.sha256(payload).hexdigest())
        self.assertEqual(set(self.directory.iterdir()), {self.output, self.manifest})

    def test_validation_and_count_errors_do_not_publish(self):
        for rows, message in (([sample(), sample()], "duplicada"), ([sample()], "esperaban")):
            with patch.object(exporter, "stream_view", side_effect=fake_stream(rows)):
                with self.assertRaisesRegex(LoadError, message):
                    exporter.export_dataset(self.output, batch_size=1, expected_rows=2)
            self.assertFalse(list(self.directory.iterdir()))

    def test_existing_output_is_preserved_without_connecting(self):
        for existing in (self.output, self.manifest):
            existing.write_bytes(b"previous")
            with patch.object(exporter, "stream_view") as connect:
                with self.assertRaisesRegex(LoadError, "ya existe"):
                    exporter.export_dataset(self.output)
            self.assertEqual(existing.read_bytes(), b"previous")
            connect.assert_not_called()
            existing.unlink()

    def test_manifest_publication_failure_retracts_new_csv(self):
        link = os.link

        def fail_second_link(source, destination):
            if destination == self.manifest:
                raise PermissionError
            link(source, destination)

        with patch.object(exporter, "stream_view", side_effect=fake_stream([sample()])):
            with patch.object(exporter.os, "link", side_effect=fail_second_link):
                with self.assertRaises(PermissionError):
                    exporter.export_dataset(self.output)
        self.assertFalse(list(self.directory.iterdir()))


@unittest.skipUnless(os.environ.get("AIRQUALITY_TEST_DB_PORT"), "Requiere MariaDB local con TLS.")
class MariaDBExportTests(unittest.TestCase):
    def test_exports_real_view_with_select_only_privileges_and_year_filter(self):
        token = uuid.uuid4().hex
        schema = "export_test_" + token
        username = "export_" + token[:20]
        password = secrets.token_hex(24)
        options = connection_options()
        options.update(host="localhost", port=int(os.environ["AIRQUALITY_TEST_DB_PORT"]), database=None)
        sql_dir = exporter.PROJECT_ROOT / "03-ingesta-datos" / "sql"
        with database_connection(options) as admin, admin.cursor() as cursor:
            admin.autocommit(True)
            cursor.execute(f"CREATE DATABASE `{schema}`")
            try:
                for script in ("create_schema.sql", "create_weather_schema.sql"):
                    sql = (sql_dir / script).read_text(encoding="utf-8").replace("airquality", schema)
                    for statement in sql.split(";"):
                        if statement.strip():
                            cursor.execute(statement)
                days = (date(2024, 2, 29), date(2025, 2, 1))
                for day in days:
                    row = sample(day)
                    cursor.execute(f"INSERT INTO {schema}.daily_aqi_by_county VALUES "
                                   "(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", row[:10])
                    cursor.execute(f"INSERT INTO {schema}.daily_weather_by_county VALUES "
                                   "(%s,%s,%s,%s,%s,%s,%s)", (*row[2:5], *row[10:]))
                cursor.execute("CREATE USER %s@'localhost' IDENTIFIED BY %s REQUIRE SSL", (username, password))
                try:
                    cursor.execute(f"GRANT SELECT ON `{schema}`.* TO %s@'localhost'", (username,))
                    self.check_export({**options, "database": schema, "user": username, "password": password})
                    cursor.execute(f"SELECT count(*) FROM {schema}.v_aqi_weather_daily")
                    self.assertEqual(cursor.fetchone(), (2,))
                finally:
                    cursor.execute("DROP USER %s@'localhost'", (username,))
            finally:
                cursor.execute(f"DROP DATABASE `{schema}`")

    def check_export(self, options):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "fixture.csv"
            result = exporter.export_dataset(output, batch_size=1, expected_rows=1, options=options)
            with output.open(encoding="utf-8", newline="") as stream:
                record, = csv.DictReader(stream)
            self.assertEqual(result["row_count"], 1)
            self.assertEqual((record["state_code"], record["county_code"], record["measurement_date"]),
                             ("01", "003", "2024-02-29"))
            self.assertEqual((record["aqi"], record["category"], record["tmin_c"], record["precipitation_mm"]),
                             ("42", "Good", "-2.30", "0.00"))
