"""Station columns and lossless conversion to the MariaDB source schema."""

import csv
import io
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

from source_tools import SourceError, csv_member


@dataclass(frozen=True)
class Column:
    header: str
    name: str
    sql_type: str


STATION_COLUMNS = (
    Column("State Code", "state_code", "VARCHAR(2)"),
    Column("County Code", "county_code", "VARCHAR(3)"),
    Column("Site Number", "site_number", "VARCHAR(4)"),
    Column("Latitude", "latitude", "DECIMAL(10,7)"),
    Column("Longitude", "longitude", "DECIMAL(10,7)"),
    Column("Datum", "datum", "VARCHAR(16)"),
    Column("Elevation", "elevation_m", "DECIMAL(20,12)"),
    Column("Land Use", "land_use", "VARCHAR(64)"),
    Column("Location Setting", "location_setting", "VARCHAR(64)"),
    Column("Site Established Date", "site_established_date", "DATE"),
    Column("Site Closed Date", "site_closed_date", "DATE"),
    Column("Met Site State Code", "met_state_code", "VARCHAR(2)"),
    Column("Met Site County Code", "met_county_code", "VARCHAR(3)"),
    Column("Met Site Site Number", "met_site_number", "VARCHAR(4)"),
    Column("Met Site Type", "met_site_type", "VARCHAR(64)"),
    Column("Met Site Distance", "met_distance_m", "DECIMAL(16,6)"),
    Column("Met Site Direction", "met_site_direction", "VARCHAR(8)"),
    Column("GMT Offset", "gmt_offset_hours", "DECIMAL(6,3)"),
    Column("Owning Agency", "owning_agency", "VARCHAR(128)"),
    Column("Local Site Name", "local_site_name", "VARCHAR(128)"),
    Column("Address", "address", "VARCHAR(255)"),
    Column("Zip Code", "postal_code", "VARCHAR(16)"),
    Column("State Name", "state_name", "VARCHAR(64)"),
    Column("County Name", "county_name", "VARCHAR(128)"),
    Column("City Name", "city_name", "VARCHAR(128)"),
    Column("CBSA Name", "cbsa_name", "VARCHAR(128)"),
    Column("Tribe Name", "tribe_name", "VARCHAR(128)"),
    Column("Extraction Date", "extraction_date", "DATE"),
)
KEY_LENGTH = 3
HEADERS = tuple(column.header for column in STATION_COLUMNS)
NAMES = tuple(column.name for column in STATION_COLUMNS)
KEY_COLUMNS = NAMES[:KEY_LENGTH]
CODE_SYNTAX = {
    "state_code": re.compile(r"(?:[0-7][0-9]|80|CC|TT)"),
    "county_code": re.compile(r"[0-9]{3}"),
    "site_number": re.compile(r"[0-9]{4}"),
}
DATE_SYNTAX = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
DECIMAL_SYNTAX = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?")


def station_rows(archive):
    member = csv_member(archive, "aqs_sites.csv")
    with archive.open(member) as stream, io.TextIOWrapper(stream, encoding="utf-8-sig", newline="") as text:
        reader = csv.reader(text, strict=True)
        if tuple(next(reader, ())) != HEADERS:
            raise SourceError("El encabezado de estaciones difiere del esquema aprobado de 28 campos.")
        for row in reader:
            if len(row) != len(HEADERS):
                raise SourceError(f"Registro de estaciones con ancho incorrecto cerca de la línea {reader.line_num}.")
            yield tuple(row)


def convert_value(column, raw):
    if raw == "":
        if column.name in KEY_COLUMNS:
            raise ValueError("missing key")
        return None
    key_pattern = CODE_SYNTAX.get(column.name.removeprefix("met_"))
    if key_pattern is not None and not key_pattern.fullmatch(raw):
        raise ValueError("invalid AQS code syntax")
    kind, _, size = column.sql_type.partition("(")
    if kind == "VARCHAR":
        if len(raw) > int(size[:-1]):
            raise ValueError("text exceeds SQL column size")
        return raw
    if kind == "DATE":
        if not DATE_SYNTAX.fullmatch(raw):
            raise ValueError("date is not in ISO format")
        value = date.fromisoformat(raw)
        if value.year < 1000:
            raise ValueError("date outside MariaDB DATE range")
        return value

    precision, scale = map(int, size[:-1].split(","))
    if not DECIMAL_SYNTAX.fullmatch(raw):
        raise ValueError("invalid decimal")
    try:
        value = Decimal(raw)
    except InvalidOperation as error:
        raise ValueError("invalid decimal") from error
    if value.adjusted() >= precision - scale or value.as_tuple().exponent < -scale:
        raise ValueError("value would be rounded or overflow SQL column")
    return value


def typed_row(row, record_number):
    values = []
    for column, raw in zip(STATION_COLUMNS, row, strict=True):
        try:
            values.append(convert_value(column, raw))
        except ValueError as error:
            raise SourceError(f"Valor incompatible en el registro {record_number}, campo {column.name}.") from error
    return tuple(values)


def schema_sql():
    definitions = [f"    {column.name} {column.sql_type} "
                   f"{'NOT NULL' if column.name in KEY_COLUMNS else 'NULL'}"
                   for column in STATION_COLUMNS]
    definitions.append(f"    PRIMARY KEY ({', '.join(KEY_COLUMNS)})")
    columns = ",\n".join(definitions)
    return (
        "CREATE DATABASE IF NOT EXISTS air_quality_source\n"
        "    DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_bin;\n\n"
        "CREATE TABLE IF NOT EXISTS air_quality_source.stations (\n"
        f"{columns}\n"
        ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;\n"
    )
