import calendar
import csv
import json
import sys
import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

from pyspark.sql import SparkSession


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import etl_raw_to_trusted as etl


def epa_row(day="2024-02-29", county="003", aqi="41"):
    return ("Alabama", "County", "01", county, day, aqi, "Good", "Ozone", "01-003-0010", "2")


def noaa_month(month, fips="01003", year=2024):
    days = calendar.monthrange(year, month)[1]
    rows = [("cty", fips, "1003", "Alabama", "AL", "County", date(year, month, day).isoformat(),
             " 12.34", " -1.20", " 5.57", " 0.00") for day in range(1, days + 1)]
    rows.extend([("cty", fips, "1003", "Alabama", "AL", "County", "", *([" -999.99"] * 4))] * (31 - days))
    return rows


def write_csv(path, headers, rows, encoding="utf-8"):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding=encoding, newline="") as stream:
        csv.writer(stream).writerows((headers, *rows))


class TrustedETLTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.spark = (SparkSession.builder.master("local[2]").appName("airquality-trusted-tests")
                     .config("spark.ui.enabled", "false").config("spark.sql.shuffle.partitions", "2").getOrCreate())
        cls.spark.sparkContext.setLogLevel("ERROR")

    @classmethod
    def tearDownClass(cls):
        cls.spark.stop()

    def spark_frame(self, rows, columns):
        return self.spark.createDataFrame(rows, etl.text_schema(columns))

    def weather(self, rows, month, year=2024):
        return self.spark_frame([(*row, f"{year}{month:02d}-scaled.csv") for row in rows], (*etl.NOAA_COLUMNS, "_file"))

    def test_epa_types_and_ascii_geography(self):
        metrics = {}
        result = etl.process_epa(self.spark_frame([epa_row()], etl.EPA_COLUMNS), 2024, metrics)
        row = result.first()
        self.assertEqual((row.state_code, row.county_code, row.measurement_date, row.aqi),
                         ("01", "003", date(2024, 2, 29), 41))
        self.assertEqual(dict(result.dtypes)["sites_reporting"], "int")
        self.assertEqual((metrics["input_rows"], metrics["duplicate_rows"]), (1, 0))

    def test_epa_rejects_duplicate_and_invalid_values(self):
        for records, invalid, duplicate in (([epa_row(), epa_row(aqi="33")], 0, 1),
                                             ([epa_row(aqi=""), epa_row(day="2024-02-30", county="005")], 2, 0)):
            metrics = {}
            with self.subTest(invalid=invalid), self.assertRaises(etl.QualityError):
                etl.process_epa(self.spark_frame(records, etl.EPA_COLUMNS), 2024, metrics)
            self.assertEqual((metrics["invalid_rows"], metrics["duplicate_rows"]), (invalid, duplicate))
            self.assertEqual(metrics["missing_values"]["aqi"], int(bool(invalid)))

    def test_noaa_february_leap_and_non_leap(self):
        for year, date_count in ((2024, 29), (2023, 28)):
            metrics = {}
            with self.subTest(year=year):
                frame = etl.process_noaa(self.weather(noaa_month(2, year=year), 2, year), year, 1, metrics)
                self.assertEqual(etl.date_bounds(frame)["date_max"], date(year, 2, date_count))
                self.assertEqual(metrics["padding_discarded"], 31 - date_count)
                self.assertEqual(metrics["missing_values"]["date"], 31 - date_count)
                self.assertEqual((frame.first().fips, frame.first().tmin_c), ("01003", Decimal("-1.20")))

    def test_noaa_rejects_false_padding_and_impossible_dates(self):
        for value in ("", "2024-02-30"):
            rows = noaa_month(2)
            corrupted = list(rows[-1])
            corrupted[6] = value
            if not value:
                corrupted[-1] = "0.00"
            rows[-1] = tuple(corrupted)
            metrics = {}
            with self.subTest(value=value), self.assertRaises(etl.QualityError):
                etl.process_noaa(self.weather(rows, 2), 2024, 1, metrics)
            self.assertEqual(metrics["invalid_rows"], 1)
            self.assertEqual(metrics["padding_discarded"], 0)

    def test_noaa_checks_count_by_fips_and_rejects_duplicates(self):
        rows = noaa_month(4)
        for invalid_rows, expected_duplicates in ((rows[:-1], 0), (rows + [rows[0]], 1)):
            metrics = {}
            with self.subTest(duplicate_rows=expected_duplicates), self.assertRaises(etl.QualityError):
                etl.process_noaa(self.weather(invalid_rows, 4), 2024, 1, metrics)
            self.assertEqual(metrics["duplicate_rows"], expected_duplicates)
            self.assertEqual(metrics["padding_discarded"], 0)

    def test_census_encoding_county_filter_and_key_uniqueness(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "census.csv"
            headers = ("STATE", "COUNTY", "SUMLEV", "POPESTIMATE2024", "CTYNAME")
            record = ("35", "013", "050", "210000", "Doña Ana")
            write_csv(path, headers, [("35", "000", "040", "1", "New Mexico"), record], encoding="cp1252")
            source = etl.read_csv(self.spark, path.as_uri(), encoding="windows-1252")
            self.assertEqual(source.where("SUMLEV = '050'").first().CTYNAME, "Doña Ana")
            metrics = {}
            result = etl.process_census(source, 2024, metrics)
            self.assertEqual(result.first().fips, "35013")
            self.assertEqual(dict(result.dtypes)["population_estimate"], "bigint")
            self.assertEqual((metrics["input_rows"], metrics["output_rows"]), (2, 1))
            with self.assertRaises(etl.QualityError):
                etl.process_census(self.spark_frame([record, record], headers), 2024, {})

    def test_end_to_end_left_join_parquet_and_repeat_protection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "raw inputs"
            epa, noaa, census = root / "epa.csv", root / "noaa", root / "census.csv"
            write_csv(epa, etl.EPA_COLUMNS, [epa_row(), epa_row("2024-01-01", "005"), epa_row(county="007")])
            for month in range(1, 13):
                write_csv(noaa / f"2024{month:02d}-scaled.csv", etl.NOAA_COLUMNS,
                          noaa_month(month) + noaa_month(month, "01005"))
            write_csv(census, ("STATE", "COUNTY", "SUMLEV", "POPESTIMATE2024"), [("01", "003", "050", "9000")])
            output, report = root / "trusted", root / "quality" / "report.json"
            arguments = etl.parse_args(["--epa-input", epa.as_uri(), "--noaa-input", noaa.as_uri(),
                                       "--census-input", census.as_uri(), "--output-root", output.as_uri(),
                                       "--report-path", report.as_uri(), "--expected-joined", "2"])
            summary = etl.run_etl(self.spark, arguments)
            self.assertEqual(summary["status"], "passed")
            self.assertEqual(summary["datasets"]["aqi_weather"]["unmatched_census_rows"], 1)
            rows = self.spark.read.parquet((output / "aqi_weather").as_posix()).select("county_code", "population_estimate").orderBy("county_code").collect()
            self.assertEqual([tuple(row) for row in rows], [("003", 9000), ("005", None)])
            self.assertTrue((output / "noaa" / "year=2024" / "month=2").is_dir())
            self.assertTrue((output / "census" / "year=2024").is_dir())
            before = report.read_bytes()
            self.assertEqual(json.loads(before)["datasets"]["epa"]["output_rows"], 3)
            with self.assertRaises(FileExistsError):
                etl.run_etl(self.spark, arguments)
            self.assertEqual(report.read_bytes(), before)

    def test_quality_failure_writes_report_without_creating_parquet(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            epa = root / "epa.csv"
            write_csv(epa, etl.EPA_COLUMNS, [epa_row(aqi="")])
            output = root / "trusted"
            report = root / "quality.json"
            args = etl.parse_args(["--epa-input", epa.as_uri(), "--noaa-input", root.as_uri(),
                                   "--census-input", root.as_uri(), "--output-root", output.as_uri(),
                                   "--report-path", report.as_uri()])
            with self.assertRaises(etl.QualityError):
                etl.run_etl(self.spark, args)
            result = json.loads(report.read_bytes())
            self.assertEqual((result["status"], result["stage"]), ("failed", "epa"))
            self.assertEqual(result["datasets"]["epa"]["missing_values"]["aqi"], 1)
            self.assertFalse(output.exists())

    def test_rejects_raw_s3_and_report_inside_parquet_before_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            epa = root / "trusted" / "epa"
            with self.assertRaisesRegex(ValueError, "fuera de S3 Raw"):
                etl.check_outputs(self.spark, {"epa": "s3://dummy-bucket/raw/epa"}, (root / "report.json").as_uri())
            with self.assertRaisesRegex(ValueError, "fuera de los directorios Parquet"):
                etl.check_outputs(self.spark, {"epa": epa.as_uri()}, (epa / "report.json").as_uri())
            self.assertFalse(epa.exists())


class GlueArgumentsTests(unittest.TestCase):
    def test_glue_aliases_and_internal_arguments(self):
        args = etl.parse_args(["--JOB_NAME", "test", "--epa_input", "data/epa", "--noaa_input", "data/noaa",
                               "--census_input", "data/census", "--output_root", "trusted",
                               "--report_path", "quality/run.json", "--expected_joined", "2",
                               "--job-bookmark-option", "job-bookmark-disable"])
        self.assertEqual((args.JOB_NAME, args.expected_joined), ("test", 2))
