# Ingesta diaria de AQI y meteorología en MariaDB

Las fases EPA y NOAA están desplegadas y validadas en RDS según el estado confirmado del proyecto. La base `airquality` contiene 329 597 filas EPA y 1 137 162 filas NOAA. La vista reúne 323 540 registros de 2024. El [módulo de extracción](../04-procesamiento-datos/README.md) exporta esa vista para su análisis. La [fase de ingesta a S3](#fase-3-ingesta-automática-a-s3) implementa el requisito 3 del Proyecto 2 de SI3007.

## Fase 1. AQI diario de EPA

El cargador toma `~/airquality-project/data/raw/epa/daily_aqi_by_county_2024.csv` en `airquality-ec2` y lo carga en `airquality.daily_aqi_by_county` en `airquality-mariadb`, en `us-east-1`. La ruta se puede cambiar con `--csv`.

## Contrato y comportamiento

- El [DDL](sql/create_schema.sql) crea la base y la tabla con `IF NOT EXISTS`. La tabla InnoDB usa `utf8mb4`, diez columnas obligatorias, clave primaria `(state_code, county_code, measurement_date)` e índice secundario sobre `measurement_date`.
- El cargador lee CSV UTF-8 con o sin BOM y reconoce `County Name` y la variante `county Name` observada en AirData. Comprueba encabezado, ancho de fila, campos obligatorios, longitudes, fechas ISO válidas y enteros en el rango de `SMALLINT UNSIGNED`. `state_code` y `county_code` conservan los ceros iniciales y deben tener dos y tres dígitos ASCII, respectivamente. Una clave repetida dentro del CSV cuenta como registro inválido. [Formato en el módulo 1](../01-caso-estudio/diccionario-datos.md).
- El script usa lotes parametrizados de 1 000 filas por defecto y una transacción para todo el archivo. Verifica que el destino use InnoDB y tenga la clave primaria esperada. Una clave ya cargada con datos idénticos cuenta como `already_present`. Un dato distinto para la misma clave, un registro inválido o un error de escritura cancela la transacción. La recarga del mismo archivo conserva las filas existentes. [Semántica de la sentencia en MariaDB](https://mariadb.com/docs/server/reference/sql-statements/data-manipulation/inserting-loading-data/insert-on-duplicate-key-update).
- `--dry-run` valida todo el CSV sin conexión, dependencia instalada ni contraseña. El resumen JSON informa `processed`, `inserted`, `already_present`, `invalid_records`, `errors` y `elapsed_seconds`. Los diagnósticos van a la salida de error e indican el campo y la línea de hasta diez filas inválidas, sin incluir valores del CSV ni datos de conexión. Los códigos de salida son `0` para éxito, `1` para fallo, `2` para argumentos inválidos y `130` para interrupción.

## Pruebas locales

Desde la raíz, con Python 3.9 o posterior.

```bash
python3 -m unittest discover -s 03-ingesta-datos/tests -v
python3 03-ingesta-datos/scripts/load_daily_aqi.py --dry-run --csv /ruta/al/archivo.csv
```

El [dry-run de la copia oficial](evidencias/dry_run_epa.json) comprobó 329 597 filas. La [integración local](evidencias/mariadb_local.json) usó Python 3.9.13, MariaDB 11.4.10 y PyMySQL 1.2.4 con datos de prueba. La EC2 del proyecto usa Python 3.9.25 y el RDS usa MariaDB 11.4.13, según el entorno suministrado.

## Instalación y ejecución posterior en EC2

Desde la raíz del repositorio local, usar la conexión SSH ya autorizada. Este ejemplo usa Bash y una identidad disponible en el agente SSH. Si la conexión vigente usa una clave explícita, añadir `-i` con su ruta local a `ssh` y `scp`.

```bash
read -r -p 'DNS o alias SSH de airquality-ec2: ' EC2_HOST
EC2_TARGET="ec2-user@$EC2_HOST"
ssh "$EC2_TARGET" 'mkdir -p "$HOME/airquality-project/03-ingesta-datos/scripts" "$HOME/airquality-project/03-ingesta-datos/sql"'
scp 03-ingesta-datos/requirements.txt "${EC2_TARGET}:~/airquality-project/03-ingesta-datos/"
scp 03-ingesta-datos/scripts/load_daily_aqi.py "${EC2_TARGET}:~/airquality-project/03-ingesta-datos/scripts/"
scp 03-ingesta-datos/sql/create_schema.sql "${EC2_TARGET}:~/airquality-project/03-ingesta-datos/sql/"
ssh "$EC2_TARGET"
```

En la sesión de EC2, comprobar la huella de la fuente, preparar el entorno y ejecutar el dry-run. Continuar al DDL solo si la validación devuelve código `0` y el conteo esperado.

```bash
cd ~/airquality-project
AQI_CSV="$HOME/airquality-project/data/raw/epa/daily_aqi_by_county_2024.csv"
sha256sum "$AQI_CSV"
python3 -m venv 03-ingesta-datos/.venv
source 03-ingesta-datos/.venv/bin/activate
python -m pip install -r 03-ingesta-datos/requirements.txt
python 03-ingesta-datos/scripts/load_daily_aqi.py --csv "$AQI_CSV" --dry-run
```

Para inicializar el esquema, usar una cuenta con permisos `CREATE` y el endpoint DNS de RDS. Si el cliente `mysql` no está disponible en AL2023, el comando instala únicamente el cliente. AWS publica el paquete y el certificado regional en estas referencias. [Cliente MariaDB en AL2023](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/mysql-install-cli.html) · [Certificados RDS](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/UsingWithRDS.SSL.html).

```bash
command -v mysql >/dev/null || sudo dnf install -y mariadb105
read -r -p 'Endpoint DNS de airquality-mariadb: ' DB_HOST
read -r -p 'Usuario autorizado para crear el esquema: ' DB_ADMIN_USER
export DB_HOST DB_PORT=3306 DB_NAME=airquality
mkdir -p "$HOME/.cache/airquality"
export DB_SSL_CA="$HOME/.cache/airquality/us-east-1-bundle.pem"
curl -fsSL https://truststore.pki.rds.amazonaws.com/us-east-1/us-east-1-bundle.pem -o "$DB_SSL_CA"
mysql --host="$DB_HOST" --port="$DB_PORT" --user="$DB_ADMIN_USER" --password \
  --ssl --ssl-ca="$DB_SSL_CA" --ssl-verify-server-cert < 03-ingesta-datos/sql/create_schema.sql
```

La cuenta de carga requiere `INSERT`, `SELECT` y `UPDATE` sobre `airquality.daily_aqi_by_county`. El cargador utiliza `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER` y `DB_PASSWORD`. Los valores predeterminados de puerto y base son `3306` y `airquality`. Si falta `DB_PASSWORD`, un terminal interactivo permite ingresarla sin eco. Para automatización, inyectar la variable al proceso mediante el mecanismo de secretos autorizado. `DB_SSL_CA` permite indicar la CA de RDS. El cliente verifica la cadena y el nombre del servidor con TLS 1.2 o posterior.

```bash
read -r -p 'Usuario de carga de AQI: ' DB_USER
export DB_USER
unset DB_PASSWORD
python 03-ingesta-datos/scripts/load_daily_aqi.py --csv "$AQI_CSV" --batch-size 1000
mysql --host="$DB_HOST" --port="$DB_PORT" --user="$DB_USER" --password \
  --ssl --ssl-ca="$DB_SSL_CA" --ssl-verify-server-cert --database="$DB_NAME" \
  --execute="SELECT COUNT(*) AS rows_2024 FROM daily_aqi_by_county WHERE measurement_date >= '2024-01-01' AND measurement_date < '2025-01-01';"
```

Para una tabla inicialmente vacía y el CSV validado, el conteo esperado de 2024 es 329 597. Una segunda carga del mismo archivo debe informar `inserted=0` y `already_present=329597`. Un fallo de comunicación durante `COMMIT` deja el resultado de esa confirmación sin verificar desde el cliente. Repetir el mismo archivo permite confirmar el estado mediante sus claves y valores.

## Fase 2. NOAA EpiNOAA

El [cargador NOAA](scripts/load_daily_weather.py) procesa `202401-scaled.csv` a `202412-scaled.csv` desde `~/airquality-project/data/raw/noaa/` o el directorio indicado con `--input-dir`. La fuente es [EpiNOAA v1-0-0 por condado](https://noaa-nclimgrid-daily-pds.s3.amazonaws.com/index.html#EpiNOAA/v1-0-0/csv/cty/). Los valores `scaled` ya están ajustados a las series mensuales de NOAA. Las temperaturas están en °C y la precipitación en mm, por lo que se conservan las unidades de origen. [Guía NOAA nClimGrid-Daily](https://www.ncei.noaa.gov/pub/data/daily-grids/v1-0-0/nclimgrid-daily_v1-0-0_user-guide.pdf).

### Limpieza, transacciones e integración

- `fips` debe tener cinco dígitos ASCII. Se separa como texto en `state_code` y `county_code` para conservar los ceros iniciales. Las fechas con contenido deben ser ISO válidas y pertenecer al mes indicado por el archivo. Se rechazan claves repetidas.
- Se descarta una fila como relleno solo ante una fecha vacía y `-999.99` en las cuatro medidas. El bloque debe estar al final del CSV, el condado debe tener todas las fechas reales del mes y su número de filas de relleno debe ser exactamente `31 − días del mes`. Se comprueba la presencia del relleno para todos los condados si el archivo contiene ese bloque. Febrero de 2024 admite el día 29 y tiene dos filas de relleno por condado. Fechas con contenido inválido, centinelas en filas con fecha y bloques incompletos causan error.
- Las cuatro medidas se convierten con `Decimal`, dentro de la precisión y el rango del esquema. Se rechazan valores vacíos, no numéricos o que requieran redondeo, precipitación negativa y temperaturas que incumplan `tmin <= tavg <= tmax`.
- La carga reutiliza la configuración TLS, las consultas parametrizadas y la detección de conflictos del cargador EPA. Lee un mes a la vez y mantiene los lotes y una máscara de días por FIPS en memoria. Cada mes válido se confirma por separado. Ante un error de datos o un conflicto, se revierte ese mes y se informa su estado. El resto de los archivos se intenta de forma independiente. Una recarga conserva los valores idénticos y cuenta esos registros en `already_present`.
- [El DDL NOAA](sql/create_weather_schema.sql) crea `daily_weather_by_county` con clave primaria por estado, condado y fecha e índice de fecha. Carga todas las observaciones meteorológicas válidas, incluso donde EPA carece de datos. La vista `v_aqi_weather_daily` usa `INNER JOIN` sobre esas tres columnas y expone las variables de AQI y meteorología sin materializar filas adicionales. Las [consultas de verificación](sql/verify_weather_integration.sql) muestran los rangos, el conteo NOAA, las coincidencias y los registros EPA sin coincidencia. La cobertura divide las coincidencias por el total EPA medido mediante `LEFT JOIN`; con cero filas EPA queda en `NULL`.

### Resultados locales con archivos oficiales

| Métrica | Verificado localmente |
| --- | ---: |
| Filas NOAA brutas | 1 155 804 |
| Relleno de calendario verificado | 18 642 |
| NOAA válidas | 1 137 162 |
| NOAA duplicadas o inválidas | 0 |
| Filas EPA | 329 597 |
| Coincidencias EPA–NOAA | 323 540 |
| EPA sin coincidencia | 6 057 |
| Cobertura de los registros EPA | 98,16 % |

El [dry-run NOAA](evidencias/dry_run_noaa.json) registra los conteos, tamaño, URL y SHA-256 de cada archivo. El [cruce local de claves](evidencias/epa_noaa_overlap_local.json) usó las copias públicas de NOAA y EPA y confirmó el rango del 1 de enero al 31 de diciembre de 2024 para ambos orígenes. La [integración SQL en MariaDB local](evidencias/weather_mariadb_local.json) comprobó las consultas y la vista con datos de prueba, incluida la conservación de una observación NOAA sin pareja EPA.

Para repetir el dry-run local con Python 3.9 y las copias oficiales, ejecutar desde la raíz. Para habilitar la prueba de integración, configurar `AIRQUALITY_TEST_DB_PORT` con el puerto de una instancia de prueba en `localhost` y las variables `DB_HOST=localhost`, `DB_USER`, `DB_PASSWORD` o un terminal interactivo y `DB_SSL_CA`. Esa prueba crea y elimina únicamente un esquema temporal de pruebas en `localhost`.

```bash
python3 -m unittest discover -s 03-ingesta-datos/tests -v
python3 03-ingesta-datos/scripts/load_daily_weather.py \
  --input-dir .cache/noaa-epinoaa-2024 --dry-run --report .cache/noaa_dry_run_repeat.json
```

### Transferencia y ejecución posterior en EC2

Desde la raíz del repositorio local, en Bash, con la identidad SSH autorizada para la instancia. Transferir ambos scripts porque el cargador NOAA importa las funciones compartidas de `load_daily_aqi.py`.

```bash
read -r -p 'DNS o alias SSH de airquality-ec2: ' EC2_HOST
EC2_TARGET="ec2-user@$EC2_HOST"
ssh "$EC2_TARGET" 'mkdir -p "$HOME/airquality-project/03-ingesta-datos/scripts" "$HOME/airquality-project/03-ingesta-datos/sql"'
scp 03-ingesta-datos/scripts/load_daily_aqi.py 03-ingesta-datos/scripts/load_daily_weather.py \
  "${EC2_TARGET}:~/airquality-project/03-ingesta-datos/scripts/"
scp 03-ingesta-datos/sql/create_weather_schema.sql 03-ingesta-datos/sql/verify_weather_integration.sql \
  "${EC2_TARGET}:~/airquality-project/03-ingesta-datos/sql/"
scp 03-ingesta-datos/requirements.txt "${EC2_TARGET}:~/airquality-project/03-ingesta-datos/"
ssh "$EC2_TARGET"
```

En EC2, reutilizar el entorno de Python y ejecutar el dry-run. Revisar el código de salida `0` y los conteos antes de aplicar SQL. El informe JSON contiene resultados mensuales y un resumen global. Los diagnósticos se emiten por la salida de error.

```bash
cd ~/airquality-project
test -x 03-ingesta-datos/.venv/bin/python || python3 -m venv 03-ingesta-datos/.venv
source 03-ingesta-datos/.venv/bin/activate
python -m pip install -r 03-ingesta-datos/requirements.txt
NOAA_DIR="$HOME/airquality-project/data/raw/noaa"
python 03-ingesta-datos/scripts/load_daily_weather.py --input-dir "$NOAA_DIR" \
  --dry-run --report 03-ingesta-datos/logs/noaa_dry_run.json
```

Usar la misma CA regional y endpoint DNS de la fase EPA. Una cuenta autorizada debe poder crear la tabla y la vista, reemplazar la vista en reejecuciones y leer las dos tablas. Aplicar exclusivamente el DDL NOAA.

```bash
read -r -p 'Endpoint DNS de airquality-mariadb: ' DB_HOST
read -r -p 'Usuario autorizado para crear tabla y vista NOAA: ' DB_ADMIN_USER
export DB_HOST DB_PORT=3306 DB_NAME=airquality
mkdir -p "$HOME/.cache/airquality"
export DB_SSL_CA="$HOME/.cache/airquality/us-east-1-bundle.pem"
test -s "$DB_SSL_CA" || curl -fsSL \
  https://truststore.pki.rds.amazonaws.com/us-east-1/us-east-1-bundle.pem -o "$DB_SSL_CA"
mysql --host="$DB_HOST" --port="$DB_PORT" --user="$DB_ADMIN_USER" --password \
  --ssl --ssl-ca="$DB_SSL_CA" --ssl-verify-server-cert --database="$DB_NAME" \
  < 03-ingesta-datos/sql/create_weather_schema.sql
```

La cuenta de carga requiere `INSERT`, `SELECT` y `UPDATE` sobre `daily_weather_by_county`. Si `DB_PASSWORD` no está definida, el cargador la solicita una vez sin eco. La cuenta de verificación requiere `SELECT` sobre las dos tablas y la vista, que usa los permisos del invocador.

```bash
read -r -p 'Usuario de carga NOAA: ' DB_USER
export DB_USER
python 03-ingesta-datos/scripts/load_daily_weather.py --input-dir "$NOAA_DIR" --batch-size 1000 \
  --report 03-ingesta-datos/logs/noaa_load.json

read -r -p 'Usuario de verificación de la integración: ' DB_REPORT_USER
mysql --host="$DB_HOST" --port="$DB_PORT" --user="$DB_REPORT_USER" --password \
  --ssl --ssl-ca="$DB_SSL_CA" --ssl-verify-server-cert --database="$DB_NAME" \
  < 03-ingesta-datos/sql/verify_weather_integration.sql
```

Con la tabla NOAA inicialmente vacía se esperan 1 137 162 inserciones. Al repetir los mismos doce archivos con todos los meses confirmados, se esperan `inserted=0` y `already_present=1137162`. Si un mes falla, revisar su entrada en `files`, corregir la causa y repetir la ejecución. Los meses ya confirmados se conservan.

## Fase 3. Ingesta automática a S3

El [script `ingest_to_s3.py`](scripts/ingest_to_s3.py) incorpora las tres fuentes a la zona `raw` del bucket existente `airquality-datalake-2024` en `us-east-1`. Cada fuente se puede ejecutar por separado con `--source rds`, `--source ec2` o `--source url`. `--source all` es el valor predeterminado e intenta las tres fuentes en la misma ejecución.

| Fuente | Lectura y validación | Clave S3 para 2024 |
| --- | --- | --- |
| RDS MariaDB | Transacción de solo lectura con TLS y cursor sin búfer. Exporta las diez columnas de `airquality.daily_aqi_by_county`, en el orden del esquema SQL, con códigos como texto y fechas ISO. Por defecto verifica 329 597 filas de 2024. | `raw/rds/epa/year=2024/daily_aqi_by_county_2024.csv` |
| EC2 NOAA | Verifica encabezados y cuenta registros de los doce CSV de `data/raw/noaa/`. Envía los bytes de origen, incluidas las filas de relleno. | `raw/ec2/noaa/year=2024/202401-scaled.csv` a `202412-scaled.csv` |
| URL Census Bureau | Descarga HTTPS, comprueba formato CSV, anchura de las filas y las columnas `STATE`, `COUNTY`, `SUMLEV` y `POPESTIMATE2024`. La validación admite UTF-8 y Windows-1252. El objeto mantiene la codificación y los bytes originales, junto con todos los niveles de resumen publicados. | `raw/url/census/year=2024/co-est2024-alldata.csv` |

La huella SHA-256 y el tamaño se comparan con el objeto existente. Si S3 no devuelve un SHA-256 completo, el script lee el objeto por bloques y calcula su huella. Un objeto idéntico se marca `skipped_identical`. Una clave con contenido distinto se marca `conflict` y se conserva. La escritura de objetos nuevos usa `If-None-Match: *` y un SHA-256 que S3 comprueba. Si otra ejecución publica durante la transferencia, se vuelve a comprobar la clave antes de decidir. Un `403` se reporta como error de permisos. [Escrituras condicionales en S3](https://docs.aws.amazon.com/AmazonS3/latest/userguide/conditional-writes.html) · [Boto3 `PutObject`](https://docs.aws.amazon.com/boto3/latest/reference/services/s3/client/put_object.html).

El manifiesto usa un identificador por ejecución. Se guarda localmente en `03-ingesta-datos/logs/ingest_to_s3/` y, en modo de ingesta, se intenta publicar el mismo JSON bajo `manifests/year=2024/`. Incluye origen, URI S3, filas, bytes, SHA-256, estado, error y tiempo por objeto. El resumen agrega los estados `validated`, `uploaded`, `skipped_identical`, `conflict` y `error`. Una falla en una fuente permite continuar con las demás. El comando devuelve `1` si hay conflictos, errores o una falla al publicar el manifiesto. Los manifiestos reflejan el tiempo hasta preparar el resumen de las transferencias.

`--dry-run` genera únicamente el manifiesto local. Valida las fuentes elegidas mediante lecturas locales, HTTPS público y, si se selecciona RDS, una transacción de solo lectura. La creación del cliente S3 y las verificaciones de llaves remotas corresponden al modo de ingesta. [Evidencia del dry-run local con datos oficiales](evidencias/ingest_s3_dry_run_local.json).

### Transferencia desde el repositorio local

Desde la raíz del proyecto, en Bash, usar la identidad SSH autorizada. El comando reutiliza `load_daily_aqi.py` y `load_daily_weather.py`, ya desplegados con las fases anteriores en EC2.

```bash
read -r -p 'DNS o alias SSH de airquality-ec2: ' EC2_HOST
EC2_TARGET="ec2-user@$EC2_HOST"
ssh "$EC2_TARGET" 'mkdir -p "$HOME/airquality-project/03-ingesta-datos/scripts"'
scp 03-ingesta-datos/scripts/ingest_to_s3.py "${EC2_TARGET}:~/airquality-project/03-ingesta-datos/scripts/"
scp 03-ingesta-datos/requirements.txt "${EC2_TARGET}:~/airquality-project/03-ingesta-datos/"
ssh "$EC2_TARGET"
```

### Ejecución en EC2

Boto3 usa la cadena predeterminada de credenciales y obtiene las credenciales temporales de `LabRole` en EC2. El rol necesita `s3:GetObject` para los objetos, `s3:ListBucket` para distinguir claves ausentes de respuestas `403` y `s3:PutObject` para los prefijos `raw/` y `manifests/` durante la carga. La cuenta de RDS necesita `SELECT` sobre `daily_aqi_by_county`. Se usa la misma CA y el mismo proceso de autenticación de la ingesta EPA. La versión de Boto3 está fijada a una que admite Python 3.9. [Cadena de credenciales de Boto3](https://boto3.amazonaws.com/v1/documentation/api/latest/guide/credentials.html) · [Boto3 1.42.97](https://pypi.org/project/boto3/1.42.97/).

```bash
cd ~/airquality-project
source .venv/bin/activate
python -m pip install -r 03-ingesta-datos/requirements.txt

read -r -p 'Endpoint DNS de MariaDB: ' DB_HOST
read -r -p 'Usuario de solo lectura de MariaDB: ' DB_USER
export DB_HOST DB_USER DB_PORT=3306 DB_NAME=airquality
export DB_SSL_CA="$HOME/.cache/airquality/us-east-1-bundle.pem"
export AWS_REGION=us-east-1 S3_BUCKET=airquality-datalake-2024 INGEST_YEAR=2024

python 03-ingesta-datos/scripts/ingest_to_s3.py --source all --dry-run
```

El dry-run debe validar 14 objetos, con 329 597 filas de RDS, 1 155 804 filas brutas de NOAA y 3 195 filas del CSV del Census Bureau. Para ejecutar la carga cuando se autorice la escritura, usar la misma sesión de EC2:

```bash
python 03-ingesta-datos/scripts/ingest_to_s3.py --source all
aws s3 ls "s3://$S3_BUCKET/manifests/year=$INGEST_YEAR/" --region "$AWS_REGION"
```

Los argumentos `--bucket`, `--region`, `--year`, `--noaa-dir`, `--census-url` y `--manifest-dir` tienen prioridad sobre `S3_BUCKET`, `AWS_REGION` o `AWS_DEFAULT_REGION`, `INGEST_YEAR`, `NOAA_DIR`, `CENSUS_URL` e `INGEST_MANIFEST_DIR`, respectivamente. Para otro año con fuente Census, indicar la URL HTTPS oficial de ese archivo. `--expected-rds-rows` permite ajustar la validación de conteo cuando se usa otra versión de origen. Las pruebas pueden ejecutarse desde la raíz sin credenciales AWS mediante `python -m unittest discover -s 03-ingesta-datos/tests -p 'test_ingest_to_s3.py' -v`. [Resultados de las pruebas simuladas](evidencias/ingest_s3_tests_local.json).
