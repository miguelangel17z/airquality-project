# Instrucciones permanentes del proyecto

## Contexto y objetivo

Este proyecto académico de Tópicos de Telemática de la Universidad EAFIT desarrolla una plataforma Big Data para analizar las concentraciones de ozono y su relación descriptiva con el Índice de Calidad del Aire (AQI) en Estados Unidos durante 2024. Conserva el origen y el significado de los datos ambientales a través de cada etapa.

## Fuentes y arquitectura

Mantén las fuentes públicas de EPA AirData y NOAA EpiNOAA para calidad del aire y meteorología. La ingesta a S3 también incorpora la fuente de población del Census Bureau indicada abajo. Conserva las URL, los nombres de origen y la trazabilidad de cada copia.

| Datos | Descarga oficial | Fuente prevista para la ingesta |
| --- | --- | --- |
| Estaciones de monitoreo | [aqs_sites.zip](https://aqs.epa.gov/aqsweb/airdata/aqs_sites.zip) | AWS RDS MariaDB |
| Mediciones horarias de ozono de 2024 (AQS `44201`) | [hourly_44201_2024.zip](https://aqs.epa.gov/aqsweb/airdata/hourly_44201_2024.zip) | Archivos en AWS EC2 |
| AQI diario de 2024 por condado | [daily_aqi_by_county_2024.zip](https://aqs.epa.gov/aqsweb/airdata/daily_aqi_by_county_2024.zip) | URL pública consultada durante la ingesta |
| Meteorología diaria de 2024 por condado | [EpiNOAA v1-0-0 · archivos `scaled`](https://noaa-nclimgrid-daily-pds.s3.amazonaws.com/index.html#EpiNOAA/v1-0-0/csv/cty/) | Archivos de EC2 y tabla MariaDB `daily_weather_by_county` |
| Estimaciones de población 2020–2024 por condado | [co-est2024-alldata.csv](https://www2.census.gov/programs-surveys/popest/datasets/2020-2024/counties/totals/co-est2024-alldata.csv) | URL pública y prefijo S3 `raw/url/census/year=2024/` |

Contrasta las fuentes EPA con el [catálogo AirData](https://aqs.epa.gov/aqsweb/airdata/download_files.html) y sus [formatos](https://aqs.epa.gov/aqsweb/airdata/FileFormats.html), y la meteorología con la [guía NOAA nClimGrid-Daily](https://www.ncei.noaa.gov/pub/data/daily-grids/v1-0-0/nclimgrid-daily_v1-0-0_user-guide.pdf). Usa datos sintéticos únicamente en pruebas.

El flujo objetivo parte de RDS MariaDB, EC2 y la URL pública. La ingesta automatizada escribe en Amazon S3 Bronze. AWS Glue ejecuta ETL con Apache Spark y escribe Parquet en S3 Silver. AWS Glue Data Catalog comparte las tablas con Athena y Amazon EMR, donde se usa Apache Hive y Spark. Athena, Hive y SparkSQL ejecutan las consultas SQL, y PySpark produce métricas descriptivas para S3 Gold. AWS Lambda y API Gateway exponen los resultados que consume Streamlit. Mantén alineada la implementación autorizada con la [arquitectura conceptual](01-caso-estudio/arquitectura.md).

## Módulos y autorización

1. Definición del caso de estudio y validación de fuentes (`01-caso-estudio/`).
2. Configuración de fuentes de datos.
3. Ingesta automática hacia AWS S3.
4. Procesamiento ETL con AWS Glue y Spark.
5. Catalogación con Glue y EMR/Hive.
6. Analítica con Athena, Hive y SparkSQL.
7. Analítica descriptiva con PySpark.
8. Visualización con Streamlit y exposición mediante API Gateway.

Trabaja exclusivamente en el módulo que el usuario haya autorizado. Al finalizar un módulo, entrega el estado y las evidencias y espera la confirmación expresa del usuario antes de crear, configurar o implementar el siguiente. Usa un directorio numerado por módulo con documentación y, cuando corresponda, código, instrucciones de reproducción y evidencias.

## Convenciones y reproducibilidad

- Redacta la documentación en español. Usa nombres de variables, funciones, tablas y columnas en inglés y `snake_case`. Conserva los encabezados originales en los datos crudos y documenta su mapeo a columnas lógicas.
- Mantén la solución simple, evita dependencias innecesarias y utiliza rutas relativas o parametrizables. Documenta requisitos, versiones efectivamente ejecutadas, comandos, parámetros, entradas y resultados esperados.
- Conserva los identificadores geográficos como texto y distingue los sitios de los monitores por parámetro y POC. Documenta granularidad, claves, unidades, calendario, calidad, cobertura, denominadores y tratamiento de conflictos antes de agregar o cruzar datos.
- Conserva URL, fecha de obtención, tamaño y SHA-256 de las versiones utilizadas. Separa las cifras publicadas por la EPA de las mediciones verificadas en el proyecto. Documenta los errores y las condiciones necesarias para repetir los resultados.
- Conserva las evidencias académicas de cada módulo en su directorio `evidencias/`. Incluye resultados verificables y, cuando corresponda, capturas, consultas y bitácoras depuradas. Excluye rutas personales, credenciales, secretos y archivos masivos.
- Mantén los archivos descargados y los entornos locales fuera de Git. Preserva cambios existentes ajenos al objetivo. Los commits y los pushes requieren una instrucción expresa del usuario.

## AWS y costos

El módulo 1 es local. La creación y configuración de recursos de AWS, así como las operaciones que puedan generar costos, requieren autorización expresa del usuario en el módulo correspondiente. Antes de una operación autorizada con costo potencial, documenta sus recursos, supuestos de costo y procedimiento de apagado o eliminación. Favorece verificaciones locales y ejecuciones acotadas. Mantén las credenciales fuera del código, evidencia y repositorio. No solicites ni almacenes credenciales del usuario.
