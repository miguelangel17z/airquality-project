# Relevo del Proyecto 2 de Big Data · SI3007

El ZIP contiene los módulos 01 a 04 tal como están en el directorio de trabajo. Incluye los scripts, SQL, pruebas, dependencias y evidencias. Empieza por [README.md](README.md), revisa [AGENTS.md](AGENTS.md) y usa [INVENTARIO.tsv](INVENTARIO.tsv) para comprobar los archivos. Los datos se reconstruyen desde las fuentes públicas indicadas abajo.

En el proyecto de origen se reportó la ingesta RDS y S3 Raw como completada. El ETL Raw → Trusted se ejecutó localmente con Spark 3.5.4 y datos oficiales. Queda pendiente ejecutarlo en un Spark Job de Glue 5.0. Los módulos 05 a 08 figuran como pendientes en el README. Los comandos de esta guía son para el integrante que continúe en su propia cuenta AWS.

## Sustituir los valores de la cuenta anterior

| Referencia histórica | Configuración de la nueva cuenta |
| --- | --- |
| Región `us-east-1` | Establecer `AWS_REGION` y `AWS_DEFAULT_REGION` para la región elegida. Usar esa región en AWS CLI y en el certificado de RDS si EC2, RDS, S3 y Glue están en la misma región. |
| EC2 `airquality-ec2`, `ec2-user`, `~/airquality-project` | Usar `EC2_TARGET` con el usuario y DNS o alias SSH propios. Extraer el ZIP en `$HOME` para conservar `~/airquality-project`, o pasar rutas explícitas a los scripts si se usa otro destino. |
| RDS `airquality-mariadb`, MariaDB 11.4, puerto `3306` | Establecer `DB_HOST` con el endpoint DNS de la instancia nueva y `DB_PORT` con el puerto configurado. Establecer `DB_SSL_CA` con la CA pública de la región. |
| Base `airquality` y usuarios de administración, carga y lectura | Conservar `DB_NAME=airquality` para coincidir con los SQL. Usar el usuario correspondiente en `DB_ADMIN_USER` o `DB_USER`. Para cambiar la base lógica también habría que actualizar las referencias calificadas de los SQL del módulo 03. |
| Variables `MARIADB_*` del módulo 02 | Solo para cargar estaciones. Usar `MARIADB_HOST`, `MARIADB_PORT`, `MARIADB_USER`, `MARIADB_SSL_CA` y `MARIADB_PASSWORD` con la conexión autorizada. Ese SQL usa `air_quality_source.stations`. La [plantilla](02-fuentes-datos/.env.example) trae campos vacíos y el script lee el entorno del proceso. |
| Bucket `airquality-datalake-2024` | Definir `S3_BUCKET` con el nombre globalmente único de la cuenta nueva. Pasar `--bucket` y `--region` en la ingesta. El script conserva valores predeterminados históricos, de modo que en los ejemplos se especifican ambos argumentos. Usar el mismo `S3_BUCKET` en todas las rutas del job. |
| Rol EC2 `LabRole` | Adjuntar un perfil de instancia autorizado en la cuenta nueva. Boto3 obtiene credenciales temporales mediante la cadena predeterminada. El rol debe poder listar el bucket, leer los objetos y escribir bajo `raw/` y `manifests/`. |
| Rol y nombre del job Glue | Definir `GLUE_ROLE_ARN` con el rol autorizado de la cuenta nueva y `GLUE_JOB` con el nombre del job. El rol debe admitir `glue.amazonaws.com`, leer el script y `raw/`, y escribir en `trusted/`, `quality/` y `glue-temp/`. Considerar el acceso a los logs y a la clave KMS si aplica. El operador necesita `iam:PassRole`. |
| Año `2024`, prefijos Raw y rutas locales | Conservar `INGEST_YEAR=2024` para reproducir las evidencias. `--noaa-dir`, `--census-url` y `--manifest-dir` permiten cambiar entradas o ubicación del manifiesto. El ETL recibe explícitamente `--epa_input`, `--noaa_input`, `--census_input`, `--output_root` y `--report_path` en Glue. |

En EC2 y RDS usa red y grupos de seguridad de la cuenta nueva. RDS debe ser privado y aceptar su puerto únicamente desde el grupo de EC2. EC2 necesita acceso a las fuentes HTTPS públicas, los repositorios de software y S3. Glue lee S3 y no requiere conexión a RDS. La [guía de infraestructura](02-fuentes-datos/docs/plan-despliegue.md) y el [plan de costos y pausas](02-fuentes-datos/docs/plan-costos.md) detallan las decisiones que deben verificarse en el laboratorio.

## Checklist de reproducción

- [ ] **Preparar cuenta y EC2.** Confirmar presupuesto, región, acceso administrativo a EC2, conectividad privada a RDS, bucket S3 propio y roles IAM. Para el traslado, desde una estación con Bash y acceso SSH autorizado, usa el ZIP recibido. En ese comando, `EC2_TARGET` contiene `usuario@host` o un alias SSH de la cuenta nueva.

  ```bash
  read -r -p 'Destino SSH de la EC2 nueva: ' EC2_TARGET
  scp SI3007_Proyecto2_Airquality_entrega.zip "$EC2_TARGET:~/"
  ssh "$EC2_TARGET"
  ```

- [ ] **Instalar en EC2.** En Amazon Linux 2023, comprobar Python 3.9, el cliente MariaDB, AWS CLI, `curl` y `unzip`. Extraer en un directorio nuevo. La opción `-n` conserva cualquier archivo existente, por lo que primero se comprueba que el destino esté libre.

  ```bash
  cd "$HOME"
  command -v mysql >/dev/null || sudo dnf install -y mariadb105
  command -v unzip >/dev/null || sudo dnf install -y unzip
  test ! -e airquality-project || { printf '%s\n' 'Ya existe ~/airquality-project. Elige un directorio nuevo.'; exit 1; }
  unzip -n SI3007_Proyecto2_Airquality_entrega.zip
  cd ~/airquality-project
  python3 --version
  aws --version
  python3 -m venv .venv
  source .venv/bin/activate
  python -m pip install -r 03-ingesta-datos/requirements.txt
  python -m unittest discover -s 03-ingesta-datos/tests -v
  python -m unittest discover -s 04-procesamiento-datos/tests -p 'test_export_integrated_dataset.py' -v
  ```

  Los módulos 01 y 02 requieren Python 3.11 o posterior. Para sus validaciones, usar un intérprete aparte, por ejemplo `python3.11 -m unittest discover -s 02-fuentes-datos/tests -v` desde la raíz. La carga opcional de estaciones requiere además instalar `02-fuentes-datos/requirements.txt` en ese entorno. Las pruebas de integración con MariaDB requieren una instancia local dedicada con TLS. Los comandos de arriba usan los casos aislados por defecto. Para habilitar la integración, consultar el [README de ingesta](03-ingesta-datos/README.md#pruebas-locales) y el [de exportación](04-procesamiento-datos/README.md#pruebas-locales). Para probar Spark, usar Python 3.11 y Java 17 con [las instrucciones del ETL](04-procesamiento-datos/docs/etl_raw_to_trusted.md#reproducción-local).

- [ ] **Obtener EPA y NOAA.** Desde EC2, descarga el ZIP oficial de AQI y los doce CSV de NOAA. `ingest_to_s3.py` descarga el CSV de Census al validar o ejecutar `--source all`. Conserva los archivos NOAA completos con sus filas de relleno. Para repetir la validación del módulo 01, usar [su README](01-caso-estudio/README.md), que también cubre los ZIP de estaciones y ozono horario.

  ```bash
  export INGEST_YEAR=2024
  export AQI_CSV="$PWD/data/raw/epa/daily_aqi_by_county_2024.csv"
  export NOAA_DIR="$PWD/data/raw/noaa"
  export CENSUS_URL='https://www2.census.gov/programs-surveys/popest/datasets/2020-2024/counties/totals/co-est2024-alldata.csv'
  mkdir -p "$PWD/data/raw/epa" "$NOAA_DIR" "$PWD/.cache"
  EPA_ZIP="$PWD/.cache/daily_aqi_by_county_2024.zip"
  curl -fSL --retry 3 'https://aqs.epa.gov/aqsweb/airdata/daily_aqi_by_county_2024.zip' -o "$EPA_ZIP"
  unzip -t "$EPA_ZIP" && unzip -n -j "$EPA_ZIP" 'daily_aqi_by_county_2024.csv' -d "$(dirname "$AQI_CSV")"
  for MONTH in {01..12}; do
    FILE="${INGEST_YEAR}${MONTH}-scaled.csv"
    test ! -f "$NOAA_DIR/$FILE" || continue
    curl -fSL --retry 3 "https://noaa-nclimgrid-daily-pds.s3.amazonaws.com/EpiNOAA/v1-0-0/csv/cty/$FILE" \
      -o "$NOAA_DIR/$FILE.part" && mv "$NOAA_DIR/$FILE.part" "$NOAA_DIR/$FILE" || break
  done
  python 03-ingesta-datos/scripts/load_daily_aqi.py --csv "$AQI_CSV" --dry-run
  python 03-ingesta-datos/scripts/load_daily_weather.py --input-dir "$NOAA_DIR" --dry-run
  ```

- [ ] **Inicializar RDS y cargar AQI.** En la sesión de EC2, usar el DNS de RDS y verificar TLS con la CA regional. Configurar el usuario de carga con `SELECT`, `INSERT` y `UPDATE` en las tablas que cargue, y el usuario de ingesta S3 con `SELECT` en AQI. Al ejecutar el cliente o el script, introducir la contraseña cuando la solicite el terminal.

  ```bash
  read -r -p 'Región AWS de la cuenta nueva: ' AWS_REGION
  export AWS_REGION AWS_DEFAULT_REGION="$AWS_REGION"
  read -r -p 'Endpoint DNS del RDS nuevo: ' DB_HOST
  read -r -p 'Puerto RDS [3306]: ' DB_PORT
  read -r -p 'Usuario administrador de RDS: ' DB_ADMIN_USER
  export DB_HOST DB_PORT="${DB_PORT:-3306}" DB_NAME=airquality
  mkdir -p "$HOME/.cache/airquality"
  export DB_SSL_CA="$HOME/.cache/airquality/$AWS_REGION-bundle.pem"
  curl -fsSL "https://truststore.pki.rds.amazonaws.com/$AWS_REGION/$AWS_REGION-bundle.pem" -o "$DB_SSL_CA"
  unset DB_PASSWORD
  MYSQL_TLS=(--host="$DB_HOST" --port="$DB_PORT" --ssl --ssl-ca="$DB_SSL_CA" --ssl-verify-server-cert)
  mysql "${MYSQL_TLS[@]}" --user="$DB_ADMIN_USER" --password < 03-ingesta-datos/sql/create_schema.sql
  read -r -p 'Usuario de carga de AQI: ' DB_USER
  export DB_USER
  python 03-ingesta-datos/scripts/load_daily_aqi.py --csv "$AQI_CSV" --batch-size 1000
  mysql "${MYSQL_TLS[@]}" --user="$DB_USER" --password --database="$DB_NAME" \
    --execute="SELECT COUNT(*) FROM daily_aqi_by_county WHERE measurement_date >= '2024-01-01' AND measurement_date < '2025-01-01';"
  ```

  Resultado de referencia `329597`. Para reproducir también la tabla NOAA y la vista integrada de RDS, aplicar `03-ingesta-datos/sql/create_weather_schema.sql`, cargar `load_daily_weather.py` y consultar `03-ingesta-datos/sql/verify_weather_integration.sql` según la [fase 2](03-ingesta-datos/README.md#fase-2-noaa-epinoaa). La vista debe devolver `323540` filas. La [exportación de la vista](04-procesamiento-datos/README.md) y la [carga de estaciones](02-fuentes-datos/README.md#carga-prevista-desde-ec2-en-2b) son flujos separados. El ETL consume los tres orígenes Raw.

- [ ] **Ingerir S3 Raw.** Cambiar a la cuenta de solo lectura de MariaDB. El dry-run lee RDS y la URL pública y escribe el manifiesto local. Pasar después al modo de escritura con el mismo bucket nuevo y revisar el manifiesto antes de seguir a Glue.

  ```bash
  read -r -p 'Nombre del bucket S3 de la cuenta nueva: ' S3_BUCKET
  read -r -p 'Usuario RDS de solo lectura: ' DB_USER
  export S3_BUCKET DB_USER
  unset DB_PASSWORD
  INGEST_ARGS=(--source all --bucket "${S3_BUCKET:?Indica el bucket nuevo}" --region "${AWS_REGION:?Indica la región}"
    --year "$INGEST_YEAR" --noaa-dir "$NOAA_DIR" --census-url "$CENSUS_URL" --expected-rds-rows 329597)
  python 03-ingesta-datos/scripts/ingest_to_s3.py "${INGEST_ARGS[@]}" --dry-run
  python 03-ingesta-datos/scripts/ingest_to_s3.py "${INGEST_ARGS[@]}"
  aws s3 ls "s3://$S3_BUCKET/raw/" --recursive --region "$AWS_REGION"
  aws s3 ls "s3://$S3_BUCKET/manifests/year=$INGEST_YEAR/" --region "$AWS_REGION"
  ```

  Verificar 14 objetos, con `329597` filas EPA en `raw/rds/epa/`, `1155804` filas NOAA repartidas en los doce objetos de `raw/ec2/noaa/` y `3195` filas Census en `raw/url/census/`. Un reintento con los mismos bytes debe informar `skipped_identical`. Si el manifiesto indica `conflict` o `error`, investigar la clave antes de avanzar.

- [ ] **Configurar Glue 5.0.** Usar un Spark Job `glueetl` (Spark 3.5.4, Python 3.11) con bookmarks desactivados y un rol Glue de la cuenta nueva. El [procedimiento del ETL](04-procesamiento-datos/docs/etl_raw_to_trusted.md#despliegue-posterior-en-glue-50) incluye la configuración de referencia. Desde una sesión de AWS CLI autorizada para administrar Glue, con `S3_BUCKET`, `AWS_REGION` e `INGEST_YEAR` definidos y desde la raíz del proyecto, ejecutar lo siguiente para un job nuevo.

  ```bash
  read -r -p 'Nombre del job Glue: ' GLUE_JOB
  read -r -p 'ARN del rol Glue de la cuenta nueva: ' GLUE_ROLE_ARN
  SCRIPT_URI="s3://${S3_BUCKET:?Indica el bucket nuevo}/scripts/airquality/etl_raw_to_trusted.py"
  aws s3 cp 04-procesamiento-datos/scripts/etl_raw_to_trusted.py "$SCRIPT_URI" --region "$AWS_REGION"
  aws glue create-job --region "$AWS_REGION" --name "$GLUE_JOB" --role "$GLUE_ROLE_ARN" \
    --glue-version 5.0 --worker-type G.1X --number-of-workers 2 --max-retries 0 --timeout 20 \
    --execution-property MaxConcurrentRuns=1 \
    --command "{\"Name\":\"glueetl\",\"ScriptLocation\":\"$SCRIPT_URI\",\"PythonVersion\":\"3\"}" \
    --default-arguments "{\"--job-language\":\"python\",\"--job-bookmark-option\":\"job-bookmark-disable\",\"--TempDir\":\"s3://$S3_BUCKET/glue-temp/airquality/\"}"
  ```

- [ ] **Ejecutar Glue y comprobar Trusted.** Si ya existe un job, comprobar su versión, script y rol antes de usarlo. Elegir destinos nuevos para un reintento tras una salida parcial. El ETL comprueba que los destinos estén libres antes de escribir.

  ```bash
  RAW="s3://$S3_BUCKET/raw"
  TRUSTED="s3://$S3_BUCKET/trusted/"
  REPORT="s3://$S3_BUCKET/quality/year=$INGEST_YEAR/etl_raw_to_trusted.json"
  ARGS=$(cat <<EOF
  {
    "--epa_input": "$RAW/rds/epa/year=$INGEST_YEAR/",
    "--noaa_input": "$RAW/ec2/noaa/year=$INGEST_YEAR/",
    "--census_input": "$RAW/url/census/year=$INGEST_YEAR/co-est2024-alldata.csv",
    "--output_root": "$TRUSTED",
    "--report_path": "$REPORT",
    "--year": "$INGEST_YEAR",
    "--expected_joined": "323540"
  }
  EOF
  )
  RUN_ID=$(aws glue start-job-run --region "$AWS_REGION" --job-name "$GLUE_JOB" \
    --arguments "$ARGS" --query JobRunId --output text)
  aws glue get-job-run --region "$AWS_REGION" --job-name "$GLUE_JOB" --run-id "$RUN_ID" \
    --query 'JobRun.{State:JobRunState,Error:ErrorMessage}'
  ```

  Volver a consultar el estado hasta `SUCCEEDED`. Luego listar Trusted y descargar o consultar el reporte de calidad.

  ```bash
  aws s3 ls "$TRUSTED" --region "$AWS_REGION"
  aws s3 ls "${TRUSTED}aqi_weather/year=$INGEST_YEAR/" --region "$AWS_REGION"
  aws s3 cp "$REPORT" - --region "$AWS_REGION"
  ```

  Comparar con las filas de referencia de abajo. Guardar una evidencia del estado del job, los prefijos y el reporte con conteos y esquemas. Depurar cualquier identificador de cuenta y ruta personal antes de compartirla.

| Salida Trusted | Filas esperadas | Particiones |
| --- | ---: | --- |
| `epa` | 329 597 | Año y mes |
| `noaa` | 1 137 162 | Año y mes |
| `census` | 3 144 | Año |
| `aqi_weather` | 323 540 | Año y mes |

En la ejecución de referencia, NOAA descartó 18 642 filas de relleno y EPA–NOAA alcanzó 98,16 % de cobertura sobre EPA. Census aportó población a 320 942 filas de la unión. Consulta los detalles en el [reporte local de calidad](04-procesamiento-datos/evidencias/etl_raw_to_trusted_local.json) y la [guía técnica](04-procesamiento-datos/docs/etl_raw_to_trusted.md).

## Evidencia y revisión previa a compartir

- El paquete conserva las evidencias locales de validación de EPA, las pruebas MariaDB, el dry-run de S3 con datos oficiales, las transferencias S3 simuladas y la ejecución Parquet local. Sus estados describen el entorno original de prueba.
- En la copia del [dry-run de ingesta](03-ingesta-datos/evidencias/ingest_s3_dry_run_local.json) se sustituyó el nombre de bucket por `<S3_BUCKET>`. Se conservaron conteos, huellas, tiempos y estados. Las guías y el código conservan los valores históricos de configuración para su trazabilidad. Los comandos de esta guía usan los valores propios de la cuenta nueva.
- El estado del repositorio al preparar la entrega era `main` sin commits, sin remotos y con el índice vacío. Los 61 archivos de `.gitignore`, `README.md`, `AGENTS.md` y los módulos `01-caso-estudio/`, `02-fuentes-datos/`, `03-ingesta-datos/` y `04-procesamiento-datos/` estaban sin seguimiento. El campo `git_state` del inventario identifica cada archivo de origen para revisar antes de agregarlo a un repositorio compartido. `GUIA_RELEVO.md` y `INVENTARIO.tsv` se generaron para el ZIP.
