# Plan para el módulo 2B

La preparación local se completó en 2A. Este procedimiento se ejecutará cuando se autorice el módulo 2B y se comprueben las restricciones del laboratorio.

## Prevalidación del laboratorio

1. Identificar la región habilitada, presupuesto, permisos, VPC y subredes disponibles. En la consola o mediante consultas de descripción, verificar AMI de Amazon Linux 2023, tipo EC2, clases y versiones de MariaDB ordenables, mínimos de almacenamiento y cuotas. Una VPC compartida puede requerir permisos adicionales.
2. Proponer **EC2 Amazon Linux 2023 x86_64 `t3.micro`**, o el tipo de menor costo compatible que el laboratorio permita. Elegir un volumen `gp3` si está permitido. Verificar después de instalar el sistema y dependencias que el volumen de datos tenga espacio libre para el ZIP, el CSV y un margen operativo. Como referencia, el CSV solo ocupa 2 054 049 428 bytes. Revisar el espacio nuevamente antes de extraer.
3. Proponer **RDS MariaDB Single-AZ**, con una clase pequeña como `db.t4g.micro` si el laboratorio la admite, y almacenamiento general en el mínimo compatible con la clase, motor y región elegidos. Confirmar disponibilidad antes de lanzar. Crear o reutilizar un grupo de subredes de BD en al menos dos zonas de disponibilidad dentro de la misma VPC, aunque la instancia sea Single-AZ. Seleccionar acceso privado y preservar el endpoint DNS para la verificación TLS. [RDS en una VPC](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/USER_VPC.WorkingWithRDSInstanceinaVPC.html).

## Red y acceso previsto

- Usar la misma VPC para EC2 y RDS. Para EC2, reutilizar conectividad saliente autorizada por el laboratorio. Si la alternativa disponible es una subred pública con Internet Gateway, EC2 necesitará una dirección pública admitida y salida HTTPS para EPA AirData y los repositorios de software. RDS se ubicará en la red privada.
- Dar acceso administrativo por Session Manager si el laboratorio ya dispone de rol, agente y conectividad. Como alternativa, autorizar SSH solamente desde la IP administrativa mediante `/32`. El security group de RDS permitirá TCP `3306` únicamente desde el security group de EC2. [Session Manager](https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-prerequisites.html), [red de RDS](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/USER_VPC.WorkingWithRDSInstanceinaVPC.html).
- Mantener los certificados públicos de CA de RDS en una ruta local configurable en EC2 y conectar usando el nombre DNS del endpoint. La cuenta de carga necesitará `INSERT`, `SELECT` y `UPDATE` sobre `air_quality_source.stations` para ejecutar la sentencia idempotente. El administrador aplicará el DDL por separado. Usar `REQUIRE SSL` para la cuenta y habilitar el transporte seguro en el servidor si lo permite la parametrización del laboratorio. [TLS en RDS](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/UsingWithRDS.SSL.html).

## Ejecución y verificación tras la autorización

1. Crear o seleccionar la conectividad y los security groups autorizados. Iniciar EC2 y RDS y comprobar estados `running` y `available`.
2. Preparar Python 3.11 o posterior, el cliente MariaDB y el directorio de datos en EC2. Obtener o transferir los dos ZIP oficiales de estaciones y ozono. Comparar SHA-256 con las evidencias de 2A o documentar la nueva versión de EPA. Usar `prepare_ec2_dataset.py` para verificar y extraer el CSV original y comprobar su tamaño y huella.
3. Aplicar `sql/create_schema.sql` con una cuenta de administración y TLS con verificación de servidor. Comprobar `SHOW CREATE TABLE air_quality_source.stations` e InnoDB. Configurar la cuenta operativa con el privilegio mínimo y obtener sus valores mediante el mecanismo autorizado por el laboratorio.
4. Ejecutar `load_stations.py --validate-only` y luego `--execute` desde EC2. Contrastar el resumen con `SELECT COUNT(*) FROM air_quality_source.stations` en una base nueva. Repetir el mismo ZIP y comprobar `inserted=0`. Si cambia el catálogo y el mismo identificador ya tiene otros valores, resolver la diferencia como una nueva decisión de versionado.
5. Guardar evidencias depuradas de conectividad TLS, resultados SQL, huella del CSV y estados de recursos. Mantener el AQI como URL pública y aplicar el [procedimiento de pausa](plan-costos.md) al finalizar la práctica.
