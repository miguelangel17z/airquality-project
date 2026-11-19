# Evidencias del módulo 1

## Evidencia generada

El [reporte de validación](validation_report.json) registra la primera ejecución exitosa, realizada el 18 de noviembre de 2026 en hora de Bogotá, con Python 3.13.17 en Windows. Sus marcas de tiempo usan UTC. Registra la URL pública, código HTTP, tamaño, SHA-256, CRC, encabezados reales y observaciones de la muestra. Contiene metadatos y valores públicos acotados, con rutas de almacenamiento excluidas. Los archivos completos se conservan en la caché local excluida de Git.

## Verificaciones ejecutadas

| Prueba | Resultado |
| --- | --- |
| Primera ejecución con caché vacía | Código `0`. Las tres descargas GET obtuvieron HTTP 200. Los ZIP pasaron CRC y se leyeron 25 filas de cada CSV. |
| Segunda ejecución en línea | Código `0`. Las tres consultas HEAD obtuvieron HTTP 200 y las copias reutilizadas mantuvieron los mismos SHA-256. |
| Validación local con `--offline --sample-size 1` | Código `0`. Se comprobó una fila por CSV y se omitieron las consultas HTTP. |
| Copia de prueba truncada de `aqs_sites.zip` | El validador detectó el ZIP inválido, descargó un original válido y recuperó su SHA-256. Los otros dos ZIP se reutilizaron. |
| Caché ausente o con contenido alterado, y red restringida | Código `1`, con errores por fuente. Se comprobó que el reporte no contiene rutas locales y que los archivos parciales de descarga se retiran. |
| Sintaxis y argumentos | El código compiló en Python 3.13.17 y pasó el análisis de sintaxis para Python 3.11. Los tamaños de muestra fuera de rango y tiempos de espera no válidos devolvieron código `2`. |
| Coherencia documental | Los 62 encabezados del diccionario coincidieron en grafía y orden con el JSON, y se comprobaron los campos requeridos por P1–P6 y los enlaces internos. |

Una lectura adicional de 25 filas de ozono y 100 de AQI encontró las claves de condado `01-003` para el 1 y 2 de marzo de 2024 en ambos archivos. También mostró casos en que la fecha local y la GMT difieren. La prueba verifica la disponibilidad de los campos de enlace y deja la unicidad y el emparejamiento completo para el ETL.

## Capturas para el informe académico

1. Catálogo público de AirData con `Site Listing`, la fila de ozono horario de 2024 y la fila de AQI diario por condado de 2024. Incluir la fecha de consulta y los conteos publicados.
2. Terminal mostrando el comando, el estado de las tres fuentes y el nombre del reporte. Recortar el prompt si revela rutas personales.
3. Fragmento del JSON con códigos HTTP, CRC, encabezados, 25 filas muestreadas, unidad de ozono y SHA-256. Identificar `verified_total_rows: null` para distinguir la muestra de un conteo completo.
4. Diagramas Mermaid del modelo lógico y de la arquitectura conceptual, renderizados con sus nombres y cardinalidades visibles. [Modelo de datos](../diccionario-datos.md), [arquitectura](../arquitectura.md).

Si una validación posterior falla, guardar el fragmento del JSON con el estado y la etapa, y registrar el comando y el resultado del reintento. Usar `--report` con una ruta diferente para conservar la evidencia original. Excluir credenciales, datos masivos y rutas personales de las capturas y los resultados compartidos.
