# Módulo 2A. Preparación local de las fuentes de datos

El módulo reutiliza las copias oficiales validadas en el [módulo 1](../01-caso-estudio/README.md). Prepara el esquema y el cargador para RDS MariaDB, el CSV horario original para EC2 y la comprobación de la URL que se consumirá directamente desde Internet. El despliegue en AWS queda para el módulo 2B, sujeto a confirmación expresa.

## Resultados con datos reales

| Fuente | Resultado y evidencia |
| --- | --- |
| Estaciones | 20 994 registros leídos, 20 994 claves distintas, 0 claves repetidas, 0 filas idénticas adicionales y 0 filas con códigos mal formados según el formato AQS. Los 28 campos pasan la validación frente al esquema propuesto. [Perfil completo](evidencias/stations_profile.json), [modo de validación del cargador](evidencias/stations_validation.json). |
| Ozono horario | CSV original de **2 054 049 428 bytes** (1,91 GiB) extraído de la caché a `.cache/ec2-source/hourly_44201_2024.csv`. Se verificaron CRC y SHA-256, y se confirmó la reutilización del CSV en una segunda ejecución. [Reporte de preparación](evidencias/ec2_preparation.json). |
| AQI diario | HTTP 200 por `HEAD` y tipo `application/zip` al momento de la prueba. La comprobación conserva metadatos HTTP y la fuente operativa sigue siendo la URL pública de EPA AirData. [Reporte HTTP](evidencias/aqi_url_check.json). |

## Esquema de origen y validación

El [DDL MariaDB](sql/create_schema.sql) crea `air_quality_source.stations` en InnoDB con `utf8mb4`. Conserva los 28 campos en `snake_case`, con tipos `VARCHAR`, `DATE` y `DECIMAL`. La clave compuesta (`state_code`, `county_code`, `site_number`) usa `VARCHAR(2)`, `VARCHAR(3)` y `VARCHAR(4)` para preservar los ceros iniciales. Los otros campos admiten `NULL`. En la carga, solo las celdas vacías pasan a `NULL`; se conservan los espacios y los caracteres originales de los campos de texto. El ZIP sigue siendo la referencia para la representación textual exacta de fechas y números.

- El [perfil](evidencias/stations_profile.json) detalla por campo los nulos, el tamaño máximo, los valores incompatibles y, cuando corresponde, los intervalos observados y dígitos fraccionarios. Por ejemplo, hay 7 latitudes ausentes, 6 longitudes ausentes y 4 744 fechas de cierre ausentes. Los máximos de texto incluyen 119 caracteres en `address` y 91 en `owning_agency` y `tribe_name`.
- La fuente incluye cuatro filas con `state_code = 'CC'` y 42 con `state_code = '80'`, códigos de Canadá y México reconocidos por AQS. La comparación con la tabla vigente de AQS encontró cinco registros con códigos históricos de Alaska, `02-232` y `02-280`, confirmados por la Oficina del Censo de EE. UU. Se conservan ambos códigos y la cobertura completa del catálogo, incluidos los territorios. [Códigos AQS](https://aqs.epa.gov/aqsweb/helpfiles/state_code.htm), [referencia vigente de estados y condados](https://aqs.epa.gov/aqsweb/documents/codetables/states_and_counties.html), [cambios históricos del Censo](https://www.census.gov/programs-surveys/geography/technical-documentation/county-changes/2000.html).
- `Met Site Direction` contiene puntos cardinales como `NNE` y `SW`. El esquema físico conserva el valor como `met_site_direction VARCHAR(8)`. Esta observación corrige la interpretación numérica preliminar para este campo y deberá tenerse en cuenta al diseñar el ETL.

## Reproducción local

Ejecutar desde la raíz con Python 3.11 o posterior. En Linux, usar `python3`; en Windows, `py -3` si hay una instalación estándar. Las cuatro comprobaciones locales usan la biblioteca estándar. Solo `--execute` requiere PyMySQL.

```bash
python 02-fuentes-datos/scripts/profile_stations.py --report .cache/stations_profile_repeat.json
python 02-fuentes-datos/scripts/load_stations.py --validate-only --report .cache/stations_validation_repeat.json
python 02-fuentes-datos/scripts/prepare_ec2_dataset.py --output-dir .cache/ec2-source --report .cache/ec2_repeat.json
python 02-fuentes-datos/scripts/check_url_source.py --timeout 30 --report .cache/aqi_url_repeat.json
python -m unittest discover -s 02-fuentes-datos/tests -v
```

`--archive` cambia la ubicación del ZIP en los scripts de estaciones y ozono. `prepare_ec2_dataset.py` valida el miembro y el CRC, comprueba espacio libre antes de escribir y extrae en bloques. `--allow-download` permite recuperar el ZIP de la EPA únicamente si falta o está dañado. El destino debe estar en un volumen con espacio suficiente y quedar fuera del control de versiones. Un CSV existente se reutiliza si su tamaño y su SHA-256 coinciden. Si el archivo difiere, el comando conserva el destino y devuelve error.

La comprobación web usa `HEAD` y, si el servidor devuelve HTTP 405 o 501, prueba `GET` por rango y verifica la firma ZIP. Los errores de red y estados HTTP quedan en el resumen. En este entorno se usó la copia portátil de Python 3.13.17 del módulo 1 y se añadió el directorio local de scripts al `.pth` de esa copia aislada.

Para repetir el contraste de códigos geográficos, guarda la [tabla oficial vigente de AQS](https://aqs.epa.gov/aqsweb/documents/codetables/states_and_counties.html) en `.cache/epa-aqs-codes.html` y ejecuta `profile_stations.py` con `--aqs-codes .cache/epa-aqs-codes.html`. El reporte registra la huella de esa referencia y las combinaciones ausentes. Los códigos históricos se documentan y conservan tal como se publicaron en el ZIP.

## Carga prevista desde EC2 en 2B

1. Aplicar `sql/create_schema.sql` con una cuenta de administración autorizada en el servidor de destino.
2. Instalar la dependencia con `python3 -m pip install -r 02-fuentes-datos/requirements.txt` dentro de un entorno virtual. PyMySQL es un cliente Python para MariaDB que permite exigir TLS e identidad del servidor. [Paquete PyMySQL](https://pypi.org/project/PyMySQL/1.2.4/).
3. Proporcionar las variables documentadas en [.env.example](.env.example) mediante el entorno seguro de EC2. Usar el endpoint DNS y la CA de RDS en `MARIADB_SSL_CA`. El cargador verifica la cadena de confianza y el nombre del servidor y exige TLS 1.2 o posterior.
4. Ejecutar `python3 02-fuentes-datos/scripts/load_stations.py --execute --batch-size 500` con el ZIP verificado. El cargador valida todas las filas antes de abrir la conexión, comprueba que la tabla use InnoDB, envía lotes parametrizados y confirma una sola transacción. Las filas existentes idénticas se cuentan aparte. Una clave existente con contenido distinto provoca rollback de toda la carga, sin sobrescribir filas. Los errores de MariaDB muestran el código, sin mensajes del servidor que puedan incluir datos de conexión.

## Planes y evidencias

- [Arquitectura Mermaid](docs/arquitectura.md), [plan de despliegue 2B](docs/plan-despliegue.md) y [plan de costos y pausas](docs/plan-costos.md).
- [Evidencias y pruebas](evidencias/README.md). Git está inicializado en `main`, sin commits ni remotos. AWS CLI no se encontró en el equipo, por lo que región, permisos y sesión del laboratorio se comprobarán en 2B.
