# Evidencias del módulo 2A

Las pruebas se realizaron el 18 de noviembre de 2026, hora de Bogotá, con Python 3.13.17 en Windows. Los JSON usan marcas de tiempo UTC y contienen conteos, tamaños, huellas y metadatos públicos. Los archivos masivos y las herramientas locales permanecen en `.cache/`.

| Evidencia | Resultado y alcance |
| --- | --- |
| [stations_profile.json](stations_profile.json) | Perfil de las 20 994 filas reales, por campo y clave. Incluye la huella del ZIP original y el contraste con la tabla actual de códigos AQS, con cinco registros bajo códigos históricos de Alaska. |
| [stations_validation.json](stations_validation.json) | Validación del tipo y capacidad del esquema para las 20 994 filas, sin conexión a MariaDB. `inserted=0` refleja el modo de validación. |
| [mariadb_local_test.json](mariadb_local_test.json) | DDL aplicado en MariaDB 11.4.10 aislado en `localhost`. Con dos filas ficticias de prueba, se verificaron inserción, recarga sin duplicados, rollback ante conflicto y rechazo de un nombre TLS incorrecto. La instancia temporal se cerró al concluir. |
| [ec2_preparation.json](ec2_preparation.json) | ZIP íntegro y CSV original de 2 054 049 428 bytes. La segunda pasada reutilizó el CSV existente con la misma huella. |
| [aqi_url_check.json](aqi_url_check.json) | La URL de EPA devolvió HTTP 200 con `Content-Type: application/zip`. La prueba utilizó `HEAD`. |
| [Pruebas unitarias](../tests/test_sources.py) | 14 pruebas aprobadas sobre contrato entre DDL y cargador, fechas y precisión, códigos, duplicados, InnoDB, rollback, TLS, extracción y reutilización, falta de espacio, ZIP inválido, rango HTTP y timeout. |
| Git | Rama local `main`, sin commits ni remotos. Se probó `.gitignore` con datos completos, archivos de entorno, claves privadas y credenciales, y se confirmó que `.env.example` queda visible. |

Para el informe académico, conservar una captura del conteo y unicidad de estaciones, el resultado de `--validate-only`, el tamaño y SHA-256 del CSV preparado, el estado HTTP del AQI y el resultado de las pruebas. En 2B, anexar evidencia depurada del estado de EC2 y RDS, la identidad TLS, `SHOW CREATE TABLE`, el conteo SQL y la recarga idempotente desde EC2. Ocultar parámetros sensibles, identificadores de cuenta y rutas personales antes de compartir capturas.
