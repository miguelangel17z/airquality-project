# Plataforma Big Data para el análisis de la calidad del aire y contaminación por ozono en Estados Unidos durante 2024

Proyecto académico de Tópicos de Telemática, Universidad EAFIT. Su objetivo es construir una plataforma reproducible para estudiar la concentración de ozono y su asociación con el Índice de Calidad del Aire (AQI) mediante datos públicos de EPA AirData.
flowchart LR
    classDef src fill:#e3f2fd,stroke:#1565c0,stroke-width:2px,color:#0d47a1
    classDef ing fill:#e0f7fa,stroke:#00838f,stroke-width:2px,color:#006064
    classDef lake fill:#fff3e0,stroke:#ef6c00,stroke-width:2px,color:#e65100
    classDef proc fill:#e8f5e9,stroke:#2e7d32,stroke-width:2px,color:#1b5e20
    classDef serv fill:#f3e5f5,stroke:#6a1b9a,stroke-width:2px,color:#4a148c

    subgraph G1 ["1. Fuentes"]
        direction TB
        RDS[("RDS MariaDB<br/>EPA AQI")]:::src
        EC2F["EC2 Local<br/>12 CSV NOAA"]:::src
        URL["HTTPS<br/>Census 2024"]:::src
    end

    subgraph G2 ["2. Ingesta"]
        ING["EC2 Python<br/>ingest_to_s3.py"]:::ing
    end

    subgraph G3 ["3. Data Lake (Amazon S3)"]
        direction TB
        RAW["Zona RAW<br/>14 CSV + Manifiesto"]:::lake
        TRU["Zona TRUSTED<br/>Parquet Particionado"]:::lake
        REF["Zona REFINED<br/>Parquet y JSON"]:::lake
    end

    subgraph G4 ["4. Procesamiento y Analítica"]
        direction TB
        GLUE["AWS Glue 4.0<br/>ETL PySpark"]:::proc
        CAT["Glue Crawler y<br/>Data Catalog"]:::proc
        ATH["Amazon Athena<br/>SQL"]:::proc
        EMR["Amazon EMR<br/>Spark + Jupyter"]:::proc
    end

    subgraph G5 ["5. Consumo"]
        direction TB
        API["Lambda +<br/>API Gateway"]:::serv
        STR["Streamlit<br/>Dashboard"]:::serv
    end

    RDS & EC2F & URL --> ING
    ING --> RAW
    RAW --> GLUE --> TRU
    TRU --> CAT
    CAT --> ATH
    CAT --> EMR --> REF
    REF --> API --> STR

## Módulos

| Módulo | Alcance | Estado |
| --- | --- | --- |
| 1. Definición del caso de estudio | Preguntas de negocio, validación de fuentes, modelo de datos y arquitectura conceptual. | Aprobado |
| 2. Configuración de fuentes de datos | Preparar las estaciones para RDS MariaDB, las mediciones horarias para EC2 y la URL pública del AQI diario. | [2A local completado](02-fuentes-datos/README.md). EC2 y RDS aprovisionados y con conectividad validada según el contexto del proyecto. |
| 3. Ingesta de datos | Cargar AQI y meteorología NOAA en MariaDB e ingerir AQI, NOAA y Census a S3 Raw. | [Ingesta implementada](03-ingesta-datos/README.md#fase-3-ingesta-automática-a-s3) y completada en S3 según el estado confirmado del proyecto. |
| 4. Procesamiento de datos | Validar e integrar las tres fuentes Raw con PySpark y publicarlas como cuatro datasets Parquet en Trusted. | [ETL Raw a Trusted](04-procesamiento-datos/docs/etl_raw_to_trusted.md) implementado para Spark 3.5.4 y Glue 5.0. Ejecución en Glue pendiente. |
| 5. Catalogación con Glue y EMR/Hive | Registrar tablas externas y sus particiones en Glue Data Catalog y habilitar su lectura desde EMR/Hive. | Pendiente |
| 6. Analítica con Athena, Hive y SparkSQL | Resolver consultas analíticas y comparar los resultados de los motores SQL. | Pendiente |
| 7. Analítica descriptiva con PySpark | Calcular métricas, cobertura y asociaciones descriptivas para la capa Gold. | Pendiente |
| 8. Visualización con Streamlit y exposición mediante API Gateway | Publicar resultados agregados a través de Lambda y API Gateway y visualizarlos con Streamlit. | Pendiente |

Los README del [módulo 1](01-caso-estudio/README.md), el [módulo 2A](02-fuentes-datos/README.md), las [fases de ingesta EPA y NOAA](03-ingesta-datos/README.md) y la [extracción del dataset integrado](04-procesamiento-datos/README.md) reúnen los comandos y evidencias. Las [instrucciones del proyecto](AGENTS.md) rigen el trabajo incremental.
