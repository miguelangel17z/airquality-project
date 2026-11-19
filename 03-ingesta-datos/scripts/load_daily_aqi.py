#!/usr/bin/env python3
"""Validate and load the EPA AirData county-level daily AQI CSV."""

import argparse
import csv
import getpass
import json
import os
import re
import ssl
import sys
import time
import warnings
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from itertools import islice
from pathlib import Path


DEFAULT_CSV = Path.home() / "airquality-project/data/raw/epa/daily_aqi_by_county_2024.csv"
HEADERS = (
    "State Name", "County Name", "State Code", "County Code", "Date", "AQI",
    "Category", "Defining Parameter", "Defining Site", "Number of Sites Reporting",
)
EPA_HEADERS = (HEADERS[0], "county Name", *HEADERS[2:])
COLUMNS = (
    "state_name", "county_name", "state_code", "county_code", "measurement_date",
    "aqi", "category", "defining_parameter", "defining_site", "sites_reporting",
)
TABLE = "daily_aqi_by_county"
KEY_COLUMNS = COLUMNS[2:5]
MAX_LOGGED_INVALID_ROWS = 10
DATE_FORMAT = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


class LoadError(Exception):
    """An operational error suitable for display without sensitive details."""


class InvalidRecord(ValueError):
    """An input validation error that describes a field without printing its value."""


@dataclass
class Counts:
    processed: int = 0
    invalid: int = 0


def required_text(value, limit, field):
    if not value.strip() or len(value) > limit:
        raise InvalidRecord(f"{field} requiere texto de 1 a {limit} caracteres.")
    return value


def geographic_code(value, width, field):
    if len(value) != width or not value.isascii() or not value.isdecimal():
        raise InvalidRecord(f"{field} requiere exactamente {width} dígitos ASCII.")
    return value


def measurement_date(value):
    if DATE_FORMAT.fullmatch(value):
        try:
            parsed = date.fromisoformat(value)
            if parsed.year >= 1000:
                return parsed
        except ValueError:
            pass
    raise InvalidRecord("measurement_date requiere una fecha válida YYYY-MM-DD dentro del rango de MariaDB.")


def unsigned_smallint(value, field):
    if value.isascii() and value.isdecimal():
        digits = value.lstrip("0") or "0"
        if len(digits) <= 5:
            number = int(digits)
            if number <= 65_535:
                return number
    raise InvalidRecord(f"{field} requiere un entero sin signo de 0 a 65535.")


def parse_record(row):
    if len(row) != len(HEADERS):
        raise InvalidRecord("Se esperaban 10 campos.")
    return (
        required_text(row[0], 100, "state_name"),
        required_text(row[1], 100, "county_name"),
        geographic_code(row[2], 2, "state_code"),
        geographic_code(row[3], 3, "county_code"),
        measurement_date(row[4]),
        unsigned_smallint(row[5], "aqi"),
        required_text(row[6], 50, "category"),
        required_text(row[7], 50, "defining_parameter"),
        required_text(row[8], 20, "defining_site"),
        unsigned_smallint(row[9], "sites_reporting"),
    )


def read_records(stream, counts):
    reader = csv.reader(stream, strict=True)
    keys = set()
    try:
        if tuple(next(reader, ())) not in (HEADERS, EPA_HEADERS):
            raise LoadError("El encabezado del CSV no coincide con el formato AQI diario por condado.")
        for row in reader:
            counts.processed += 1
            try:
                record = parse_record(row)
                key = record[2:5]
                if key in keys:
                    raise InvalidRecord("La clave estado-condado-fecha está repetida en el CSV.")
                keys.add(key)
            except InvalidRecord as error:
                counts.invalid += 1
                if counts.invalid <= MAX_LOGGED_INVALID_ROWS:
                    print(f"Línea {reader.line_num} — {error}", file=sys.stderr)
                continue
            yield record
    except csv.Error as error:
        raise LoadError(f"CSV mal formado cerca de la línea {reader.line_num}.") from error
    if not counts.processed:
        raise LoadError("El CSV contiene el encabezado y cero registros.")


def connection_options():
    required = ("DB_HOST", "DB_USER")
    missing = [key for key in required if not os.environ.get(key)]
    if missing:
        raise LoadError("Configura las variables " + ", ".join(missing) + ".")
    try:
        port = int(os.environ.get("DB_PORT", "3306"))
        if not 1 <= port <= 65_535:
            raise ValueError
    except ValueError as error:
        raise LoadError("DB_PORT debe ser un entero de 1 a 65535.") from error
    password = os.environ.get("DB_PASSWORD")
    if password is None:
        if not sys.stdin.isatty():
            raise LoadError("Usa un terminal interactivo o configura DB_PASSWORD en el entorno del proceso.")
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            try:
                password = getpass.getpass("Contraseña de MariaDB: ")
            except getpass.GetPassWarning as error:
                raise LoadError("El terminal no permite ingresar la contraseña sin eco.") from error
    try:
        context = ssl.create_default_context(cafile=os.environ.get("DB_SSL_CA") or None)
    except OSError as error:
        raise LoadError("No se pudo preparar la CA para verificar MariaDB mediante TLS.") from error
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    return {
        "host": os.environ["DB_HOST"], "port": port, "database": os.environ.get("DB_NAME") or "airquality",
        "user": os.environ["DB_USER"], "password": password.encode("utf-8"), "ssl": context,
        "charset": "utf8mb4", "autocommit": False, "sql_mode": "STRICT_ALL_TABLES,NO_ZERO_IN_DATE,NO_ZERO_DATE",
        "connect_timeout": 10, "read_timeout": 30, "write_timeout": 30,
    }


@contextmanager
def database_connection(options=None):
    try:
        import pymysql
    except ImportError as error:
        raise LoadError("Instala requirements.txt para ejecutar la carga en MariaDB.") from error
    try:
        with pymysql.connect(**(connection_options() if options is None else options)) as connection:
            yield connection
    except pymysql.MySQLError as error:
        code = error.args[0] if error.args and type(error.args[0]) is int else "desconocido"
        raise LoadError(f"Falló la operación en MariaDB (código {code}).") from error


def verify_existing(cursor, batch, table, columns):
    markers = ", ".join(["(%s, %s, %s)"] * len(batch))
    positions = tuple(columns.index(key) for key in KEY_COLUMNS)
    keys = tuple(record[position] for record in batch for position in positions)
    cursor.execute(f"SELECT {', '.join(columns)} FROM {table} "
                   f"WHERE (state_code, county_code, measurement_date) IN ({markers}) FOR UPDATE", keys)
    if set(cursor.fetchall()) != set(batch):
        raise LoadError("Hay claves existentes con datos distintos. Se cancela la carga completa.")


def write_records(connection, records, batch_size, counts, *, table=TABLE, columns=COLUMNS):
    # Table and column identifiers come from the loaders, never from input or environment.
    markers = ", ".join(["%s"] * len(columns))
    insert_sql = (f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({markers}) "
                  "ON DUPLICATE KEY UPDATE state_code = state_code")
    inserted = 0
    existing = 0
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT ENGINE FROM information_schema.TABLES "
                           "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s", (table,))
            if cursor.fetchone() != ("InnoDB",):
                raise LoadError(f"La tabla {table} debe existir y usar InnoDB.")
            cursor.execute("SELECT COLUMN_NAME FROM information_schema.KEY_COLUMN_USAGE "
                           "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s "
                           "AND CONSTRAINT_NAME = 'PRIMARY' ORDER BY ORDINAL_POSITION", (table,))
            if tuple(row[0] for row in cursor.fetchall()) != KEY_COLUMNS:
                raise LoadError(f"La clave primaria de {table} debe ser estado, condado y fecha.")
            while batch := list(islice(records, batch_size)):
                if counts.invalid:
                    continue
                affected = cursor.executemany(insert_sql, batch)
                if not 0 <= affected <= len(batch):
                    raise LoadError("MariaDB devolvió un conteo de inserción inesperado.")
                if affected != len(batch):
                    verify_existing(cursor, batch, table, columns)
                inserted += affected
                existing += len(batch) - affected
        if counts.invalid:
            connection.rollback()
            return 0, 0
        connection.commit()
    except (Exception, KeyboardInterrupt):
        if connection.open:
            connection.rollback()
        raise
    return inserted, existing


def main(argv=None):
    parser = argparse.ArgumentParser(description="Valida y carga el AQI diario por condado en MariaDB.")
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    parser.add_argument("--batch-size", type=int, default=1000)
    parser.add_argument("--dry-run", action="store_true", help="Valida el archivo sin conectarse a MariaDB.")
    args = parser.parse_args(argv)
    if args.batch_size < 1:
        parser.error("--batch-size debe ser mayor que cero.")

    start = time.perf_counter()
    counts = Counts()
    inserted = existing = fatal = 0
    interrupted = False
    try:
        with args.csv.expanduser().open(encoding="utf-8-sig", newline="") as stream:
            records = read_records(stream, counts)
            if args.dry_run:
                for _ in records:
                    pass
            else:
                with database_connection() as connection:
                    inserted, existing = write_records(connection, records, args.batch_size, counts)
    except (LoadError, OSError, UnicodeError, EOFError, KeyboardInterrupt) as error:
        interrupted = isinstance(error, KeyboardInterrupt)
        fatal = 1
        message = str(error) if isinstance(error, LoadError) else type(error).__name__
        print(f"Error — {message}", file=sys.stderr)

    errors = counts.invalid + fatal
    print(json.dumps({
        "status": "failed" if errors else "passed", "mode": "dry-run" if args.dry_run else "load",
        "processed": counts.processed, "inserted": inserted, "already_present": existing,
        "invalid_records": counts.invalid, "errors": errors, "elapsed_seconds": round(time.perf_counter() - start, 3),
    }))
    return 130 if interrupted else (1 if errors else 0)


if __name__ == "__main__":
    sys.exit(main())
