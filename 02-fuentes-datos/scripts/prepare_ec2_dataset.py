#!/usr/bin/env python3
"""Prepare a byte-for-byte copy of the AirData hourly CSV for EC2."""

import argparse
import hashlib
import shutil
import sys
from pathlib import Path
from tempfile import NamedTemporaryFile
from zipfile import ZipFile

from source_tools import (AIRDATA, ARCHIVE_ERRORS, BUFFER_SIZE, CACHE, EPA_BASE, HOURLY_ZIP,
                          NETWORK_ERRORS, SourceError, check_crc, csv_member, emit_report,
                          error_message, open_epa, positive_timeout, sha256_file, timestamp)


CSV_NAME = "hourly_44201_2024.csv"
URL = EPA_BASE + HOURLY_ZIP


def verified_size(path):
    with ZipFile(path) as archive:
        member = csv_member(archive, CSV_NAME)
        if member.file_size <= 0:
            raise SourceError("El CSV horario está vacío.")
        check_crc(archive)
        return member.file_size


def download_archive(path, timeout):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        with NamedTemporaryFile(dir=path.parent, suffix=".part", delete=False) as output:
            temp_path = Path(output.name)
            with open_epa(URL, "GET", timeout) as response:
                if response.status != 200:
                    raise SourceError("La descarga del ozono no devolvió HTTP 200.")
                shutil.copyfileobj(response, output, length=BUFFER_SIZE)
                length = response.headers.get("Content-Length", "")
                if length.isdecimal() and int(length) != output.tell():
                    raise SourceError("La descarga del ZIP está incompleta según Content-Length.")
        size = verified_size(temp_path)
        temp_path.replace(path)
        return size
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def prepare(archive_path, output_dir, allow_download=False, timeout=30):
    origin = "local"
    try:
        size = verified_size(archive_path)
    except (FileNotFoundError, *ARCHIVE_ERRORS, SourceError) as error:
        if not allow_download:
            raise SourceError(f"ZIP local ausente o inválido ({error_message(error)}). "
                              "Usa --allow-download para obtenerlo de EPA AirData.") from error
        size = download_archive(archive_path, timeout)
        origin = "download"

    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / CSV_NAME
    with ZipFile(archive_path) as archive:
        member = csv_member(archive, CSV_NAME)
        if csv_path.exists():
            with archive.open(member) as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            if csv_path.stat().st_size != size or sha256_file(csv_path) != digest:
                raise SourceError("Ya existe un CSV distinto en el destino. Selecciona otro directorio.")
            preparation = "reused"
        else:
            free_bytes = shutil.disk_usage(output_dir).free
            if free_bytes < size:
                raise SourceError(f"Espacio insuficiente. El CSV requiere {size} bytes y hay {free_bytes} disponibles.")
            digest = extract_csv(archive, member, csv_path, size)
            preparation = "extracted"

    return {"status": "passed", "checked_at_utc": timestamp(), "source_url": URL,
            "archive_name": archive_path.name, "archive_origin": origin,
            "archive_bytes": archive_path.stat().st_size, "archive_sha256": sha256_file(archive_path),
            "zip_crc_passed": True, "csv_name": csv_path.name,
            "csv_bytes": size, "csv_sha256": digest, "preparation": preparation}


def extract_csv(archive, member, csv_path, size):
    digest = hashlib.sha256()
    temp_path = None
    try:
        with NamedTemporaryFile(dir=csv_path.parent, prefix=csv_path.name + ".", suffix=".part",
                                delete=False) as output:
            temp_path = Path(output.name)
            with archive.open(member) as stream:
                for block in iter(lambda: stream.read(BUFFER_SIZE), b""):
                    digest.update(block)
                    output.write(block)
            if output.tell() != size:
                raise SourceError("El CSV extraído no coincide con el tamaño declarado en el ZIP.")
        if csv_path.exists():
            raise SourceError("Se creó otro CSV en el destino durante la extracción.")
        temp_path.replace(csv_path)
        return digest.hexdigest()
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description="Prepara el CSV horario original para su almacenamiento en EC2.")
    parser.add_argument("--archive", type=Path, default=AIRDATA / HOURLY_ZIP)
    parser.add_argument("--output-dir", type=Path, default=CACHE / "ec2-source")
    parser.add_argument("--allow-download", action="store_true", help="Descarga de EPA si el ZIP local falta o falla.")
    parser.add_argument("--timeout", type=positive_timeout, default=30)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    try:
        emit_report(prepare(args.archive, args.output_dir, args.allow_download, args.timeout), args.report)
        return 0
    except (*NETWORK_ERRORS, *ARCHIVE_ERRORS, SourceError) as error:
        print(error_message(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
