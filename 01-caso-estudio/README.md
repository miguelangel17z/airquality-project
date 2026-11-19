# Módulo 1. Definición del caso de estudio y validación de fuentes

## Planteamiento del problema

Determinar dónde y cuándo se registran concentraciones altas de ozono en Estados Unidos exige combinar mediciones horarias de distintos instrumentos y ubicaciones. El AQI se publica en otra granularidad y resume la calidad del aire diaria por condado. Una unión directa entre mediciones horarias y AQI repetiría valores, y un promedio de observaciones sin controlar cobertura daría más peso a los sitios que reportan con mayor frecuencia.

## Justificación

El ozono troposférico tiene relevancia para la salud ambiental. AirData aporta mediciones públicas, metadatos de monitoreo e índices diarios con distintas granularidades. El volumen publicado de cerca de nueve millones de mediciones horarias en un año hace pertinente estudiar un flujo de procesamiento distribuido y reproducible. [EPA sobre el ozono troposférico](https://www.epa.gov/ozone-pollution-and-your-patients-health/what-ozone), [catálogo de EPA AirData](https://aqs.epa.gov/aqsweb/airdata/download_files.html).

## Objetivo general

Construir una plataforma Big Data para estudiar la concentración de ozono y su asociación descriptiva con el Índice de Calidad del Aire (AQI) en Estados Unidos durante 2024 a partir de datos públicos de la EPA.

## Objetivos específicos

1. Verificar la disponibilidad, estructura y procedencia de los tres archivos oficiales.
2. Formular seis preguntas analíticas con campos, métricas, cobertura y límites de interpretación.
3. Definir las tablas lógicas, las claves y el tratamiento de unidades, tiempo, calidad y granularidad.
4. Documentar el flujo conceptual de la plataforma y dejar instrucciones y evidencias de una validación reproducible.

## Alcance

El caso de estudio abarca el año calendario 2024 en los 50 estados y el Distrito de Columbia, según la disponibilidad de observaciones en la red de monitoreo. El catálogo de estaciones sirve de referencia y puede incluir otros años y jurisdicciones. En esta etapa se consulta la documentación oficial y se verifican localmente ZIP, encabezados y muestras. Los análisis masivos, las respuestas a P1–P6 y la configuración de AWS corresponden a los módulos posteriores.

## Fuentes y documentos

| Documento | Contenido |
| --- | --- |
| [Fuentes de datos](datasets.md) | Descargas oficiales de estaciones AQS, ozono horario `44201` y AQI diario por condado. Procedencia, conteos publicados y resultados observados. |
| [Preguntas de negocio](preguntas-negocio.md) | P1–P6 con objetivos, hipótesis, campos, métricas, visualizaciones, utilidad y limitaciones. |
| [Diccionario de datos](diccionario-datos.md) | Encabezados reales, tipos, granularidad, claves, diagrama entidad-relación y reglas para el cruce condado-día. |
| [Arquitectura conceptual](arquitectura.md) | Capas Bronze, Silver y Gold y responsabilidades de cada servicio futuro. |
| [Evidencias](evidencias/README.md) | Evidencia generada, lista de capturas para el informe y verificaciones ejecutadas. |

## Validación reproducible

Se requiere Python 3.11 o posterior. El script usa únicamente la biblioteca estándar. Para una descarga inicial se necesita acceso HTTPS a `aqs.epa.gov` y unos 75 MB libres para los ZIP. Ejecutar desde la raíz del proyecto.

**Windows**

```powershell
py -3 .\01-caso-estudio\scripts\validate_sources.py
```

**Linux**

```bash
python3 01-caso-estudio/scripts/validate_sources.py
```

La ejecución normal descarga mediante GET si falta un ZIP o su caché falla la validación. Si la caché pasa, reutiliza el ZIP y verifica en línea la URL mediante HEAD. Cuando HEAD no está soportado intenta GET con un rango de un byte. La descarga se guarda temporalmente y se incorpora a la caché después de verificarla. La integridad CRC se comprueba sobre el ZIP completo. Solo se interpretan como CSV el encabezado y la muestra de 25 filas por archivo. Los ZIP se guardan en `.cache/epa-airdata/` y el reporte en `01-caso-estudio/evidencias/validation_report.json`.

Parámetros para repetir la validación:

- `--sample-size 25` establece el máximo de filas de muestra por CSV. Admite valores de 1 a 1000.
- `--timeout 60` aumenta el tiempo de espera de operaciones de red, cuyo valor predeterminado es de 30 segundos.
- `--cache-dir .cache/epa-airdata` selecciona el directorio local de los ZIP.
- `--report .cache/validation_repeat.json` guarda el resultado en otra ruta para conservar la evidencia inicial.
- `--offline` valida una copia local sin consultar las URLs. `--refresh` solicita una copia nueva de cada ZIP. La comprobación HEAD informa disponibilidad, mientras el SHA-256 identifica los bytes usados en la validación.

El código de salida `0` indica éxito de las tres fuentes para el modo elegido. El código `1` señala un fallo y el reporte identifica la etapa afectada por fuente cuando puede escribirse. El código `2` indica argumentos no válidos. Si falla la red, corregir el acceso HTTPS y repetir con el mismo comando. Una copia local íntegra permite repetir la inspección con `--offline`.

## Criterios de aceptación

| Criterio | Estado |
| --- | --- |
| Caso de estudio, objetivos, alcance y seis preguntas definidos | Cumplido. [Preguntas](preguntas-negocio.md). |
| Tres fuentes originales identificadas, accesibles y validadas con ZIP y muestra | Cumplido. HTTP 200, CRC correcto y 25 filas de muestra por CSV. [Reporte](evidencias/validation_report.json). |
| Encabezados reales, granularidad, tipos, claves y diagrama entidad-relación documentados | Cumplido. [Diccionario](diccionario-datos.md). |
| Reglas de cobertura, calidad, unidades, calendario y cruce entre granularidades definidas | Cumplido como diseño preliminar. [Diccionario](diccionario-datos.md), [preguntas](preguntas-negocio.md). |
| Arquitectura conceptual documentada sin despliegue y etapas futuras identificadas | Cumplido. [Arquitectura](arquitectura.md), [módulos](../README.md). |
| Código reproducible, evidencia local y reglas permanentes de trabajo disponibles | Cumplido. [Validador](scripts/validate_sources.py), [evidencias](evidencias/README.md), [AGENTS.md](../AGENTS.md). |

El paso al módulo 2 requiere confirmación expresa del usuario.
