# Plan preliminar de costos

La estimación se completará con la región, disponibilidad, cuotas y duración autorizadas por el laboratorio. Consultar las tarifas de AWS para las opciones que el laboratorio permita antes de lanzar recursos y registrar los supuestos. [AWS Pricing Calculator](https://calculator.aws/).

| Recurso previsto | Motivo | Componentes que pueden generar costo |
| --- | --- | --- |
| EC2 Amazon Linux 2023, `t3.micro` o alternativa admitida | Ejecutar los scripts de origen, alojar el CSV y conectar a RDS. | Tiempo de cómputo, cargos por créditos de CPU si aplica el modo Unlimited, transferencia y dirección IPv4 pública si se asigna. |
| EBS `gp3` o volumen equivalente admitido | Conservar el CSV de 1,91 GiB, el ZIP, sistema operativo y margen de extracción. | GiB aprovisionados mientras exista el volumen, IOPS o rendimiento adicionales y snapshots que se conserven. |
| RDS MariaDB Single-AZ | Alojar las 20 994 estaciones del catálogo de origen. | Cómputo, almacenamiento aprovisionado, opciones de E/S, respaldos o snapshots según la retención y la transferencia aplicable. |
| Red existente y VPC compartida por EC2 y RDS | Mantener RDS privado y permitir un camino directo a EPA desde EC2. | Dirección pública de EC2, si se usa, y transferencia según ruta, región y cruce de zonas. La topología usa conectividad existente o una salida directa autorizada. |

[Precios de EC2](https://aws.amazon.com/ec2/pricing/), [EBS](https://aws.amazon.com/ebs/pricing/), [RDS para MariaDB](https://aws.amazon.com/rds/mariadb/pricing/), [VPC e IPv4 pública](https://aws.amazon.com/vpc/pricing/).

## Control operativo en 2B

1. Antes de ejecutar, registrar región, clase y horas estimadas, GiB, direcciones públicas, retención y posibles flujos entre zonas. Revisar si `t3` usa Standard o Unlimited y cómo factura el laboratorio el exceso de CPU.
2. Al terminar la jornada, detener EC2 desde la consola de AWS y comprobar que figure `stopped`. Detener RDS cuando la configuración lo permita y verificar `stopped`. Según AWS, RDS puede reiniciarse automáticamente tras siete días detenido. EBS, almacenamiento y respaldos de RDS, snapshots y las direcciones públicas que sigan asignadas pueden continuar generando costos. [Detener EC2](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/Stop_Start.html), [detener RDS temporalmente](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/USER_StopInstance.html).
3. Usar las pantallas **EC2 → Instances, Volumes y Elastic IPs** y **RDS → Databases y Snapshots** en la región usada. Si el laboratorio habilita AWS CLI, las operaciones de solo lectura equivalentes incluyen `ec2 describe-instances`, `ec2 describe-volumes`, `ec2 describe-addresses`, `rds describe-db-instances` y `rds describe-db-snapshots`. Registrar fecha y estado, y contrastar con la vista de facturación o la cuota del laboratorio cuando esté disponible.
4. Al cerrar el proyecto y con autorización, revisar qué artefactos deben preservarse y liberar almacenamiento, snapshots y direcciones que ya carezcan de uso. Verificar el inventario y la facturación después de la limpieza autorizada.
