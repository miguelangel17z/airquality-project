#!/usr/bin/env python3
"""Export the integrated EPA–NOAA view as a validated CSV and manifest."""

import argparse
import csv
import hashlib
import json
import os
import sys
import tempfile
import time
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "03-ingesta-datos" / "scripts"))
from load_daily_aqi import LoadError, database_connection


VIEW = "v_aqi_weather_daily"
COLUMNS = (
    "state_name", "county_name", "state_code", "county_code", "measurement_date",
    "aqi", "category", "defining_parameter", "defining_site", "sites_reporting",
    "tmax_c", "tmin_c", "tavg_c", "precipitation_mm",
)
TEXT_POSITIONS = (0, 1, 2, 3, 6, 7, 8)
INTEGER_POSITIONS = (5, 9)
DECIMAL_POSITIONS = (10, 11, 12, 13)
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "processed" / "aqi_weather_2024.csv"
FIRST_DAY = date(2024, 1, 1)
NEXT_YEAR = date(2025, 1, 1)
SELECT_SQL = (
    f"SELECT {', '.join(COLUMNS)} FROM {VIEW} "
    "WHERE measurement_date >= %s AND measurement_date < %s "
    "ORDER BY state_code, county_code, measurement_date"
)


def validate_record(row, number):
    if len(row) != len(COLUMNS):
        raise LoadError(f"Fila {number}: la vista no devolvió las 14 columnas esperadas.")
    for column, value in zip(COLUMNS, row):
        if value is None or (isinstance(value, str) and not value.strip()):
            raise LoadError(f"Fila {number}: falta un valor obligatorio en {column}.")

    for position in TEXT_POSITIONS:
        if not isinstance(row[position], str):
            raise LoadError(f"Fila {number}: {COLUMNS[position]} debe recibirse como texto.")
    for position, width in ((2, 2), (3, 3)):
        code = row[position]
        if len(code) != width or not code.isascii() or not code.isdecimal():
            raise LoadError(f"Fila {number}: {COLUMNS[position]} requiere {width} dígitos ASCII.")

    measured_on = row[4]
    if type(measured_on) is not date or not FIRST_DAY <= measured_on < NEXT_YEAR:
        raise LoadError(f"Fila {number}: measurement_date debe ser una fecha válida de 2024.")
    for position in INTEGER_POSITIONS:
        if type(row[position]) is not int or not 0 <= row[position] <= 65_535:
            raise LoadError(f"Fila {number}: {COLUMNS[position]} debe ser un entero sin signo de 16 bits.")
    for position in DECIMAL_POSITIONS:
        value = row[position]
        if not isinstance(value, Decimal) or not value.is_finite():
            raise LoadError(f"Fila {number}: {COLUMNS[position]} debe recibirse como un decimal finito.")
    return measured_on


def write_csv(cursor, stream, batch_size):
    if tuple(column[0] for column in cursor.description) != COLUMNS:
        raise LoadError("Las columnas de la consulta no coinciden con el contrato de exportación.")
    writer = csv.writer(stream)
    writer.writerow(COLUMNS)
    previous_key = None
    first_date = last_date = None
    count = 0

    while batch := cursor.fetchmany(batch_size):
        for row in batch:
            count += 1
            measured_on = validate_record(row, count)
            key = (row[2], row[3], measured_on)
            if key == previous_key:
                raise LoadError(f"Fila {count}: clave duplicada de estado, condado y fecha.")
            if previous_key is not None and key < previous_key:
                raise LoadError(f"Fila {count}: la vista no devolvió las claves en el orden esperado.")
            previous_key = key
            first_date = measured_on if first_date is None else min(first_date, measured_on)
            last_date = measured_on if last_date is None else max(last_date, measured_on)
            writer.writerow((*row[:4], measured_on.isoformat(), *row[5:10],
                             *(format(row[position], "f") for position in DECIMAL_POSITIONS)))
    return count, first_date, last_date


def stream_view(output, batch_size, options):
    with database_connection(options) as connection:
        from pymysql.cursors import SSCursor

        with connection.cursor() as setup:
            setup.execute("START TRANSACTION READ ONLY")
        with connection.cursor(SSCursor) as cursor, output.open("w", encoding="utf-8", newline="") as stream:
            cursor.execute(SELECT_SQL, (FIRST_DAY, NEXT_YEAR))
            return write_csv(cursor, stream, batch_size)


def file_metadata(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
        return stream.tell(), digest.hexdigest()


def export_dataset(output, batch_size=1000, expected_rows=None, options=None):
    start = time.perf_counter()
    manifest_path = output.with_suffix(".manifest.json")
    if output.exists() or manifest_path.exists():
        raise LoadError("El CSV o su manifiesto ya existe. Elige otro destino de exportación.")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".aqi-weather-export-", dir=output.parent) as stage:
        staging = Path(stage)
        staged_csv = staging / output.name
        count, first_date, last_date = stream_view(staged_csv, batch_size, options)
        if expected_rows is not None and count != expected_rows:
            raise LoadError(f"Se esperaban {expected_rows} filas y se exportaron {count}.")
        size, digest = file_metadata(staged_csv)
        manifest = {
            "source_view": VIEW, "csv_file": output.name, "year": FIRST_DAY.year,
            "row_count": count, "columns": list(COLUMNS),
            "date_min": first_date.isoformat() if first_date else None,
            "date_max": last_date.isoformat() if last_date else None,
            "size_bytes": size, "sha256": digest,
            "elapsed_seconds": round(time.perf_counter() - start, 3),
            "exported_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        staged_manifest = staging / manifest_path.name
        staged_manifest.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        # Publish only completed artifacts, without overwriting a previous or concurrent export.
        os.link(staged_csv, output)
        try:
            os.link(staged_manifest, manifest_path)
        except OSError:
            output.unlink()
            raise
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description="Exporta la vista diaria EPA–NOAA de 2024 en CSV con manifiesto JSON.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--batch-size", type=int, default=1000)
    parser.add_argument("--expected-rows", type=int, help="Verifica el conteo antes de publicar los archivos.")
    args = parser.parse_args(argv)
    if args.output.suffix.lower() != ".csv":
        parser.error("--output debe terminar en .csv.")
    if args.batch_size < 1:
        parser.error("--batch-size debe ser mayor que cero.")
    if args.expected_rows is not None and args.expected_rows < 0:
        parser.error("--expected-rows debe ser un entero no negativo.")
    try:
        manifest = export_dataset(args.output.expanduser(), args.batch_size, args.expected_rows)
    except KeyboardInterrupt:
        print("Exportación interrumpida.", file=sys.stderr)
        return 130
    except (LoadError, OSError, EOFError) as error:
        message = str(error) if isinstance(error, LoadError) else type(error).__name__
        print(f"Error — {message}", file=sys.stderr)
        return 1
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
