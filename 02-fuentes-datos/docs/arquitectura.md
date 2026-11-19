# Arquitectura de las fuentes

```mermaid
flowchart LR
    ST["EPA · aqs_sites.zip"]
    OZ["EPA · hourly_44201_2024.zip"]
    AQ["EPA · URL diaria de AQI"]
    subgraph LOCAL["Módulo 2A · preparación local"]
        CACHE["Caché y verificación ZIP"]
        PROFILE["Perfil de estaciones y DDL MariaDB"]
        FILE["CSV horario original y SHA-256"]
        CHECK["Comprobación HTTP del AQI"]
        CACHE --> PROFILE
        CACHE --> FILE
    end
    subgraph VPC["Módulo 2B · misma VPC, previsto"]
        EC2["EC2 · Amazon Linux 2023 y EBS"]
        RDS["RDS MariaDB · subred privada"]
        EC2 -->|"TLS verificado · TCP 3306"| RDS
    end
    ST --> CACHE
    OZ --> CACHE
    AQ -->|"HEAD"| CHECK
    PROFILE -.-> EC2
    FILE -.-> EC2
    AQ -.-> M3["Lectura directa desde Internet · módulo 3"]
```

- **Estaciones.** Actividad local de perfil, validación completa y DDL. En 2B, EC2 ejecutará el cargador desde el ZIP oficial y escribirá el catálogo completo en `air_quality_source.stations` por TLS.
- **Ozono horario.** El archivo destinado a EC2 es `hourly_44201_2024.csv`. La preparación extrae los bytes del ZIP, registra el tamaño y calcula SHA-256. El volumen EBS mantendrá el archivo durante las pausas de la instancia.
- **AQI diario.** La URL de EPA AirData es la fuente operativa. El módulo 2A comprueba su disponibilidad y conserva metadatos HTTP. La descarga de referencia del módulo 1 sigue en la caché local. El consumidor del módulo 3 consultará la URL pública directamente.

Los enlaces punteados representan actividades futuras. Las opciones de red y permisos se detallan en el [plan de despliegue](plan-despliegue.md), y las decisiones de almacenamiento y ciclo de vida en el [plan de costos](plan-costos.md).
