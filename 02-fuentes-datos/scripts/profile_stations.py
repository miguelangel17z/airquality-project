#!/usr/bin/env python3
"""Calculate the complete station profile for the MariaDB source schema."""

import argparse
import csv
import re
import sys
from collections import Counter
from decimal import Decimal
from pathlib import Path
from zipfile import ZipFile

from source_tools import (AIRDATA, ARCHIVE_ERRORS, EPA_BASE, STATIONS_ZIP, SourceError, check_crc,
                          emit_report, error_message, sha256_file, timestamp)
from stations_data import CODE_SYNTAX, KEY_LENGTH, NAMES, STATION_COLUMNS, convert_value, station_rows


AQS_CODE_URL = "https://aqs.epa.gov/aqsweb/documents/codetables/states_and_counties.html"
MET_STATE_INDEX = NAMES.index("met_state_code")
MET_COUNTY_INDEX = NAMES.index("met_county_code")


def compare_geography(path, station_pairs, met_pairs):
    page = path.read_text(encoding="utf-8-sig")
    published = set()
    for table_row in re.findall(r"<tr\b[^>]*>(.*?)</tr>", page, flags=re.IGNORECASE | re.DOTALL):
        cells = re.findall(r"<td\b[^>]*>\s*([^<>]*?)\s*</td>", table_row, flags=re.IGNORECASE)
        if len(cells) != 6:
            continue
        state, county = cells[0], cells[3]
        if CODE_SYNTAX["state_code"].fullmatch(state) and CODE_SYNTAX["county_code"].fullmatch(county):
            published.add((state, county))
    if not published:
        raise SourceError("No se encontraron pares de códigos en la tabla de referencia de AQS.")
    return {
        "reference_url": AQS_CODE_URL,
        "reference_sha256": sha256_file(path),
        "reference_pair_count": len(published),
        "station_pairs_not_listed": {"-".join(pair): station_pairs[pair] for pair in sorted(station_pairs.keys() - published)},
        "met_pairs_not_listed": {"-".join(pair): met_pairs[pair] for pair in sorted(met_pairs.keys() - published)},
    }


def profile_stations(path, reference_path=None):
    columns = {column.name: {
        "header": column.header, "mariadb_type": column.sql_type,
        "null_count": 0, "invalid_count": 0, "max_chars": 0,
        "max_text_chars": 0 if column.sql_type.startswith("VARCHAR") else None,
        "with_surrounding_whitespace": 0,
    } for column in STATION_COLUMNS}
    min_max = {}
    keys = Counter()
    states = Counter()
    distinct_records = set()
    directions = Counter()
    met_pairs = Counter()
    invalid_code_rows = 0
    with ZipFile(path) as archive:
        check_crc(archive)
        for row in station_rows(archive):
            keys[row[:KEY_LENGTH]] += 1
            states[row[0]] += 1
            distinct_records.add(row)
            met_state, met_county = row[MET_STATE_INDEX], row[MET_COUNTY_INDEX]
            if met_state and met_county:
                met_pairs[(met_state, met_county)] += 1
            has_invalid_code = False
            for column, raw in zip(STATION_COLUMNS, row, strict=True):
                stats = columns[column.name]
                stats["null_count"] += raw == ""
                stats["max_chars"] = max(stats["max_chars"], len(raw))
                stats["with_surrounding_whitespace"] += raw != raw.strip()
                if stats["max_text_chars"] is not None:
                    stats["max_text_chars"] = stats["max_chars"]
                try:
                    value = convert_value(column, raw)
                except ValueError:
                    stats["invalid_count"] += 1
                    has_invalid_code |= column.name.removeprefix("met_") in CODE_SYNTAX
                    continue
                if value is None:
                    continue
                if not isinstance(value, str):
                    low, high = min_max.get(column.name, (value, value))
                    min_max[column.name] = (min(low, value), max(high, value))
                if isinstance(value, Decimal):
                    scale = max(0, -value.as_tuple().exponent)
                    stats["max_fractional_digits"] = max(stats.get("max_fractional_digits", 0), scale)
                if column.name == "met_site_direction":
                    directions[value] += 1
            invalid_code_rows += has_invalid_code
    for name, (minimum, maximum) in min_max.items():
        columns[name].update(min=str(minimum), max=str(maximum))
    records = keys.total()
    valid = records > 0 and records == len(keys) and not any(c["invalid_count"] for c in columns.values())
    geography = None
    if reference_path is not None:
        station_pairs = Counter()
        for (state, county, _), count in keys.items():
            station_pairs[(state, county)] += count
        geography = compare_geography(reference_path, station_pairs, met_pairs)
    return {
        "status": "passed" if valid else "failed",
        "checked_at_utc": timestamp(),
        "source_url": EPA_BASE + STATIONS_ZIP,
        "archive_name": path.name,
        "archive_sha256": sha256_file(path),
        "csv_member": "aqs_sites.csv",
        "records": records,
        "distinct_station_keys": len(keys),
        "duplicate_station_keys": sum(count > 1 for count in keys.values()),
        "duplicate_key_rows": records - len(keys),
        "identical_duplicate_rows": records - len(distinct_records),
        "invalid_code_rows": invalid_code_rows,
        "aqs_geography_reference": geography,
        "state_code_counts": dict(sorted(states.items())),
        "met_site_direction_counts": dict(sorted(directions.items())),
        "columns": columns,
    }


def main():
    parser = argparse.ArgumentParser(description="Perfila todas las filas del catálogo de estaciones de AirData.")
    parser.add_argument("--archive", type=Path, default=AIRDATA / STATIONS_ZIP)
    parser.add_argument("--aqs-codes", type=Path, help="Copia HTML de la tabla oficial AQS para contrastar geografía.")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    try:
        result = profile_stations(args.archive, args.aqs_codes)
        emit_report(result, args.report)
        return 0 if result["status"] == "passed" else 1
    except (*ARCHIVE_ERRORS, OSError, csv.Error, UnicodeError, SourceError) as error:
        print(error_message(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
