#!/usr/bin/env python3
"""Validate original EPA AirData archives and record reproducible evidence."""

import argparse
import csv
import hashlib
import http.client
import io
import json
import math
import shutil
import ssl
import sys
import zlib
from dataclasses import dataclass
from datetime import datetime, timezone
from itertools import islice
from pathlib import Path, PurePosixPath
from tempfile import NamedTemporaryFile
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from zipfile import BadZipFile, ZipFile


MODULE_DIR = Path(__file__).resolve().parents[1]
EPA_BASE = "https://aqs.epa.gov/aqsweb/airdata/"
CATALOG_URL = EPA_BASE + "download_files.html"
GEOGRAPHIC_COLUMNS = ("State Code", "County Code")
CACHE_ERRORS = (OSError, EOFError, RuntimeError, ValueError, BadZipFile, csv.Error, zlib.error)
NETWORK_ERRORS = (OSError, http.client.HTTPException, ValueError)


@dataclass(frozen=True)
class Source:
    name: str
    archive: str
    published_rows: int
    required_columns: tuple[str, ...]
    sampled_columns: tuple[str, ...]

    @property
    def url(self):
        return EPA_BASE + self.archive


SOURCES = (
    Source("stations", "aqs_sites.zip", 20_994,
           GEOGRAPHIC_COLUMNS + ("Site Number", "Latitude", "Longitude"),
           ("State Code", "County Code", "Site Number")),
    Source("ozone_measurements", "hourly_44201_2024.zip", 8_995_978,
           GEOGRAPHIC_COLUMNS + ("Site Num", "Parameter Code", "POC", "Date Local",
                                 "Time Local", "Date GMT", "Time GMT",
                                 "Sample Measurement", "Units of Measure"),
           ("State Code", "County Code", "Site Num", "Parameter Code", "POC",
            "Date Local", "Units of Measure")),
    Source("daily_air_quality", "daily_aqi_by_county_2024.zip", 329_597,
           GEOGRAPHIC_COLUMNS + ("Date", "AQI", "Defining Parameter"),
           ("State Code", "County Code", "Date", "Defining Parameter")),
)


class ValidationError(ValueError):
    """A validation failure with a message safe to include in the report."""


def timestamp():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def http_metadata(response, method):
    endpoint = urlsplit(response.geturl())
    if endpoint.scheme != "https" or endpoint.hostname != "aqs.epa.gov":
        raise ValidationError("El destino final no es el servidor HTTPS oficial de AirData.")
    length = response.headers.get("Content-Length", "")
    return {
        "status": "reachable",
        "method": method,
        "http_status": response.status,
        "content_type": response.headers.get_content_type(),
        "content_length": int(length) if length.isdecimal() else None,
        "last_modified": response.headers.get("Last-Modified"),
        "etag": response.headers.get("ETag"),
    }


def open_request(url, method, timeout, headers=None):
    request = Request(url, headers=headers or {}, method=method)
    return urlopen(request, timeout=timeout)


def probe(url, timeout):
    try:
        with open_request(url, "HEAD", timeout) as response:
            return http_metadata(response, "HEAD")
    except HTTPError as error:
        error.close()
        if error.code not in (405, 501):
            raise
    with open_request(url, "GET", timeout, {"Range": "bytes=0-0"}) as response:
        response.read(1)
        return http_metadata(response, "GET Range")


def download(url, output, timeout):
    with open_request(url, "GET", timeout) as response:
        metadata = http_metadata(response, "GET")
        if response.status != 200:
            raise ValidationError("La descarga no devolvió una respuesta HTTP completa (200).")
        shutil.copyfileobj(response, output, length=1024 * 1024)
        expected_bytes = metadata["content_length"]
        if expected_bytes is not None and output.tell() != expected_bytes:
            raise ValidationError("La descarga tiene un tamaño diferente de Content-Length.")
        return metadata


def inspect_csv(archive, member, source, sample_size):
    with archive.open(member) as raw, io.TextIOWrapper(raw, encoding="utf-8-sig", newline="") as stream:
        reader = csv.reader(stream, strict=True)
        headers = next(reader, [])
        if not headers or any(not column.strip() for column in headers) or len(set(headers)) != len(headers):
            raise ValidationError("El encabezado CSV está vacío, tiene nombres vacíos o repite columnas.")
        missing = [column for column in source.required_columns if column not in headers]
        if missing:
            raise ValidationError("Faltan columnas requeridas: " + ", ".join(missing))

        positions = {column: headers.index(column) for column in source.sampled_columns}
        observed = {column: set() for column in positions}
        sampled_rows = 0
        for sampled_rows, row in enumerate(islice(reader, sample_size), start=1):
            if len(row) != len(headers):
                raise ValidationError(f"El registro de muestra {sampled_rows} no coincide con el ancho del encabezado.")
            for column, position in positions.items():
                observed[column].add(row[position])
        if not sampled_rows:
            raise ValidationError("El CSV tiene encabezado pero no contiene registros.")

    return {
        "name": PurePosixPath(member.filename).name,
        "uncompressed_bytes": member.file_size,
        "headers": headers,
        "sampled_rows": sampled_rows,
        "sample_observed_values": {column: sorted(values) for column, values in observed.items()},
        "verified_total_rows": None,
    }


def inspect_archive(path, source, sample_size):
    with path.open("rb") as stream:
        with ZipFile(stream) as archive:
            members = [member for member in archive.infolist()
                       if not member.is_dir() and PurePosixPath(member.filename).suffix.lower() == ".csv"]
            expected_name = Path(source.archive).with_suffix(".csv").name
            if len(members) != 1 or PurePosixPath(members[0].filename).name != expected_name:
                raise ValidationError(f"Se esperaba un único CSV llamado {expected_name}.")
            if archive.testzip() is not None:
                raise ValidationError("Falló la comprobación de integridad CRC de un miembro del ZIP.")
            csv_result = inspect_csv(archive, members[0], source, sample_size)
        zip_bytes = stream.seek(0, io.SEEK_END)
        stream.seek(0)
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    return {"zip_bytes": zip_bytes, "sha256": digest, "zip_crc_passed": True, "csv_files": [csv_result]}


def describe_error(error):
    if isinstance(error, ValidationError):
        return str(error)
    if isinstance(error, HTTPError):
        error.close()
        return f"HTTP {error.code}."
    if isinstance(error, URLError):
        return f"No se pudo conectar con AirData ({type(error.reason).__name__})."
    if isinstance(error, TimeoutError):
        return "Se agotó el tiempo de espera de red."
    if isinstance(error, ssl.SSLError):
        return "Falló la conexión TLS."
    return f"Error de tipo {type(error).__name__}."


def validate(source, args):
    path = args.cache_dir / source.archive
    result = {
        "name": source.name,
        "source_url": source.url,
        "archive_name": source.archive,
        "epa_catalog_published_rows": source.published_rows,
        "status": "failed",
        "http": {"status": "skipped" if args.offline else "not_checked"},
        "origin": None,
        "archive": None,
        "cache_issue": None,
        "error": None,
    }
    if not args.refresh:
        try:
            result["archive"] = inspect_archive(path, source, args.sample_size)
            result["origin"] = "cache"
        except FileNotFoundError:
            result["cache_issue"] = "No existe una copia en caché."
        except CACHE_ERRORS as error:
            result["cache_issue"] = describe_error(error)

    if args.offline:
        if result["archive"] is not None:
            result["status"] = "passed"
        else:
            result["error"] = {"stage": "cache", "message": result["cache_issue"]}
        return result

    if result["archive"] is not None:
        try:
            result["http"] = probe(source.url, args.timeout)
            result["status"] = "passed"
        except NETWORK_ERRORS as error:
            result["http"] = {"status": "failed"}
            result["error"] = {"stage": "http", "message": describe_error(error)}
        return result

    temp_path = None
    stage = "cache"
    try:
        with NamedTemporaryFile(dir=args.cache_dir, prefix=source.archive + ".",
                                suffix=".part", delete=False) as output:
            temp_path = Path(output.name)
            stage = "download"
            result["http"] = download(source.url, output, args.timeout)
        stage = "archive"
        result["archive"] = inspect_archive(temp_path, source, args.sample_size)
        stage = "cache"
        temp_path.replace(path)
        result["origin"] = "download"
        result["status"] = "passed"
    except (*CACHE_ERRORS, http.client.HTTPException) as error:
        if stage == "download":
            result["http"] = {"status": "failed"}
        result["error"] = {"stage": stage, "message": describe_error(error)}
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
    return result


def parse_args():
    parser = argparse.ArgumentParser(description="Valida los tres ZIP oficiales de EPA AirData.")
    parser.add_argument("--cache-dir", type=Path, default=MODULE_DIR.parent / ".cache" / "epa-airdata",
                        help="Directorio de los ZIP. El valor predeterminado está en .cache/epa-airdata.")
    parser.add_argument("--report", type=Path, default=MODULE_DIR / "evidencias" / "validation_report.json",
                        help="Ruta del reporte JSON.")
    parser.add_argument("--sample-size", type=int, default=25, help="Registros por CSV (1–1000, por defecto 25).")
    parser.add_argument("--timeout", type=float, default=30, help="Espera de red en segundos (por defecto 30).")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--offline", action="store_true", help="Revalida solo las copias locales.")
    mode.add_argument("--refresh", action="store_true", help="Obtiene versiones nuevas aunque exista la caché.")
    args = parser.parse_args()
    if not 1 <= args.sample_size <= 1000:
        parser.error("--sample-size debe estar entre 1 y 1000.")
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("--timeout debe ser finito y mayor que cero.")
    return args


def write_report(path, report):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                prefix=path.name + ".", suffix=".part", delete=False) as output:
            temp_path = Path(output.name)
            json.dump(report, output, ensure_ascii=False, indent=2)
            output.write("\n")
        temp_path.replace(path)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def main():
    args = parse_args()
    try:
        args.cache_dir.mkdir(parents=True, exist_ok=True)
        report = {
            "started_at_utc": timestamp(),
            "mode": "offline" if args.offline else "online",
            "refresh_requested": args.refresh,
            "catalog_url": CATALOG_URL,
            "catalog_consulted_on": "2026-11-18",
            "sample_size_per_csv": args.sample_size,
            "network_timeout_seconds": args.timeout,
            "datasets": [],
        }
        for source in SOURCES:
            print(f"Validando {source.archive}...", flush=True)
            result = validate(source, args)
            report["datasets"].append(result)
            print(f"  {result['error']['message'] if result['error'] else result['origin']}", flush=True)
        report["finished_at_utc"] = timestamp()
        success = all(result["status"] == "passed" for result in report["datasets"])
        report["status"] = "passed" if success else "failed"
        write_report(args.report, report)
    except OSError as error:
        print(f"No se pudo completar la operación local. {describe_error(error)}", file=sys.stderr)
        return 1
    print(f"Reporte escrito en {args.report.name}.")
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
