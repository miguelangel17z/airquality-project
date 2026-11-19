#!/usr/bin/env python3
"""Validate and load NOAA EpiNOAA county CSV files, one transaction per month."""

import argparse
import calendar
import csv
import hashlib
import io
import json
import re
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from load_daily_aqi import (
    MAX_LOGGED_INVALID_ROWS, Counts, InvalidRecord, LoadError, connection_options,
    database_connection, geographic_code, measurement_date, write_records,
)


SOURCE_URL = "https://noaa-nclimgrid-daily-pds.s3.amazonaws.com/EpiNOAA/v1-0-0/csv/cty/"
DEFAULT_INPUT_DIR = Path.home() / "airquality-project/data/raw/noaa"
YEAR = 2024
TABLE = "daily_weather_by_county"
COLUMNS = ("state_code", "county_code", "measurement_date", "tmax_c", "tmin_c", "tavg_c", "precipitation_mm")
HEADERS = ("region_type", "fips", "ncei_code", "state_name", "postal_code", "region_name",
           "date", "tmax", "tmin", "tavg", "prcp")
MEASUREMENT_FIELDS = HEADERS[7:]
PADDING_VALUE = "-999.99"
PADDING_NUMBER = Decimal(PADDING_VALUE)
DECIMAL_FORMAT = re.compile(r"[+-]?[0-9]+(?:\.[0-9]+)?")
CENT = Decimal("0.01")


@dataclass
class WeatherCounts(Counts):
    valid: int = 0
    padding: int = 0
    duplicates: int = 0


def parse_decimal(raw, field):
    value = raw.strip()
    if not DECIMAL_FORMAT.fullmatch(value):
        raise InvalidRecord(f"{field} requiere una medida decimal válida.")
    number = Decimal(value)
    limit = 1_000_000 if field == "prcp" else 10_000
    if number.copy_abs() >= limit or number == PADDING_NUMBER:
        raise InvalidRecord(f"{field} excede el rango o la precisión admitidos, o es un valor centinela.")
    exact = number.quantize(CENT)
    if number != exact:
        raise InvalidRecord(f"{field} requiere un valor representable con dos decimales.")
    return exact


def weather_values(values):
    tmax, tmin, tavg, prcp = (parse_decimal(value, field) for value, field in zip(values, MEASUREMENT_FIELDS))
    if prcp < 0:
        raise InvalidRecord("prcp debe ser no negativa.")
    if not tmin <= tavg <= tmax:
        raise InvalidRecord("Las temperaturas deben cumplir tmin <= tavg <= tmax.")
    return tmax, tmin, tavg, prcp


def report_invalid(counts, message, rows=1):
    if counts.invalid < MAX_LOGGED_INVALID_ROWS:
        print(message, file=sys.stderr)
    counts.invalid += rows


def read_weather(stream, year, month, counts):
    last_day = calendar.monthrange(year, month)[1]
    full_calendar = (1 << last_day) - 1
    expected_padding = 31 - last_day
    # One bit per calendar day per FIPS is sufficient for duplicate and padding checks.
    seen_days = {}
    padding_slots = {}
    reader = csv.reader(stream, strict=True)

    try:
        if tuple(next(reader, ())) != HEADERS:
            raise LoadError("El encabezado no coincide con EpiNOAA diario por condado.")
        for row in reader:
            counts.processed += 1
            try:
                if len(row) != len(HEADERS) or row[0] != "cty":
                    raise InvalidRecord("Se esperaban 11 campos de una fila de tipo cty.")
                fips = geographic_code(row[1], 5, "fips")
                observed = seen_days.get(fips, 0)
                raw_date, *values = row[6:]
                if raw_date == "":
                    if (not expected_padding or observed != full_calendar
                            or any(value.strip() != PADDING_VALUE for value in values)
                            or padding_slots.get(fips, 0) >= expected_padding):
                        raise InvalidRecord("La fila sin fecha no corresponde al relleno del calendario EpiNOAA.")
                    padding_slots[fips] = padding_slots.get(fips, 0) + 1
                    continue
                day = measurement_date(raw_date)
                if (day.year, day.month) != (year, month):
                    raise InvalidRecord("La fecha no pertenece al año y mes del archivo.")
                day_bit = 1 << (day.day - 1)
                if observed & day_bit:
                    counts.duplicates += 1
                    raise InvalidRecord("La clave FIPS-fecha está repetida dentro del archivo.")
                if padding_slots:
                    raise InvalidRecord("Hay observaciones con fecha después del inicio del relleno.")
                measures = weather_values(values)
            except InvalidRecord as error:
                report_invalid(counts, f"Línea {reader.line_num} — {error}")
                continue
            seen_days[fips] = observed | day_bit
            counts.valid += 1
            yield (fips[:2], fips[2:], day, *measures)
    except csv.Error as error:
        raise LoadError(f"CSV mal formado cerca de la línea {reader.line_num}.") from error

    for slots in padding_slots.values():
        if slots == expected_padding:
            counts.padding += slots
        else:
            report_invalid(counts, "Bloque de relleno incompleto para un condado.", rows=slots)
    if padding_slots and len(padding_slots) != len(seen_days):
        raise LoadError("Faltan filas de relleno para uno o más condados del archivo.")
    if not counts.processed:
        raise LoadError("El archivo contiene el encabezado y cero registros.")


def process_month(path, month, batch_size, dry_run, options):
    print(f"Validando {path.name}...", file=sys.stderr, flush=True)
    start = time.perf_counter()
    counts = WeatherCounts()
    size = sha256 = error_message = None
    inserted = existing = 0
    try:
        with path.open("rb") as raw:
            digest = hashlib.sha256()
            for block in iter(lambda: raw.read(1024 * 1024), b""):
                digest.update(block)
            size, sha256 = raw.tell(), digest.hexdigest()
            raw.seek(0)
            with io.TextIOWrapper(raw, encoding="utf-8-sig", newline="") as stream:
                records = read_weather(stream, YEAR, month, counts)
                if dry_run:
                    for _ in records:
                        pass
                else:
                    with database_connection(options) as connection:
                        inserted, existing = write_records(connection, records, batch_size, counts,
                                                           table=TABLE, columns=COLUMNS)
    except (LoadError, OSError, UnicodeError) as error:
        error_message = str(error) if isinstance(error, LoadError) else type(error).__name__
        print(f"Error en {path.name} — {error_message}", file=sys.stderr)

    errors = counts.invalid + (error_message is not None)
    return {"file": path.name, "source_url": SOURCE_URL + path.name, "bytes": size, "sha256": sha256,
            "status": "failed" if errors else "passed", **asdict(counts), "inserted": inserted,
            "already_present": existing, "errors": errors, "error": error_message,
            "elapsed_seconds": round(time.perf_counter() - start, 3)}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Valida y carga los doce archivos NOAA EpiNOAA scaled de 2024.")
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT_DIR)
    parser.add_argument("--batch-size", type=int, default=1000)
    parser.add_argument("--dry-run", action="store_true", help="Valida los archivos sin conectarse a MariaDB.")
    parser.add_argument("--report", type=Path, help="Guarda también el resumen y los resultados mensuales en JSON.")
    args = parser.parse_args(argv)
    if args.batch_size < 1:
        parser.error("--batch-size debe ser mayor que cero.")
    if args.report and args.report.suffix.lower() != ".json":
        parser.error("--report debe indicar un archivo .json.")

    start = time.perf_counter()
    files = []
    error_message = None
    interrupted = False
    try:
        options = None if args.dry_run else connection_options()
        for month in range(1, 13):
            path = args.input_dir.expanduser() / f"{YEAR}{month:02d}-scaled.csv"
            files.append(process_month(path, month, args.batch_size, args.dry_run, options))
    except (LoadError, OSError, UnicodeError, EOFError, KeyboardInterrupt) as error:
        interrupted = isinstance(error, KeyboardInterrupt)
        error_message = str(error) if isinstance(error, LoadError) else type(error).__name__
        print(f"Error — {error_message}", file=sys.stderr)

    totals = {field: sum(result[field] for result in files) for field in
              ("processed", "valid", "padding", "invalid", "duplicates", "inserted", "already_present", "errors")}
    totals["errors"] += error_message is not None
    failed = totals["errors"] > 0
    report = {"status": "failed" if failed else "passed", "mode": "dry-run" if args.dry_run else "load",
              "year": YEAR, "batch_size": args.batch_size, "expected_files": 12, "files_reported": len(files),
              "summary": totals, "files": files, "error": error_message,
              "finished_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "elapsed_seconds": round(time.perf_counter() - start, 3)}
    output = json.dumps(report, ensure_ascii=True, indent=2)
    print(output)
    if args.report:
        try:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(output + "\n", encoding="utf-8")
        except OSError as error:
            print(f"No se pudo guardar el reporte — {type(error).__name__}", file=sys.stderr)
            failed = True
    return 130 if interrupted else (1 if failed else 0)


if __name__ == "__main__":
    sys.exit(main())
