# Fuentes de datos EPA AirData

EPA AirData publica archivos derivados de Air Quality System (AQS) como CSV con encabezado, comprimidos en ZIP. Este proyecto usa exclusivamente las tres descargas siguientes como datos analíticos. El catálogo se consultó el 18 de noviembre de 2026 y mostraba una fecha de actualización del 25 de junio de 2026. [Catálogo oficial](https://aqs.epa.gov/aqsweb/airdata/download_files.html), [documentación de formatos](https://aqs.epa.gov/aqsweb/airdata/FileFormats.html).

| Fuente oficial | Contenido | Filas publicadas en el catálogo | Destino futuro |
| --- | --- | ---: | --- |
| [aqs_sites.zip](https://aqs.epa.gov/aqsweb/airdata/aqs_sites.zip) | Catálogo geográfico y administrativo de sitios AQS. Es una versión de referencia que abarca distintos períodos y parámetros. | 20 994 | AWS RDS MariaDB |
| [hourly_44201_2024.zip](https://aqs.epa.gov/aqsweb/airdata/hourly_44201_2024.zip) | Observaciones horarias de ozono en 2024 por monitor, identificado mediante sitio, parámetro `44201` y POC. | 8 995 978 | Archivos en AWS EC2 |
| [daily_aqi_by_county_2024.zip](https://aqs.epa.gov/aqsweb/airdata/daily_aqi_by_county_2024.zip) | AQI diario de 2024 por condado. Incluye categoría, contaminante y sitio que definieron el índice. | 329 597 | URL pública consultada por la ingesta automática |

## Resultados observados

| CSV identificado | Respuesta de descarga | Tamaño del ZIP observado (bytes) | Columnas | Filas leídas en la muestra |
| --- | --- | ---: | ---: | ---: |
| `aqs_sites.csv` | HTTP 200 | 1 018 314 | 28 | 25 |
| `hourly_44201_2024.csv` | HTTP 200 | 68 510 135 | 24 | 25 |
| `daily_aqi_by_county_2024.csv` | HTTP 200 | 1 625 395 | 10 | 25 |

Los tres ZIP pasaron el control de integridad CRC. El [reporte JSON](evidencias/validation_report.json) conserva los encabezados exactos, tamaños, SHA-256 y metadatos HTTP. Los encabezados están mapeados al modelo en el [diccionario de datos](diccionario-datos.md). El servidor devolvió `Last-Modified` del 13 de julio de 2026 para las tres descargas. Las cifras de la primera tabla corresponden al catálogo consultado. El conteo total verificado queda como `null` en el reporte porque la validación solo interpreta una muestra de registros.

- En estaciones se observaron códigos con ceros iniciales, entre ellos `01`, `001` y `0001`. El catálogo se utilizará como referencia y las mediciones permitirán comprobar actividad de los monitores en 2024.
- En ozono se observaron `Parameter Code` igual a `44201` y `Units of Measure` igual a `Parts per million` (ppm) en las 25 filas de la muestra. La distribución de unidades y la cobertura completa se verificarán en el ETL.
- En AQI se observó el encabezado literal `county Name` y `PM2.5` como parámetro definitorio en la muestra inicial. La EPA calcula el AQI del condado a partir del valor máximo disponible de los monitores y contaminantes aplicables para ese día. El indicador puede responder a ozono y a otros contaminantes. [EPA sobre el AQI por condado](https://aqs.epa.gov/aqsweb/documents/aqi/aqi_data.html), [EPA sobre los informes AirData](https://www.epa.gov/outdoor-air-quality-data/about-airdata-reports).

La muestra corresponde al inicio de cada CSV y depende de su orden. Sirve para confirmar el formato y observar ejemplos. La representatividad geográfica, la unicidad de claves y la cobertura temporal se evaluarán al procesar el conjunto completo. Si la EPA actualiza los archivos, comparar los SHA-256 de las nuevas descargas para identificar qué versión se está usando.
