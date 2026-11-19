#!/usr/bin/env python3
"""Validate and optionally load the EPA station snapshot into MariaDB."""

import argparse
import csv
import os
import ssl
import sys
from itertools import islice
from pathlib import Path
from zipfile import ZipFile

from source_tools import (AIRDATA, ARCHIVE_ERRORS, EPA_BASE, STATIONS_ZIP, SourceError, check_crc,
                          emit_report, error_message, sha256_file, timestamp)
from stations_data import KEY_COLUMNS, KEY_LENGTH, NAMES, station_rows, typed_row


TABLE = "air_quality_source.stations"
COLUMN_LIST = ", ".join(NAMES)
VALUE_MARKERS = ", ".join(["%s"] * len(NAMES))
INSERT = (f"INSERT INTO {TABLE} ({COLUMN_LIST}) VALUES ({VALUE_MARKERS}) "
          "ON DUPLICATE KEY UPDATE site_number = site_number")


def preflight(archive):
    check_crc(archive)
    keys = set()
    for number, row in enumerate(station_rows(archive), 1):
        key = typed_row(row, number)[:KEY_LENGTH]
        if key in keys:
            raise SourceError(f"Clave de estación repetida en el registro {number}.")
        keys.add(key)
    if not keys:
        raise SourceError("El CSV de estaciones está vacío.")
    return len(keys)


def connect(pymysql):
    required = ("MARIADB_HOST", "MARIADB_USER", "MARIADB_PASSWORD", "MARIADB_SSL_CA")
    missing = [variable for variable in required if not os.environ.get(variable)]
    if missing:
        raise SourceError("Faltan variables de entorno para MariaDB: " + ", ".join(missing))
    try:
        port = int(os.environ.get("MARIADB_PORT", "3306"))
    except ValueError as error:
        raise SourceError("MARIADB_PORT debe ser un entero entre 1 y 65535.") from error
    if not 1 <= port <= 65535:
        raise SourceError("MARIADB_PORT debe ser un entero entre 1 y 65535.")

    tls = ssl.create_default_context(cafile=os.environ["MARIADB_SSL_CA"])
    tls.minimum_version = ssl.TLSVersion.TLSv1_2
    return pymysql.connect(host=os.environ["MARIADB_HOST"], port=port, user=os.environ["MARIADB_USER"],
                           password=os.environ["MARIADB_PASSWORD"].encode("utf-8"),
                           database="air_quality_source", charset="utf8mb4", ssl=tls,
                           connect_timeout=10, read_timeout=30, write_timeout=30,
                           autocommit=False, sql_mode="STRICT_ALL_TABLES,NO_ZERO_IN_DATE,NO_ZERO_DATE")


def verify_batch(cursor, batch):
    key_markers = "(" + ", ".join(["%s"] * KEY_LENGTH) + ")"
    markers = ", ".join([key_markers] * len(batch))
    key_names = ", ".join(KEY_COLUMNS)
    cursor.execute(f"SELECT {COLUMN_LIST} FROM {TABLE} WHERE ({key_names}) IN ({markers}) FOR UPDATE",
                   tuple(value for row in batch for value in row[:KEY_LENGTH]))
    if set(cursor.fetchall()) != set(batch):
        raise SourceError("Hay claves con datos distintos en MariaDB. Se cancela la carga para preservar el origen.")


def insert_stations(connection, archive, batch_size):
    rows = (typed_row(row, number) for number, row in enumerate(station_rows(archive), 1))
    inserted = existing = 0
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT ENGINE FROM information_schema.TABLES "
                           "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s", ("air_quality_source", "stations"))
            if cursor.fetchone() != ("InnoDB",):
                raise SourceError("La tabla de estaciones debe existir y usar InnoDB para la carga transaccional.")
            while batch := list(islice(rows, batch_size)):
                affected = cursor.executemany(INSERT, batch)
                if not 0 <= affected <= len(batch):
                    raise SourceError("MariaDB devolvió un conteo de inserción inesperado.")
                if affected != len(batch):
                    verify_batch(cursor, batch)
                inserted += affected
                existing += len(batch) - affected
        connection.commit()
    except (Exception, KeyboardInterrupt):
        if connection.open:
            connection.rollback()
        raise
    return inserted, existing


def load(archive, batch_size):
    try:
        import pymysql
    except ImportError as error:
        raise SourceError("Instala requirements.txt para usar --execute con MariaDB.") from error
    try:
        with connect(pymysql) as connection:
            return insert_stations(connection, archive, batch_size)
    except pymysql.MySQLError as error:
        number = error.args[0] if error.args and type(error.args[0]) is int else "desconocido"
        raise SourceError(f"Falló la operación en MariaDB (código {number}).") from error


def main():
    parser = argparse.ArgumentParser(description="Valida estaciones o autoriza explícitamente su carga en MariaDB.")
    parser.add_argument("--archive", type=Path, default=AIRDATA / STATIONS_ZIP)
    parser.add_argument("--batch-size", type=int, default=500)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--validate-only", action="store_true", help="Valida sin conexión (predeterminado).")
    mode.add_argument("--execute", action="store_true", help="Ejecuta la carga en MariaDB con TLS verificado.")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    if not 1 <= args.batch_size <= 1000:
        parser.error("--batch-size debe estar entre 1 y 1000.")
    try:
        with ZipFile(args.archive) as archive:
            processed = preflight(archive)
            archive_digest = sha256_file(args.archive)
            inserted, existing = load(archive, args.batch_size) if args.execute else (0, None)
        emit_report({"status": "passed", "checked_at_utc": timestamp(),
                     "mode": "execute" if args.execute else "validate-only",
                     "source_url": EPA_BASE + STATIONS_ZIP, "archive_name": args.archive.name,
                     "archive_sha256": archive_digest, "processed": processed, "inserted": inserted,
                     "already_present": existing, "batch_size": args.batch_size}, args.report)
        return 0
    except (*ARCHIVE_ERRORS, OSError, csv.Error, UnicodeError, SourceError) as error:
        print(error_message(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
