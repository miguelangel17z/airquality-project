#!/usr/bin/env python3
"""Validate the three Raw sources and publish partitioned Trusted Parquet."""

import argparse
import json
import time
from datetime import datetime, timezone
from urllib.parse import unquote

from pyspark import SparkContext
from pyspark.sql import SparkSession, functions as F, types as T


EPA_COLUMNS = (
    "state_name", "county_name", "state_code", "county_code", "measurement_date",
    "aqi", "category", "defining_parameter", "defining_site", "sites_reporting",
)
NOAA_COLUMNS = ("region_type", "fips", "ncei_code", "state_name", "postal_code", "region_name",
                "date", "tmax", "tmin", "tavg", "prcp")
MEASURES = ("tmax", "tmin", "tavg", "prcp")
GEO_KEY = ("state_code", "county_code")
DAILY_KEY = (*GEO_KEY, "measurement_date")
PARTITIONS = {"epa": ("year", "month"), "noaa": ("year", "month"), "census": ("year",),
              "aqi_weather": ("year", "month")}
ISO_DATE = r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$"
DECIMAL_TEXT = r"^[+-]?[0-9]+(?:\.[0-9]{1,2})?$"


class QualityError(ValueError):
    """An input failed the Trusted quality contract."""


def path_join(root, name):
    return root.rstrip("/") + "/" + name


def text_schema(columns):
    return T.StructType([T.StructField(column, T.StringType(), True) for column in columns])


def read_csv(spark, path, encoding="UTF-8", columns=None):
    reader = spark.read.options(header=True, inferSchema=False, enforceSchema=False,
                                mode="FAILFAST", encoding=encoding, escape='"')
    if columns is not None:
        reader = reader.schema(text_schema(columns))
    return reader.csv(path)


def missing(column):
    return column.isNull() | (F.length(F.trim(column)) == 0)


def count_if(condition):
    return F.count(F.when(condition, 1))


def invalid(valid_condition):
    return ~F.coalesce(valid_condition, F.lit(False))


def input_metrics(frame, fields, invalid_condition, *extra):
    row = frame.agg(F.count("*").alias("input_rows"), count_if(invalid_condition).alias("invalid_rows"),
                    *(count_if(missing(F.col(field))).alias(f"missing_{field}") for field in fields), *extra).first()
    result = row.asDict()
    result["missing_values"] = {field: result.pop(f"missing_{field}") for field in fields}
    return result


def geographic_valid(state, county):
    return state.rlike(r"^[0-9]{2}$") & county.rlike(r"^[0-9]{3}$")


def duplicate_rows(frame, keys):
    return frame.groupBy(*keys).count().where("count > 1").agg(
        F.coalesce(F.sum(F.col("count") - 1), F.lit(0))).first()[0]


def require_clean(name, metrics):
    if metrics["input_rows"] == 0:
        raise QualityError(f"{name}: la entrada no contiene registros.")
    if metrics["invalid_rows"] or metrics.get("duplicate_rows", 0):
        raise QualityError(f"{name}: contiene registros inválidos o claves duplicadas. Consulta el reporte.")


def daily_partitions(frame):
    return frame.withColumn("year", F.year("measurement_date")).withColumn("month", F.month("measurement_date"))


def process_epa(raw, year, metrics):
    parsed = (raw.select(*EPA_COLUMNS)
              .withColumn("_date", F.expr("try_cast(measurement_date AS DATE)"))
              .withColumn("_aqi", F.expr("try_cast(aqi AS INT)"))
              .withColumn("_sites", F.expr("try_cast(sites_reporting AS INT)")))
    valid = (geographic_valid(F.col("state_code"), F.col("county_code"))
             & F.col("measurement_date").rlike(ISO_DATE) & (F.year("_date") == year)
             & F.col("aqi").rlike(r"^[0-9]+$") & F.col("_aqi").between(0, 65535)
             & F.col("sites_reporting").rlike(r"^[0-9]+$") & F.col("_sites").between(0, 65535))
    for field in ("state_name", "county_name", "category", "defining_parameter", "defining_site"):
        valid = valid & ~missing(F.col(field))
    metrics.update(input_metrics(parsed, EPA_COLUMNS, invalid(valid)))
    clean = (parsed.where(valid).drop("measurement_date", "aqi", "sites_reporting")
             .withColumnRenamed("_date", "measurement_date").withColumnRenamed("_aqi", "aqi")
             .withColumnRenamed("_sites", "sites_reporting").select(*EPA_COLUMNS))
    metrics["duplicate_rows"] = duplicate_rows(clean, DAILY_KEY)
    require_clean("EPA", metrics)
    metrics["output_rows"] = metrics["input_rows"]
    return daily_partitions(clean)


def process_noaa(raw, year, expected_months, metrics):
    numeric_cols = ("tmax_c", "tmin_c", "tavg_c", "precipitation_mm")
    raw = raw.select("region_type", "fips", "date", *MEASURES, "_file")
    frame = (raw.withColumn("_month", F.expr("try_cast(concat(substr(_file, 1, 4), '-', substr(_file, 5, 2), '-01') AS DATE)"))
             .withColumn("_days", F.dayofmonth(F.last_day("_month")))
             .withColumn("measurement_date", F.expr("try_cast(date AS DATE)"))
             .withColumn("state_code", F.substring("fips", 1, 2))
             .withColumn("county_code", F.substring("fips", 3, 3)))
    padding = missing(F.col("date"))
    valid_measures = F.lit(True)
    for field, output in zip(MEASURES, numeric_cols):
        precision = 8 if field == "prcp" else 6
        frame = frame.withColumn(output, F.expr(f"try_cast(trim({field}) AS DECIMAL({precision},2))"))
        padding = padding & (F.trim(F.col(field)) == "-999.99")
        valid_measures &= (F.trim(F.col(field)).rlike(DECIMAL_TEXT) & F.col(output).isNotNull()
                           & (F.col(output) != F.lit("-999.99").cast("decimal(6,2)")))
    valid_measures &= ((F.col("precipitation_mm") >= 0) & (F.col("tmin_c") <= F.col("tavg_c"))
                       & (F.col("tavg_c") <= F.col("tmax_c")))
    valid_day = (F.col("date").rlike(ISO_DATE) & (F.trunc("measurement_date", "month") == F.col("_month"))
                 & valid_measures)
    frame = frame.withColumn("_padding", F.coalesce(padding, F.lit(False)))
    basic = ((F.col("region_type") == "cty") & F.col("fips").rlike(r"^[0-9]{5}$")
             & F.col("_file").rlike(r"^[0-9]{6}-scaled\.csv$") & (F.year("_month") == year))
    valid = basic & (valid_day | (F.col("_padding") & (F.col("_days") < 31)))
    metrics.update(input_metrics(frame, ("region_type", "fips", "date", *MEASURES), invalid(valid),
                                 F.countDistinct("_file").alias("input_files"),
                                 count_if(F.col("_padding")).alias("padding_candidates")))
    metrics["padding_discarded"] = 0
    require_clean("NOAA", metrics)
    if metrics["input_files"] != expected_months:
        raise QualityError("NOAA: falta contenido en uno o más archivos mensuales.")

    by_county = frame.groupBy("_month", "fips", "_days").agg(
        count_if(~F.col("_padding")).alias("dated_rows"),
        F.countDistinct("measurement_date").alias("distinct_days"),
        count_if(F.col("_padding")).alias("padding_rows"))
    calendar = by_county.agg(
        count_if((F.col("distinct_days") != F.col("_days"))
                 | (F.col("padding_rows") != 31 - F.col("_days"))).alias("invalid_county_months"),
        F.coalesce(F.sum(F.col("dated_rows") - F.col("distinct_days")), F.lit(0)).alias("duplicate_rows")).first()
    metrics.update(calendar.asDict())
    require_clean("NOAA", metrics)
    if metrics["invalid_county_months"]:
        raise QualityError("NOAA: el número de días reales y de filas de relleno no coincide con el calendario del condado.")
    metrics["padding_discarded"] = metrics["padding_candidates"]
    metrics["output_rows"] = metrics["input_rows"] - metrics["padding_discarded"]
    return daily_partitions(frame.where(~F.col("_padding")).select("fips", *DAILY_KEY, *numeric_cols))


def process_census(raw, year, metrics):
    population = f"POPESTIMATE{year}"
    expected = ("STATE", "COUNTY", "SUMLEV", population)
    if not set(expected).issubset(raw.columns):
        raise QualityError("Census: faltan columnas requeridas en el encabezado.")
    raw = raw.select(*expected)
    level = raw.agg(F.count("*").alias("input_rows"),
                    count_if(invalid(F.col("SUMLEV").rlike(r"^[0-9]{3}$"))).alias("invalid_levels"),
                    count_if(missing(F.col("SUMLEV"))).alias("missing_sumlev")).first()
    counties = (raw.where(F.col("SUMLEV") == "050")
                .withColumn("population_estimate", F.expr(f"try_cast({population} AS BIGINT)")))
    valid = (geographic_valid(F.col("STATE"), F.col("COUNTY")) & F.col(population).rlike(r"^[0-9]+$")
             & (F.col("population_estimate") >= 0))
    metrics.update(input_metrics(counties, ("STATE", "COUNTY", population), invalid(valid)))
    metrics["county_rows"] = metrics["input_rows"]
    metrics["input_rows"] = level["input_rows"]
    metrics["invalid_rows"] += level["invalid_levels"]
    metrics["missing_values"]["SUMLEV"] = level["missing_sumlev"]
    metrics["filtered_non_county_rows"] = level["input_rows"] - metrics["county_rows"]
    clean = counties.where(valid).select(F.concat("STATE", "COUNTY").alias("fips"),
                                         F.col("STATE").alias("state_code"), F.col("COUNTY").alias("county_code"),
                                         "population_estimate", F.lit(year).alias("year"))
    metrics["duplicate_rows"] = duplicate_rows(clean, GEO_KEY)
    require_clean("Census", metrics)
    if not metrics["county_rows"]:
        raise QualityError("Census: la entrada no contiene filas SUMLEV=050.")
    metrics["output_rows"] = metrics["county_rows"]
    return clean


def date_bounds(frame):
    return frame.agg(F.min("measurement_date").alias("date_min"),
                     F.max("measurement_date").alias("date_max")).first().asDict()


def describe(frame):
    return [{"name": field.name, "type": field.dataType.simpleString()} for field in frame.schema.fields]


def qualified_path(spark, location):
    path = spark._jvm.org.apache.hadoop.fs.Path(location)
    fs = path.getFileSystem(spark.sparkContext._jsc.hadoopConfiguration())
    return fs, fs.makeQualified(path)


def check_outputs(spark, outputs, report_path):
    data_paths = []
    for location in (*outputs.values(), report_path):
        requested = spark._jvm.org.apache.hadoop.fs.Path(location).toUri().normalize()
        if requested.getScheme() in ("s3", "s3a", "s3n") and requested.getPath().strip("/").split("/")[0].lower() == "raw":
            raise ValueError("Las salidas y el reporte deben quedar fuera de S3 Raw.")
        fs, path = qualified_path(spark, location)
        uri = path.toUri().normalize()
        scheme = "s3" if uri.getScheme() in ("s3", "s3a", "s3n") else uri.getScheme()
        data_paths.append((scheme, uri.getAuthority(), uri.getPath().rstrip("/")))
        if fs.exists(path):
            raise FileExistsError(f"El destino ya existe: {location}")
    scheme, authority, path = data_paths[-1]
    for parent_scheme, parent_authority, parent_path in data_paths[:-1]:
        if (scheme, authority) == (parent_scheme, parent_authority) and (path == parent_path or path.startswith(parent_path + "/")):
            raise ValueError("El reporte debe quedar fuera de los directorios Parquet.")


def write_report(spark, destination, report):
    fs, path = qualified_path(spark, destination)
    fs.mkdirs(path.getParent())
    stream = fs.create(path, False)
    try:
        stream.write(bytearray((json.dumps(report, indent=2, default=str) + "\n").encode("utf-8")))
    finally:
        stream.close()


def run_etl(spark, args):
    outputs = {name: path_join(args.output_root, name) for name in PARTITIONS}
    check_outputs(spark, outputs, args.report_path)
    spark.conf.set("spark.sql.csv.parser.columnPruning.enabled", "false")
    spark.conf.set("spark.sql.session.timeZone", "UTC")
    sources = {"epa": args.epa_input, "noaa": args.noaa_input, "census": args.census_input}
    report = {"status": "failed", "year": args.year, "spark_version": spark.version,
              "started_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), "datasets": {}}
    frames = {}
    start = time.perf_counter()
    try:
        for name in sources:
            report["stage"] = name
            tick = time.perf_counter()
            metrics = {"source": sources[name], "output": outputs[name], "output_rows": 0}
            report["datasets"][name] = metrics
            if name == "epa":
                frame = process_epa(read_csv(spark, args.epa_input, columns=EPA_COLUMNS), args.year, metrics)
            elif name == "noaa":
                paths = [path_join(args.noaa_input, f"{args.year}{month:02d}-scaled.csv") for month in range(1, 13)]
                noaa = read_csv(spark, paths, columns=NOAA_COLUMNS).withColumn(
                    "_file", F.regexp_extract(F.input_file_name(), r"([^/]+)$", 1))
                frame = process_noaa(noaa, args.year, len(paths), metrics)
            else:
                frame = process_census(read_csv(spark, args.census_input, encoding="windows-1252"), args.year, metrics)
            frames[name] = frame.persist()
            if name != "census":
                metrics.update(date_bounds(frame))
            metrics.update(schema=describe(frame), processing_seconds=round(time.perf_counter() - tick, 3))

        report["stage"] = "join"
        tick = time.perf_counter()
        joined = (frames["epa"].join(frames["noaa"].drop("year", "month"), list(DAILY_KEY), "inner")
                  .join(frames["census"].select(*GEO_KEY, "population_estimate"), list(GEO_KEY), "left")
                  .select(*EPA_COLUMNS, "fips", "tmax_c", "tmin_c", "tavg_c", "precipitation_mm",
                          "population_estimate", "year", "month").persist())
        frames["aqi_weather"] = joined
        joined_metrics = joined.agg(F.count("*").alias("output_rows"),
                                    count_if(F.col("population_estimate").isNull()).alias("unmatched_census_rows"),
                                    F.min("measurement_date").alias("date_min"),
                                    F.max("measurement_date").alias("date_max")).first().asDict()
        rows = joined_metrics["output_rows"]
        census_nulls = joined_metrics["unmatched_census_rows"]
        epa_rows = report["datasets"]["epa"]["output_rows"]
        duplicates = duplicate_rows(joined, DAILY_KEY)
        report["datasets"]["aqi_weather"] = {
            **joined_metrics, "epa_input_rows": epa_rows, "noaa_input_rows": report["datasets"]["noaa"]["output_rows"],
            "census_input_rows": report["datasets"]["census"]["output_rows"],
            "unmatched_epa_rows": epa_rows - rows, "epa_noaa_coverage_pct": round(100 * rows / epa_rows, 4),
            "census_matches": rows - census_nulls, "census_coverage_pct": round(100 * (rows - census_nulls) / rows, 4) if rows else None,
            "missing_values": {"population_estimate": census_nulls}, "duplicate_rows": duplicates, "schema": describe(joined),
            "output": outputs["aqi_weather"], "processing_seconds": round(time.perf_counter() - tick, 3),
        }
        if duplicates:
            raise QualityError("La integración contiene claves diarias duplicadas.")
        if not rows or (args.expected_joined is not None and rows != args.expected_joined):
            raise QualityError(f"La integración produjo {rows} filas. Conteo esperado: {args.expected_joined}.")

        for name, frame in frames.items():
            report["stage"] = "write_" + name
            tick = time.perf_counter()
            partition = PARTITIONS[name]
            (frame.repartition(*partition).write.mode("errorifexists")
             .partitionBy(*partition).parquet(outputs[name]))
            report["datasets"][name]["write_seconds"] = round(time.perf_counter() - tick, 3)
        report.update(status="passed", stage="complete")
    except Exception as error:
        report["error"] = str(error) if isinstance(error, QualityError) else type(error).__name__
        raise
    finally:
        report["elapsed_seconds"] = round(time.perf_counter() - start, 3)
        try:
            write_report(spark, args.report_path, report)
        finally:
            for frame in frames.values():
                frame.unpersist()
    return report


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Procesa las fuentes Raw EPA, NOAA y Census hacia Trusted.")
    paths = ("epa_input", "noaa_input", "census_input", "output_root", "report_path")
    for name in paths:
        parser.add_argument("--" + name.replace("_", "-"), "--" + name, dest=name, required=True)
    parser.add_argument("--year", type=int, default=2024)
    parser.add_argument("--expected-joined", "--expected_joined", dest="expected_joined", type=int)
    parser.add_argument("--JOB_NAME")
    args, unknown = parser.parse_known_args(argv)
    if unknown and not args.JOB_NAME:
        parser.error("Argumentos no reconocidos: " + " ".join(unknown))
    if not 1000 <= args.year <= 9999 or (args.expected_joined is not None and args.expected_joined < 1):
        parser.error("Usa un año de cuatro dígitos y un conteo esperado positivo.")
    for name in paths:
        location = getattr(args, name)
        if location.startswith("file:"):
            setattr(args, name, unquote(location))
    return args


def main(argv=None):
    args = parse_args(argv)
    if args.JOB_NAME:
        from awsglue.context import GlueContext
        from awsglue.job import Job

        context = GlueContext(SparkContext.getOrCreate())
        job = Job(context)
        job.init(args.JOB_NAME, {"JOB_NAME": args.JOB_NAME})
        run_etl(context.spark_session, args)
        job.commit()
    else:
        spark = SparkSession.builder.appName("airquality-raw-to-trusted").getOrCreate()
        try:
            run_etl(spark, args)
        finally:
            spark.stop()


if __name__ == "__main__":
    main()
