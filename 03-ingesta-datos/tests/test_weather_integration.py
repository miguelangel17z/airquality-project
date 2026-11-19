import io
import os
import tempfile
import unittest
import uuid
from contextlib import redirect_stderr
from datetime import date
from decimal import Decimal
from pathlib import Path

import test_load_daily_weather as fixtures

import load_daily_aqi as aqi
import load_daily_weather as weather


SQL_DIR = Path(__file__).resolve().parents[1] / "sql"


def execute_script(cursor, name, schema):
    sql = (SQL_DIR / name).read_text(encoding="utf-8").replace("airquality", schema)
    results = []
    for statement in sql.split(";"):
        if statement.strip():
            cursor.execute(statement)
            if cursor.description:
                results.append(cursor.fetchone())
    return results


@unittest.skipUnless(os.environ.get("AIRQUALITY_TEST_DB_PORT"), "Requiere MariaDB local con TLS en localhost.")
class MariaDBWeatherIntegrationTests(unittest.TestCase):
    def test_load_replay_monthly_rollback_view_and_coverage(self):
        options = aqi.connection_options()
        options.update(host="localhost", port=int(os.environ["AIRQUALITY_TEST_DB_PORT"]), database=None)
        schema = "ingest_test_" + uuid.uuid4().hex
        with aqi.database_connection(options) as admin, admin.cursor() as cursor:
            admin.autocommit(True)
            cursor.execute(f"CREATE DATABASE `{schema}`")
            try:
                execute_script(cursor, "create_schema.sql", schema)
                execute_script(cursor, "create_weather_schema.sql", schema)
                execute_script(cursor, "create_weather_schema.sql", schema)
                cursor.execute(f"SHOW INDEX FROM {schema}.daily_weather_by_county "
                               "WHERE Key_name = 'idx_daily_weather_measurement_date'")
                self.assertEqual(cursor.fetchone()[4], "measurement_date")
                self.assertEqual(execute_script(cursor, "verify_weather_integration.sql", schema)[2],
                                 (0, 0, None, None, None))
                self.check_load(cursor, schema, {**options, "database": schema})
            finally:
                cursor.execute(f"DROP DATABASE `{schema}`")

    def check_load(self, cursor, schema, options):
        epa_rows = [("Alabama", "County", "01", county, day, 41, "Good", "Ozone", "01-003-0010", 2)
                    for county, day in (("003", date(2024, 1, 1)), ("003", date(2024, 2, 29)),
                                        ("005", date(2024, 1, 1)))]
        with aqi.database_connection(options) as connection:
            self.assertEqual(aqi.write_records(connection, iter(epa_rows), 2, aqi.Counts()), (3, 0))

        with tempfile.TemporaryDirectory() as directory, redirect_stderr(io.StringIO()):
            folder = Path(directory)
            january = folder / "202401-scaled.csv"
            february = folder / "202402-scaled.csv"
            march = folder / "202403-scaled.csv"
            fixtures.write_fixture(january, [fixtures.row("2024-01-01"), fixtures.row("2024-01-01", "01007")])
            fixtures.write_fixture(february, [fixtures.row("2024-02-29")])
            self.assertEqual(weather.process_month(january, 1, 2, False, options)["inserted"], 2)
            self.assertEqual(weather.process_month(february, 2, 2, False, options)["inserted"], 1)
            replay = weather.process_month(january, 1, 2, False, options)
            self.assertEqual((replay["inserted"], replay["already_present"]), (0, 2))
            fixtures.write_fixture(february, [fixtures.row("2024-02-01"),
                                               fixtures.row("2024-02-29", measures=("14", "1", "7", "2"))])
            conflict = weather.process_month(february, 2, 1, False, options)
            self.assertEqual((conflict["status"], conflict["inserted"]), ("failed", 0))
            self.assertIn("datos distintos", conflict["error"])
            fixtures.write_fixture(march, [fixtures.row("2024-03-01"), fixtures.row("2024-03-32")])
            invalid = weather.process_month(march, 3, 1, False, options)
            self.assertEqual((invalid["status"], invalid["invalid"], invalid["inserted"]), ("failed", 1, 0))

        cursor.execute(f"SELECT state_code, county_code, aqi, tmax_c FROM {schema}.v_aqi_weather_daily "
                       "ORDER BY measurement_date")
        self.assertEqual(cursor.fetchall(), (("01", "003", 41, Decimal("12.34")),) * 2)
        first, last = date(2024, 1, 1), date(2024, 2, 29)
        self.assertEqual(execute_script(cursor, "verify_weather_integration.sql", schema),
                         [(3, first, last), (2,), (3, 1, Decimal("66.67"), first, last)])
