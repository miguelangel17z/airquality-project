# Requisito 4. ETL Raw a Trusted

[El script PySpark](../scripts/etl_raw_to_trusted.py) lee las tres fuentes originales de S3 Raw y produce cuatro datasets Parquet. Se ejecuta en Spark 3.5.4 local o en AWS Glue 5.0 con Python 3.11. El trabajo de Glue lee archivos de S3 mediante su rol de ejecución. [Versiones de Glue](https://docs.aws.amazon.com/glue/latest/dg/release-notes.html).

| Dataset | Entrada o unión | Preparación | Particiones |
| --- | --- | --- | --- |
| `epa` | `raw/rds/epa/year=2024/` | Declara las diez columnas del CSV. Conserva los códigos geográficos como texto, convierte la fecha y los enteros, y valida la clave diaria. | `year`, `month` |
| `noaa` | Los doce archivos `YYYYMM-scaled.csv` en `raw/ec2/noaa/year=2024/` | Divide FIPS en dos y tres caracteres. Valida fechas del mes del archivo y medidas decimales con escala 2. Descarta solo filas de fecha vacía con `-999.99` en las cuatro medidas cuando, por archivo y FIPS, hay todos los días reales y exactamente `31 − días del mes` filas de relleno. Febrero de 2024 conserva el día 29. | `year`, `month` |
| `census` | `raw/url/census/year=2024/co-est2024-alldata.csv` | Lee Windows-1252, selecciona `SUMLEV=050`, conserva FIPS y tipifica `POPESTIMATE2024` como `bigint`. Comprueba una fila por condado. | `year` |
| `aqi_weather` | `INNER JOIN` EPA–NOAA por estado, condado y fecha seguido de `LEFT JOIN` Census por estado y condado | Conserva todos los resultados EPA–NOAA. `population_estimate` queda nulo donde no hay pareja Census. | `year`, `month` |

Cada tabla queda en su subdirectorio de `trusted/`. Antes de escribir se validan las fuentes, las claves y el conteo esperado de la unión. Los destinos se comprueban al inicio y las escrituras usan `errorifexists`. Si un intento deja archivos parciales, usar otro prefijo Trusted y otro reporte para repetirlo. El informe JSON se guarda fuera de los directorios Parquet e incluye conteos, faltantes, duplicados, relleno, rangos, coberturas y tiempos. En NOAA, `missing_values.date` mide las fechas vacías Raw y `padding_discarded` mide las filas verificadas. La cobertura EPA–NOAA usa el total EPA como denominador. La cobertura Census usa las filas de la unión EPA–NOAA.

## Reproducción local

Usar Java 17 y Python 3.11 para igualar las versiones mayores de Glue. En Windows, Hadoop necesita `winutils.exe` y `hadoop.dll` disponibles a través de `HADOOP_HOME` y `PATH`. Esos componentes son exclusivos del entorno Windows. [Hadoop en Windows](https://cwiki.apache.org/confluence/display/HADOOP2/WindowsProblems).

Desde la raíz del proyecto, en Bash, con las copias locales Raw. La copia EPA de esta verificación reproduce las columnas y el SHA-256 del CSV del manifiesto del módulo 3. [Evidencia de ingesta](../../03-ingesta-datos/evidencias/ingest_s3_dry_run_local.json).

```bash
python -m pip install -r 04-procesamiento-datos/requirements.txt
python -m unittest discover -s 04-procesamiento-datos/tests -p 'test_etl_raw_to_trusted.py' -v

spark-submit --master 'local[2]' --conf spark.sql.shuffle.partitions=8 \
  04-procesamiento-datos/scripts/etl_raw_to_trusted.py \
  --epa-input .cache/trusted-inputs/epa/daily_aqi_by_county_2024.csv \
  --noaa-input .cache/noaa-epinoaa-2024 \
  --census-input .cache/census-popest-2024/co-est2024-alldata.csv \
  --output-root .cache/trusted-run-2024/trusted \
  --report-path .cache/trusted-run-2024/quality/report.json \
  --year 2024 --expected-joined 323540
```

## Resultados de la ejecución local

| Dataset | Filas Raw | Filas Parquet | Particiones verificadas |
| --- | ---: | ---: | ---: |
| EPA | 329 597 | 329 597 | 12 mensuales |
| NOAA | 1 155 804 | 1 137 162 | 12 mensuales |
| Census | 3 195 | 3 144 | 1 anual |
| AQI, meteorología y población | Unión de las tres fuentes | 323 540 | 12 mensuales |

Se verificaron 18 642 filas de relleno NOAA y 51 filas de nivel estatal en Census. En las cuatro salidas se verificaron cero claves duplicadas. Los tres conjuntos diarios abarcan del `2024-01-01` al `2024-12-31`. EPA–NOAA logró 98,1623 % de cobertura sobre EPA. Census aportó población para 320 942 filas de la unión, una cobertura de 99,197 %. Las 2 598 filas restantes conservaron `population_estimate` nulo.

La lectura posterior de Parquet verificó conteos, tipos y particiones. Los códigos y FIPS son `string`, las fechas `date`, AQI y sitios reportantes `int`, las temperaturas `decimal(6,2)`, la precipitación `decimal(8,2)` y la población `bigint`. `year` y `month` se leen como `int`. [Reporte de calidad y esquemas completos](../evidencias/etl_raw_to_trusted_local.json) · [Pruebas PySpark](../evidencias/etl_spark_local_tests.json).

## Despliegue posterior en Glue 5.0

1. Seleccionar un rol existente que permita a `glue.amazonaws.com` asumirlo. El rol necesita listar el bucket y leer el script y Raw. El acceso de escritura, incluyendo la limpieza de temporales de Spark, se limita a Trusted, `quality/` y `glue-temp/`; también requiere permisos para logs y, si aplica, SSE-KMS. El operador que configura el job debe poder pasar el rol con `iam:PassRole`.
2. Transferir el script y configurar un Spark Job `glueetl`, Glue 5.0, Python, una ejecución simultánea y bookmarks desactivados, ya que se lee la carga completa de 2024. Con un job existente, usar esa configuración para ejecutarlo. El runtime de Glue ya incluye PySpark. [Propiedades del job](https://docs.aws.amazon.com/glue/latest/dg/add-job.html) · [Parámetros de Glue](https://docs.aws.amazon.com/glue/latest/dg/aws-glue-programming-etl-glue-arguments.html).
3. Pasar rutas de las tres fuentes, salida Trusted y reporte separado. Los comandos siguientes corresponden a una ejecución futura autorizada.

```bash
export AWS_REGION=us-east-1
BUCKET=airquality-datalake-2024
GLUE_JOB=airquality-raw-to-trusted-2024
read -r -p 'ARN del rol existente para Glue: ' GLUE_ROLE_ARN
SCRIPT_URI="s3://$BUCKET/scripts/airquality/etl_raw_to_trusted.py"

aws s3 cp 04-procesamiento-datos/scripts/etl_raw_to_trusted.py "$SCRIPT_URI" --region "$AWS_REGION"
aws glue create-job --region "$AWS_REGION" --name "$GLUE_JOB" --role "$GLUE_ROLE_ARN" \
  --glue-version 5.0 --worker-type G.1X --number-of-workers 2 --max-retries 0 --timeout 20 \
  --execution-property MaxConcurrentRuns=1 \
  --command "{\"Name\":\"glueetl\",\"ScriptLocation\":\"$SCRIPT_URI\",\"PythonVersion\":\"3\"}" \
  --default-arguments "{\"--job-language\":\"python\",\"--job-bookmark-option\":\"job-bookmark-disable\",\"--TempDir\":\"s3://$BUCKET/glue-temp/airquality/\"}"

ARGS=$(cat <<EOF
{
  "--epa_input": "s3://$BUCKET/raw/rds/epa/year=2024/",
  "--noaa_input": "s3://$BUCKET/raw/ec2/noaa/year=2024/",
  "--census_input": "s3://$BUCKET/raw/url/census/year=2024/co-est2024-alldata.csv",
  "--output_root": "s3://$BUCKET/trusted/",
  "--report_path": "s3://$BUCKET/quality/year=2024/etl_raw_to_trusted.json",
  "--year": "2024",
  "--expected_joined": "323540"
}
EOF
)
RUN_ID=$(aws glue start-job-run --region "$AWS_REGION" --job-name "$GLUE_JOB" \
  --arguments "$ARGS" --query JobRunId --output text)
aws glue get-job-run --region "$AWS_REGION" --job-name "$GLUE_JOB" --run-id "$RUN_ID" \
  --query 'JobRun.{State:JobRunState,Error:ErrorMessage}'
```

Al confirmar `SUCCEEDED`, comprobar los prefijos y el reporte:

```bash
aws s3 ls "s3://$BUCKET/trusted/" --region "$AWS_REGION"
aws s3 ls "s3://$BUCKET/trusted/aqi_weather/year=2024/" --region "$AWS_REGION"
aws s3 cp "s3://$BUCKET/quality/year=2024/etl_raw_to_trusted.json" - --region "$AWS_REGION"
```
