import calendar
import csv
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import load_daily_weather as weather


def row(day, fips="01003", measures=(" 12.34", " -1.20", "  5.57", "  0.00")):
    return ["cty", fips, "1003", "Alabama", "AL", "AL: Baldwin", day, *measures]


def padding(fips="01003"):
    return row("", fips=fips, measures=("  -999.99",) * 4)


def calendar_rows(year, month, fips="01003"):
    return [row(date(year, month, day).isoformat(), fips) for day in range(1, calendar.monthrange(year, month)[1] + 1)]


def stream(rows):
    source = io.StringIO(newline="")
    csv.writer(source).writerows([weather.HEADERS, *rows])
    source.seek(0)
    return source


def write_fixture(path, rows):
    path.write_bytes(stream(rows).getvalue().encode("utf-8-sig"))


class WeatherValidationTests(unittest.TestCase):
    def read(self, rows, month=2, year=2024):
        counts = weather.WeatherCounts()
        with redirect_stderr(io.StringIO()):
            records = list(weather.read_weather(stream(rows), year, month, counts))
        return records, counts

    def test_valid_fips_preserves_zeros_and_decimals(self):
        records, counts = self.read([row("2024-02-29")])
        self.assertEqual(records, [("01", "003", date(2024, 2, 29), Decimal("12.34"), Decimal("-1.20"),
                                    Decimal("5.57"), Decimal("0.00"))])
        self.assertEqual((counts.valid, counts.invalid), (1, 0))

    def test_invalid_fips(self):
        for fips in ("1003", "001003", "01A03", " 1003", "０１００３", ""):
            with self.subTest(fips=fips):
                records, counts = self.read([row("2024-02-29", fips)])
                self.assertEqual((records, counts.invalid), ([], 1))

    def test_invalid_and_out_of_month_dates_are_rejected(self):
        rows = [row(day) for day in ("2024-02-30", "2024-02-00", "2024-03-01", "2023-02-28", "2024-2-01", "NA")]
        rows.append(row("2024-02-30", measures=("-999.99",) * 4))
        records, counts = self.read(rows)
        self.assertEqual((records, counts.padding, counts.invalid), ([], 0, 7))

    def test_leap_february_has_two_padding_slots_and_keeps_the_29th(self):
        records, counts = self.read(calendar_rows(2024, 2) + [padding(), padding()])
        self.assertEqual(records[-1][2], date(2024, 2, 29))
        self.assertEqual((counts.processed, counts.valid, counts.padding, counts.invalid), (31, 29, 2, 0))

    def test_nonleap_february_has_three_slots_and_rejects_the_29th(self):
        records, counts = self.read(calendar_rows(2023, 2) + [row("2023-02-29"), padding(), padding(), padding()], year=2023)
        self.assertEqual(records[-1][2], date(2023, 2, 28))
        self.assertEqual((counts.valid, counts.padding, counts.invalid), (28, 3, 1))

    def test_blank_date_needs_sentinels_complete_calendar_and_short_month(self):
        for rows, month in (([padding()], 2), (calendar_rows(2024, 1) + [padding()], 1),
                            (calendar_rows(2024, 4) + [row("")], 4), ([row("   ")], 2)):
            with self.subTest(month=month):
                _, counts = self.read(rows, month)
                self.assertEqual((counts.padding, counts.invalid), (0, 1))

    def test_padding_count_must_match_missing_calendar_days(self):
        for extra, expected in ((1, (0, 1)), (2, (2, 0)), (3, (2, 1))):
            with self.subTest(extra=extra):
                _, counts = self.read(calendar_rows(2024, 2) + [padding()] * extra)
                self.assertEqual((counts.padding, counts.invalid), expected)

    def test_padding_must_be_trailing_and_cover_every_county(self):
        rows = calendar_rows(2024, 4) + calendar_rows(2024, 4, "01005")
        with redirect_stderr(io.StringIO()):
            with self.assertRaisesRegex(weather.LoadError, "Faltan filas de relleno"):
                list(weather.read_weather(stream(rows + [padding()]), 2024, 4, weather.WeatherCounts()))
        records, counts = self.read(calendar_rows(2024, 4) + [padding(), row("2024-04-06", "01005")], 4)
        self.assertEqual((len(records), counts.invalid), (30, 1))

    def test_rejects_invalid_weather_values(self):
        examples = [
            ("N/A", "0", "1", "3"), ("NaN", "0", "1", "3"), ("inf", "0", "1", "3"),
            ("-999.99", "0", "1", "3"), ("1", "0", "2", "3"), ("1", "0", "1", "-0.01"),
            ("10000", "0", "1", "3"), ("1", "0", "1", "1000000"), ("0.123", "0", "0", "3"),
        ]
        records, counts = self.read([row("2024-02-01", measures=values) for values in examples])
        self.assertEqual((records, counts.invalid), ([], len(examples)))

    def test_duplicate_key_reports_invalid_even_with_different_values(self):
        rows = [row("2024-02-29"), row("2024-02-29", measures=("13", "0", "7", "2"))]
        records, counts = self.read(rows)
        self.assertEqual((len(records), counts.duplicates, counts.invalid), (1, 1, 1))

    def test_dry_run_reports_all_files_and_never_reads_database_config(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for month in range(2, 13):
                write_fixture(root / f"2024{month:02d}-scaled.csv", [row(date(2024, month, 1).isoformat())])
            report = root / "result.json"
            with patch.object(weather, "connection_options") as options, patch.object(weather, "database_connection") as connect:
                with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    status = weather.main(["--dry-run", "--input-dir", directory, "--report", str(report)])
            options.assert_not_called()
            connect.assert_not_called()
            payload = json.loads(report.read_text(encoding="utf-8"))
            self.assertEqual((status, payload["files_reported"], payload["summary"]["valid"]), (1, 12, 11))
            self.assertEqual(payload["files"][0]["error"], "FileNotFoundError")
            self.assertEqual(payload["files"][1]["status"], "passed")
