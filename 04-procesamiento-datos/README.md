# Extracción del dataset integrado EPA–NOAA

El [requisito 4. ETL Raw a Trusted](docs/etl_raw_to_trusted.md) documenta el procesamiento de las tres fuentes Raw, la reproducción local y el despliegue en Glue 5.0. Esta página conserva las instrucciones de la extracción anterior de MariaDB.

Esta fase exporta la vista `airquality.v_aqi_weather_daily` para su posterior procesamiento. En RDS se confirmaron 323 540 registros integrados entre el 1 de enero y el 31 de diciembre de 2024, con 98,16 % de cobertura sobre EPA. La implementación se verificó en MariaDB local. La extracción en EC2 contra el RDS real queda pendiente.

## Contrato de salida

- `data/processed/aqi_weather_2024.csv` conserva las 14 columnas de la vista, con los códigos de estado y condado como texto, las fechas en ISO `YYYY-MM-DD`, los valores meteorológicos como decimales y las columnas y categorías de AQI. El CSV es UTF-8. Al importarlo en otra herramienta, declarar ambos códigos como cadenas para conservar sus ceros iniciales.
- `data/processed/aqi_weather_2024.manifest.json` contiene el conteo, los encabezados, los extremos del rango de fechas, el tamaño en bytes, SHA-256, tiempo de ejecución y hora UTC de exportación. El CSV y su manifiesto operativo quedan excluidos de Git.
- El [exportador](scripts/export_integrated_dataset.py) inicia una transacción de solo lectura y selecciona las fechas de 2024. `SSCursor` lee en lotes de 1 000 filas por defecto. Ordenar por estado, condado y fecha permite detectar duplicados comparando cada clave con la anterior, incluso entre lotes. Se rechazan nulos, cadenas vacías o en blanco y valores incompatibles con los tipos esperados. [Cursor sin búfer de PyMySQL](https://pymysql.readthedocs.io/en/latest/modules/cursors.html) · [Transacciones de solo lectura de MariaDB](https://mariadb.com/docs/server/reference/sql-statements/transactions/start-transaction).
- Primero se prepara el CSV y se calcula su huella en una ubicación temporal dentro del directorio de destino. Tras validar el conteo y generar el manifiesto se publican los archivos mediante enlaces duros locales. La presencia del manifiesto y su SHA-256 identifican un par completo. El proceso conserva una exportación previa. Para repetirlo, `--output` permite elegir otro nombre `.csv`; su manifiesto se ubica junto al CSV. El volumen de destino debe permitir enlaces duros.

## Pruebas locales

Desde la raíz del proyecto, con Python 3.9 o posterior:

```bash
python3 -m unittest discover -s 04-procesamiento-datos/tests -v
```

La prueba MariaDB se habilita con `AIRQUALITY_TEST_DB_PORT` y se conecta únicamente a `localhost`. Para ejecutarla, definir también `DB_HOST=localhost`, `DB_USER` de una instancia aislada, `DB_SSL_CA` de prueba y `DB_PASSWORD` en el entorno o ingresar la contraseña sin eco. La prueba usa un esquema y una cuenta temporal con permiso `SELECT`. [Resultados de las pruebas](evidencias/tests_local.json).

El CSV completo se verificó mediante una vista reconstruida en MariaDB local con las copias oficiales de EPA y NOAA. La evidencia identifica esa procedencia y documenta la verificación independiente del conteo y SHA-256 del archivo publicado. [Exportación local con fuentes oficiales](evidencias/export_local_official.json).

## Transferencia y ejecución en EC2

Desde la raíz local, en Bash, con la identidad SSH autorizada para la instancia:

```bash
read -r -p 'DNS o alias SSH de airquality-ec2: ' EC2_HOST
EC2_TARGET="ec2-user@$EC2_HOST"
ssh "$EC2_TARGET" 'mkdir -p "$HOME/airquality-project/04-procesamiento-datos/scripts"'
scp 04-procesamiento-datos/scripts/export_integrated_dataset.py \
  "${EC2_TARGET}:~/airquality-project/04-procesamiento-datos/scripts/"
ssh "$EC2_TARGET"
```

El exportador importa `03-ingesta-datos/scripts/load_daily_aqi.py` y reutiliza `03-ingesta-datos/requirements.txt`, disponibles en la fase de ingesta desplegada. Si es necesario restaurar esos dos archivos, copiarlos desde la raíz local con los siguientes comandos antes de abrir la sesión SSH:

```bash
scp 03-ingesta-datos/scripts/load_daily_aqi.py \
  "${EC2_TARGET}:~/airquality-project/03-ingesta-datos/scripts/"
scp 03-ingesta-datos/requirements.txt "${EC2_TARGET}:~/airquality-project/03-ingesta-datos/"
```

En EC2, reutilizar la CA verificada de la [fase de ingesta](../03-ingesta-datos/README.md#transferencia-y-ejecución-posterior-en-ec2). Usar el endpoint DNS de RDS y una cuenta con `SELECT` sobre la vista y sus dos tablas, según los permisos del invocador de la vista. `DB_PASSWORD` se puede inyectar al proceso. Si está ausente y se usa un terminal interactivo, se solicita sin eco.

```bash
cd ~/airquality-project
source 03-ingesta-datos/.venv/bin/activate
python -m pip install -r 03-ingesta-datos/requirements.txt
read -r -p 'Endpoint DNS de airquality-mariadb: ' DB_HOST
read -r -p 'Usuario de lectura para el dataset integrado: ' DB_USER
export DB_HOST DB_USER DB_PORT=3306 DB_NAME=airquality
export DB_SSL_CA="$HOME/.cache/airquality/us-east-1-bundle.pem"

python 04-procesamiento-datos/scripts/export_integrated_dataset.py --batch-size 1000 --expected-rows 323540
```

Comprobar el archivo contra el manifiesto desde la misma sesión:

```bash
DIGEST=$(python -c 'import json; print(json.load(open("data/processed/aqi_weather_2024.manifest.json"))["sha256"])')
printf '%s  %s\n' "$DIGEST" 'data/processed/aqi_weather_2024.csv' | sha256sum -c -
```
