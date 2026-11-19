#!/usr/bin/env python3
"""Ingest the RDS, NOAA and Census sources into the raw S3 prefixes."""

import argparse
import base64
import csv
import hashlib
import json
import math
import os
import shutil
import sys
import tempfile
import time
from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime, timezone
from http.client import HTTPException
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from uuid import uuid4

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from load_daily_aqi import COLUMNS as AQI_COLUMNS
from load_daily_aqi import TABLE as AQI_TABLE
from load_daily_aqi import LoadError, database_connection
from load_daily_weather import HEADERS as NOAA_HEADERS


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BUCKET = "airquality-datalake-2024"
DEFAULT_REGION = "us-east-1"
CENSUS_URL = "https://www2.census.gov/programs-surveys/popest/datasets/2020-2024/counties/totals/co-est2024-alldata.csv"
BUFFER_SIZE = 1024 * 1024
SELECT_AQI = (
    f"SELECT {', '.join(AQI_COLUMNS)} FROM {AQI_TABLE} "
    "WHERE measurement_date >= %s AND measurement_date < %s "
    "ORDER BY state_code, county_code, measurement_date"
)


class ContentConflict(LoadError):
    """S3 already holds different bytes for the requested key."""


@dataclass(frozen=True)
class Source:
    source: str
    origin: str
    key: str
    path: Path


def display_path(path):
    try:
        return path.resolve().relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return path.name


def source_plan(args, directory):
    year = args.year
    if args.source in ("all", "rds"):
        name = f"daily_aqi_by_county_{year}.csv"
        database = os.environ.get("DB_NAME") or "airquality"
        yield Source("rds", f"{database}.{AQI_TABLE}", f"raw/rds/epa/year={year}/{name}", directory / name)
    if args.source in ("all", "ec2"):
        for month in range(1, 13):
            name = f"{year}{month:02d}-scaled.csv"
            path = args.noaa_dir.expanduser() / name
            yield Source("ec2", display_path(path), f"raw/ec2/noaa/year={year}/{name}", path)
    if args.source in ("all", "url"):
        name = f"co-est{year}-alldata.csv"
        yield Source("url", args.census_url, f"raw/url/census/year={year}/{name}", directory / name)


def export_rds(path, year, batch_size, expected_rows):
    start, end = date(year, 1, 1), date(year + 1, 1, 1)
    count = 0
    with database_connection() as connection:
        from pymysql.cursors import SSCursor

        with connection.cursor() as setup:
            setup.execute("START TRANSACTION READ ONLY")
        with connection.cursor(SSCursor) as cursor, path.open("w", newline="", encoding="utf-8") as stream:
            cursor.execute(SELECT_AQI, (start, end))
            if tuple(column[0] for column in cursor.description) != AQI_COLUMNS:
                raise LoadError("La tabla AQI no devolvió las diez columnas esperadas.")
            writer = csv.writer(stream)
            writer.writerow(AQI_COLUMNS)
            while batch := cursor.fetchmany(batch_size):
                for row in batch:
                    if len(row) != len(AQI_COLUMNS) or any(value is None for value in row):
                        raise LoadError("La tabla AQI devolvió una fila incompleta.")
                    if type(row[4]) is not date or not start <= row[4] < end:
                        raise LoadError("La tabla AQI devolvió una fecha inválida o fuera del año seleccionado.")
                    writer.writerow((*row[:4], row[4].isoformat(), *row[5:]))
                    count += 1
    if expected_rows is not None and count != expected_rows:
        raise LoadError(f"Se esperaban {expected_rows} filas AQI y se extrajeron {count}.")
    return count


def public_census_url(value):
    try:
        parts = urlsplit(value)
        return (parts.scheme == "https" and parts.hostname in {"www2.census.gov", "www.census.gov"}
                and parts.port in (None, 443) and not any((parts.username, parts.password, parts.query, parts.fragment)))
    except ValueError:
        return False


def download_census(url, path, timeout):
    request = Request(url, headers={"User-Agent": "SI3007-AirQuality/1.0"})
    with urlopen(request, timeout=timeout) as response:
        if response.status != 200 or not public_census_url(response.geturl()):
            raise LoadError("La descarga no devolvió HTTP 200 desde el dominio HTTPS oficial del Census Bureau.")
        if response.headers.get_content_type() in {"text/html", "application/xhtml+xml"}:
            raise LoadError("La URL devolvió HTML en lugar del CSV del Census Bureau.")
        with path.open("wb") as output:
            shutil.copyfileobj(response, output, length=BUFFER_SIZE)
            length = response.headers.get("Content-Length", "")
            if length.isdecimal() and output.tell() != int(length):
                raise LoadError("La descarga del Census Bureau está incompleta según Content-Length.")


def count_csv(path, required, encoding, exact_header=False):
    with path.open(encoding=encoding, newline="") as stream:
        reader = csv.reader(stream, strict=True)
        header = tuple(next(reader, ()))
        if (not set(required).issubset(header) or len(set(header)) != len(header)
                or (exact_header and header != required)):
            raise LoadError("El encabezado CSV no contiene las columnas esperadas para esta fuente.")
        positions = () if exact_header else tuple(header.index(column) for column in required)
        count = 0
        for row in reader:
            if len(row) != len(header) or any(not row[index].strip() for index in positions):
                raise LoadError(f"Registro CSV incompleto cerca de la línea {reader.line_num}.")
            count += 1
    if not count:
        raise LoadError("El CSV contiene el encabezado y cero registros.")
    return count


def prepare_source(item, args):
    if item.source == "rds":
        return export_rds(item.path, args.year, args.batch_size, args.expected_rds_rows)
    if item.source == "ec2":
        return count_csv(item.path, NOAA_HEADERS, "utf-8-sig", exact_header=True)
    download_census(item.origin, item.path, args.timeout)
    required = ("STATE", "COUNTY", "SUMLEV", f"POPESTIMATE{args.year}")
    try:
        return count_csv(item.path, required, "utf-8-sig")
    except UnicodeDecodeError:
        return count_csv(item.path, required, "cp1252")


def fingerprint(stream):
    digest = hashlib.sha256()
    size = 0
    for chunk in iter(lambda: stream.read(BUFFER_SIZE), b""):
        size += len(chunk)
        digest.update(chunk)
    return size, digest.hexdigest()


def head_or_missing(s3, bucket, key):
    try:
        return s3.head_object(Bucket=bucket, Key=key, ChecksumMode="ENABLED")
    except ClientError as error:
        if error.response["Error"].get("Code") not in {"404", "NoSuchKey", "NotFound"}:
            raise
        return None


def object_fingerprint(s3, bucket, key, head):
    checksum = head.get("ChecksumSHA256")
    if checksum and head.get("ChecksumType") in (None, "FULL_OBJECT"):
        try:
            digest = base64.b64decode(checksum, validate=True)
        except ValueError:
            digest = b""
        if len(digest) == 32:
            return head["ContentLength"], digest.hex()
    conditions = {"IfMatch": head["ETag"]} if head.get("ETag") else {}
    response = s3.get_object(Bucket=bucket, Key=key, **conditions)
    with closing(response["Body"]) as body:
        size, digest = fingerprint(body)
    if size != head["ContentLength"]:
        raise LoadError("La lectura de S3 no coincide con el tamaño del objeto consultado.")
    return size, digest


def publish(s3, bucket, key, path, size, sha256, content_type):
    encoded_checksum = base64.b64encode(bytes.fromhex(sha256)).decode("ascii")
    for _ in range(3):
        head = head_or_missing(s3, bucket, key)
        if head is not None:
            if head["ContentLength"] == size and object_fingerprint(s3, bucket, key, head) == (size, sha256):
                return "skipped_identical"
            raise ContentConflict("El objeto existente tiene contenido distinto.")
        try:
            with path.open("rb") as body:
                s3.put_object(Bucket=bucket, Key=key, Body=body, ContentLength=size, ContentType=content_type,
                              ChecksumSHA256=encoded_checksum, Metadata={"sha256": sha256}, IfNoneMatch="*")
            return "uploaded"
        except ClientError as error:
            code = error.response["Error"].get("Code")
            if code not in {"409", "412", "ConditionalRequestConflict", "PreconditionFailed"}:
                raise
    raise LoadError("S3 rechazó la creación condicional tras varios cambios concurrentes.")


def describe_error(error):
    if isinstance(error, LoadError):
        return str(error)
    if isinstance(error, ClientError):
        status = error.response.get("ResponseMetadata", {}).get("HTTPStatusCode", "desconocido")
        code = error.response.get("Error", {}).get("Code", "desconocido")
        return f"S3 {error.operation_name} devolvió HTTP {status} ({code})."
    if isinstance(error, HTTPError):
        error.close()
        return f"La fuente pública devolvió HTTP {error.code}."
    if isinstance(error, URLError):
        return f"Falló la conexión con el Census Bureau ({type(error.reason).__name__})."
    return f"Error de tipo {type(error).__name__}."


SOURCE_ERRORS = (LoadError, ClientError, BotoCoreError, OSError, HTTPException, EOFError, UnicodeError, csv.Error)


def process_source(item, args, s3, client_error):
    start = time.perf_counter()
    records = size = sha256 = error_message = None
    status = "error"
    try:
        records = prepare_source(item, args)
        with item.path.open("rb") as stream:
            size, sha256 = fingerprint(stream)
        if args.dry_run:
            status = "validated"
        elif client_error is not None:
            error_message = client_error
        else:
            status = publish(s3, args.bucket, item.key, item.path, size, sha256, "text/csv")
    except SOURCE_ERRORS as error:
        if isinstance(error, ContentConflict):
            status = "conflict"
        error_message = describe_error(error)
    return {"source": item.source, "origin": item.origin, "s3_uri": f"s3://{args.bucket}/{item.key}",
            "records": records, "size_bytes": size, "sha256": sha256, "status": status,
            "error": error_message, "elapsed_seconds": round(time.perf_counter() - start, 3)}


def create_s3_client(region, timeout):
    config = Config(connect_timeout=timeout, read_timeout=timeout, retries={"mode": "standard", "total_max_attempts": 3})
    return boto3.client("s3", region_name=region, config=config)


def write_manifest(path, report):
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = path.with_suffix(".json.part")
    try:
        staging.write_text(json.dumps(report, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
        staging.replace(path)
    finally:
        staging.unlink(missing_ok=True)


def run(args, s3=None):
    start = time.perf_counter()
    started_at = datetime.now(timezone.utc)
    run_id = f"{started_at:%Y%m%dT%H%M%S}-{uuid4().hex}"
    manifest_name = f"ingest-{run_id}.json"
    manifest_key = f"manifests/year={args.year}/{manifest_name}"
    manifest_path = args.manifest_dir.expanduser() / manifest_name
    client_error = None
    if not args.dry_run and s3 is None:
        try:
            s3 = create_s3_client(args.region, args.timeout)
        except (BotoCoreError, OSError) as error:
            client_error = describe_error(error)

    with tempfile.TemporaryDirectory(prefix="aqi-s3-ingest-") as directory:
        objects = []
        for item in source_plan(args, Path(directory)):
            result = process_source(item, args, s3, client_error)
            objects.append(result)
            detail = f" ({result['error']})" if result["error"] else ""
            print(f"{result['s3_uri']} — {result['status']}{detail}", file=sys.stderr, flush=True)
    totals = {status: sum(item["status"] == status for item in objects)
              for status in ("validated", "uploaded", "skipped_identical", "conflict", "error")}
    report = {
        "run_id": run_id, "year": args.year, "bucket": args.bucket, "region": args.region,
        "mode": "dry-run" if args.dry_run else "ingest", "selected_source": args.source,
        "status": "failed" if totals["conflict"] or totals["error"] else "passed",
        "started_at_utc": started_at.isoformat(timespec="seconds"),
        "elapsed_seconds": round(time.perf_counter() - start, 3),
        "manifest_s3_uri": None if args.dry_run else f"s3://{args.bucket}/{manifest_key}",
        "summary": totals, "objects": objects,
    }
    write_manifest(manifest_path, report)
    if not args.dry_run:
        manifest_error = client_error
        if manifest_error is None:
            try:
                with manifest_path.open("rb") as stream:
                    size, sha256 = fingerprint(stream)
                publish(s3, args.bucket, manifest_key, manifest_path, size, sha256, "application/json")
            except SOURCE_ERRORS as error:
                manifest_error = describe_error(error)
        if manifest_error is not None:
            report.update(status="failed", manifest_upload_error=manifest_error)
            write_manifest(manifest_path, report)
    return report, manifest_path


def parse_args(argv):
    parser = argparse.ArgumentParser(description="Ingiere RDS, NOAA y Census Bureau en la zona raw de S3.")
    parser.add_argument("--source", choices=("all", "rds", "ec2", "url"), default="all")
    parser.add_argument("--bucket", default=os.environ.get("S3_BUCKET", DEFAULT_BUCKET))
    parser.add_argument("--region", default=os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or DEFAULT_REGION)
    parser.add_argument("--year", type=int, default=os.environ.get("INGEST_YEAR", "2024"))
    parser.add_argument("--noaa-dir", type=Path, default=os.environ.get("NOAA_DIR", PROJECT_ROOT / "data/raw/noaa"))
    parser.add_argument("--census-url", default=os.environ.get("CENSUS_URL"))
    parser.add_argument("--manifest-dir", type=Path, default=os.environ.get("INGEST_MANIFEST_DIR", PROJECT_ROOT / "03-ingesta-datos/logs/ingest_to_s3"))
    parser.add_argument("--batch-size", type=int, default=1000)
    parser.add_argument("--expected-rds-rows", type=int)
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--dry-run", action="store_true", help="Valida las fuentes sin contactar S3.")
    args = parser.parse_args(argv)
    if not 1000 <= args.year <= 9998 or args.batch_size < 1:
        parser.error("Usa un año entre 1000 y 9998 y un tamaño de lote positivo.")
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("--timeout debe ser finito y positivo.")
    if args.expected_rds_rows is None and args.year == 2024:
        args.expected_rds_rows = 329_597
    if args.expected_rds_rows is not None and args.expected_rds_rows < 0:
        parser.error("--expected-rds-rows debe ser no negativo.")
    if args.source in ("url", "all"):
        if args.census_url is None and args.year == 2024:
            args.census_url = CENSUS_URL
        if not args.census_url or not public_census_url(args.census_url):
            parser.error("Indica la URL HTTPS pública del CSV oficial del Census Bureau para ese año.")
    return args


def main(argv=None):
    args = parse_args(argv)
    try:
        report, path = run(args)
    except OSError as error:
        print(describe_error(error), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Ingesta interrumpida.", file=sys.stderr)
        return 130
    print(f"Manifiesto local — {path.name}")
    if report.get("manifest_upload_error"):
        print(f"Error al guardar el manifiesto en S3 — {report['manifest_upload_error']}", file=sys.stderr)
    elif report["manifest_s3_uri"]:
        print(f"Manifiesto S3 — {report['manifest_s3_uri']}")
    print(json.dumps(report["summary"]))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    sys.exit(main())
