"""Local paths, provenance and transport for the approved AirData sources."""

import argparse
import hashlib
import http.client
import json
import math
import ssl
import sys
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from zipfile import BadZipFile
from zlib import error as ZlibError


ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / ".cache"
AIRDATA = CACHE / "epa-airdata"
EPA_BASE = "https://aqs.epa.gov/aqsweb/airdata/"
STATIONS_ZIP = "aqs_sites.zip"
HOURLY_ZIP = "hourly_44201_2024.zip"
AQI_ZIP = "daily_aqi_by_county_2024.zip"
BUFFER_SIZE = 1024 * 1024
ARCHIVE_ERRORS = (BadZipFile, EOFError, RuntimeError, ZlibError)
NETWORK_ERRORS = (OSError, http.client.HTTPException)


class SourceError(Exception):
    """A source validation error with a message safe for public evidence."""


def timestamp():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_file(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def csv_member(archive, filename):
    csv_files = [member for member in archive.infolist()
                 if not member.is_dir() and PurePosixPath(member.filename).suffix.lower() == ".csv"]
    if len(csv_files) != 1 or PurePosixPath(csv_files[0].filename).name != filename:
        raise SourceError(f"Se esperaba un único CSV llamado {filename}.")
    return csv_files[0]


def check_crc(archive):
    if archive.testzip() is not None:
        raise SourceError("El ZIP falló la verificación CRC.")


def open_epa(url, method, timeout, headers=None):
    request = Request(url, headers=headers or {}, method=method)
    response = urlopen(request, timeout=timeout)
    endpoint = urlsplit(response.geturl())
    if endpoint.scheme != "https" or endpoint.hostname != "aqs.epa.gov":
        response.close()
        raise SourceError("La URL final no pertenece al servidor HTTPS oficial de AirData.")
    return response


def positive_timeout(value):
    timeout = float(value)
    if not math.isfinite(timeout) or timeout <= 0:
        raise argparse.ArgumentTypeError("El tiempo de espera debe ser finito y positivo.")
    return timeout


def error_message(error):
    if isinstance(error, SourceError):
        return str(error)
    if isinstance(error, HTTPError):
        error.close()
        return f"HTTP {error.code}."
    if isinstance(error, URLError):
        return f"Error de conexión HTTPS ({type(error.reason).__name__})."
    if isinstance(error, TimeoutError):
        return "Se agotó el tiempo de espera."
    if isinstance(error, ssl.SSLError):
        return "Falló TLS."
    return f"Error de tipo {type(error).__name__}."


def emit_report(result, path=None):
    output = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(output, encoding="utf-8")
    sys.stdout.write(output)
