import csv
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import load_daily_aqi as loader


def row(day="2024-02-29"):
    return ["Alabama", "Baldwin", "01", "003", day, "42", "Good", "Ozone", "01-003-0010", "2"]


def csv_stream(rows, headers=loader.HEADERS):
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerows([headers, *rows])
    stream.seek(0)
    return stream


class ValidationTests(unittest.TestCase):
    def test_types_preserve_geographic_zeros_and_convert_leap_day(self):
        result = loader.parse_record(row())
        self.assertEqual(result[2:6], ("01", "003", date(2024, 2, 29), 42))
        self.assertEqual(result[-1], 2)

    def test_rejects_invalid_dates(self):
        for value in ("2023-02-29", "2024-02-30", "2024-2-09", "2024-13-01", "0999-12-31", ""):
            with self.subTest(value=value), self.assertRaisesRegex(loader.InvalidRecord, "measurement_date"):
                loader.parse_record(row(value))

    def test_rejects_invalid_geographic_codes(self):
        for index, value in ((2, "1"), (2, "001"), (2, "A1"), (2, " 1"), (3, "03"), (3, "００３")):
            with self.subTest(index=index, value=value), self.assertRaisesRegex(loader.InvalidRecord, "código|code"):
                record = row()
                record[index] = value
                loader.parse_record(record)

    def test_rejects_negative_overflow_null_and_noninteger_values(self):
        for index in (5, 9):
            for value in ("-1", "65536", "3.5", "", "+2", "١", "9" * 6000):
                with self.subTest(index=index, value=value[:20]), self.assertRaises(loader.InvalidRecord):
                    record = row()
                    record[index] = value
                    loader.parse_record(record)

    def test_accepts_unsigned_limits_and_preserves_text(self):
        record = row()
        record[1], record[5], record[9] = " Condado, Ñ ", "65535", "000"
        self.assertEqual(loader.parse_record(record)[1], " Condado, Ñ ")
        self.assertEqual(loader.parse_record(record)[5], 65535)
        self.assertEqual(loader.parse_record(record)[9], 0)

    def test_rejects_wrong_field_count_and_missing_or_oversized_text(self):
        with self.assertRaisesRegex(loader.InvalidRecord, "10 campos"):
            loader.parse_record(row()[:-1])
        for value in ("  ", "x" * 51):
            record = row()
            record[6] = value
            with self.assertRaisesRegex(loader.InvalidRecord, "category"):
                loader.parse_record(record)

    def test_accepts_both_documented_headers_and_rejects_reordered_columns(self):
        for headers in (loader.HEADERS, loader.EPA_HEADERS):
            with self.subTest(headers=headers):
                self.assertEqual(len(list(loader.read_records(csv_stream([row()], headers), loader.Counts()))), 1)
        with self.assertRaisesRegex(loader.LoadError, "encabezado"):
            list(loader.read_records(csv_stream([row()], loader.HEADERS[::-1]), loader.Counts()))

    def test_malformed_and_empty_csv_fail(self):
        with self.assertRaisesRegex(loader.LoadError, "cero registros"):
            list(loader.read_records(csv_stream([]), loader.Counts()))
        malformed = io.StringIO(",".join(loader.HEADERS) + '\n"unterminated')
        with self.assertRaisesRegex(loader.LoadError, "mal formado"):
            list(loader.read_records(malformed, loader.Counts()))

    def test_duplicate_keys_count_as_invalid_and_other_rows_are_checked(self):
        counts = loader.Counts()
        diagnostics = io.StringIO()
        with redirect_stderr(diagnostics):
            records = list(loader.read_records(csv_stream([row(), row(), row("2024-03-01")]), counts))
        self.assertEqual((counts.processed, counts.invalid, len(records)), (3, 1, 2))
        self.assertIn("Línea 3", diagnostics.getvalue())

    def test_dry_run_does_not_connect_and_reports_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.csv"
            path.write_bytes(csv_stream([row(), row("2024-02-31")]).getvalue().encode("utf-8-sig"))
            output = io.StringIO()
            with patch.object(loader, "database_connection") as connect, redirect_stdout(output), redirect_stderr(io.StringIO()):
                self.assertEqual(loader.main(["--dry-run", "--csv", str(path)]), 1)
            connect.assert_not_called()
            result = json.loads(output.getvalue())
            self.assertEqual((result["processed"], result["errors"], result["inserted"]), (2, 1, 0))


class TransactionTests(unittest.TestCase):
    def setUp(self):
        self.connection = MagicMock()
        self.cursor = self.connection.cursor.return_value.__enter__.return_value
        self.cursor.fetchone.return_value = ("InnoDB",)
        self.primary_key = tuple((column,) for column in loader.KEY_COLUMNS)
        self.cursor.fetchall.return_value = self.primary_key

    def write(self, rows, batch_size):
        counts = loader.Counts()
        with redirect_stderr(io.StringIO()):
            return loader.write_records(self.connection, loader.read_records(csv_stream(rows), counts), batch_size, counts)

    def test_inserts_using_parameterized_batches_and_commits_once(self):
        self.cursor.executemany.side_effect = [2, 1]
        self.assertEqual(self.write([row(), row("2024-03-01"), row("2024-03-02")], 2), (3, 0))
        self.assertEqual([len(call.args[1]) for call in self.cursor.executemany.call_args_list], [2, 1])
        self.assertEqual(self.cursor.executemany.call_args.args[0].count("%s"), 10)
        self.connection.commit.assert_called_once()

    def test_identical_existing_row_is_counted_without_duplicate(self):
        self.cursor.executemany.return_value = 0
        self.cursor.fetchall.side_effect = [self.primary_key, (loader.parse_record(row()),)]
        self.assertEqual(self.write([row()], 1000), (0, 1))
        self.assertEqual(self.cursor.execute.call_args.args[1], ("01", "003", date(2024, 2, 29)))
        self.connection.commit.assert_called_once()

    def test_conflict_or_database_exception_rolls_back_earlier_batches(self):
        for affected, expected_error in ((0, loader.LoadError), (RuntimeError("test error"), RuntimeError)):
            with self.subTest(expected_error=expected_error):
                self.connection.reset_mock()
                self.cursor.executemany.side_effect = [1, affected]
                self.cursor.fetchall.side_effect = [self.primary_key, ()]
                with self.assertRaises(expected_error):
                    self.write([row(), row("2024-03-01")], 1)
                self.connection.rollback.assert_called_once()
                self.connection.commit.assert_not_called()

    def test_late_invalid_record_rolls_back_and_reports_no_inserts(self):
        self.cursor.executemany.return_value = 1
        self.assertEqual(self.write([row(), row("2024-02-31"), row("2024-03-02")], 1), (0, 0))
        self.cursor.executemany.assert_called_once()
        self.connection.rollback.assert_called_once()
        self.connection.commit.assert_not_called()

    def test_refuses_nontransactional_table(self):
        self.cursor.fetchone.return_value = ("MyISAM",)
        with self.assertRaisesRegex(loader.LoadError, "InnoDB"):
            self.write([row()], 1)
        self.cursor.executemany.assert_not_called()

    def test_refuses_table_with_incompatible_primary_key(self):
        self.cursor.fetchall.return_value = (("state_code",),)
        with self.assertRaisesRegex(loader.LoadError, "clave primaria"):
            self.write([row()], 1)
        self.cursor.executemany.assert_not_called()


class ConfigurationTests(unittest.TestCase):
    def test_reads_default_database_and_password_interactively(self):
        with patch.dict(os.environ, {"DB_HOST": "fixture.invalid", "DB_USER": "fixture"}, clear=True):
            with patch.object(sys.stdin, "isatty", return_value=True), patch.object(loader.getpass, "getpass", return_value="fixture") as prompt:
                options = loader.connection_options()
        prompt.assert_called_once()
        self.assertEqual((options["database"], options["port"]), ("airquality", 3306))
        self.assertTrue(options["ssl"].check_hostname)

    def test_missing_password_without_terminal_fails_safely(self):
        with patch.dict(os.environ, {"DB_HOST": "fixture.invalid", "DB_USER": "fixture"}, clear=True):
            with patch.object(sys.stdin, "isatty", return_value=False), patch.object(loader.getpass, "getpass") as prompt:
                with self.assertRaisesRegex(loader.LoadError, "DB_PASSWORD"):
                    loader.connection_options()
        prompt.assert_not_called()

    def test_interactive_password_rejects_echo_fallback(self):
        with patch.dict(os.environ, {"DB_HOST": "fixture.invalid", "DB_USER": "fixture"}, clear=True):
            with patch.object(sys.stdin, "isatty", return_value=True):
                with patch.object(loader.getpass, "getpass", side_effect=loader.getpass.GetPassWarning):
                    with self.assertRaisesRegex(loader.LoadError, "sin eco"):
                        loader.connection_options()
