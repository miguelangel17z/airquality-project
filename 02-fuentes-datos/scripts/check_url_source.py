#!/usr/bin/env python3
"""Check availability of the official daily AQI ZIP without storing it."""

import argparse
import sys
from pathlib import Path
from urllib.error import HTTPError

from source_tools import (AQI_ZIP, EPA_BASE, NETWORK_ERRORS, SourceError, emit_report,
                          error_message, open_epa, positive_timeout, timestamp)


URL = EPA_BASE + AQI_ZIP


def check_url(timeout):
    result = {"status": "failed", "checked_at_utc": timestamp(), "source_url": URL,
              "method": "HEAD", "http_status": None, "content_type": None,
              "content_length": None, "last_modified": None, "etag": None,
              "timeout_seconds": timeout, "error": None}
    try:
        try:
            response = open_epa(URL, "HEAD", timeout)
        except HTTPError as error:
            if error.code not in (405, 501):
                raise
            error.close()
            result["method"] = "GET Range"
            response = open_epa(URL, "GET", timeout, {"Range": "bytes=0-3"})
        with response:
            length = response.headers.get("Content-Length", "")
            result.update(http_status=response.status, content_type=response.headers.get_content_type(),
                          content_length=int(length) if length.isdecimal() else None,
                          last_modified=response.headers.get("Last-Modified"), etag=response.headers.get("ETag"))
            if result["method"] == "HEAD":
                if response.status != 200 or result["content_type"] not in (
                    "application/zip", "application/x-zip-compressed", "application/octet-stream"
                ) or result["content_length"] == 0:
                    raise SourceError("La respuesta HEAD no identifica un ZIP accesible.")
            elif response.status not in (200, 206) or response.read(4) != b"PK\x03\x04":
                raise SourceError("La respuesta parcial no contiene la firma esperada de un ZIP.")
            result["status"] = "passed"
    except (*NETWORK_ERRORS, SourceError) as error:
        if isinstance(error, HTTPError):
            result["http_status"] = error.code
        result["error"] = error_message(error)
    return result


def main():
    parser = argparse.ArgumentParser(description="Comprueba el origen web del AQI diario de 2024.")
    parser.add_argument("--timeout", type=positive_timeout, default=30)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    result = check_url(args.timeout)
    try:
        emit_report(result, args.report)
    except OSError as error:
        print(error_message(error), file=sys.stderr)
        return 1
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    sys.exit(main())
