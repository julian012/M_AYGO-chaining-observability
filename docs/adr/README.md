# Registro de decisiones de arquitectura (ADR)

Formato: contexto → decisión → alternativas → consecuencias. Una decisión por archivo, numeradas, nunca se borran (se marcan *Reemplazada por*).

| ADR | Título | Estado |
|---|---|---|
| [0001](0001-id-chaining.md) | Correlación por encadenamiento de IDs | Aceptada |
| [0002](0002-ec2-compose-vs-eks.md) | EC2 + Compose en lugar de EKS/ECS/Lambda | Aceptada |
| [0003](0003-stdlib-minimal.md) | Solo biblioteca estándar en runtime | Aceptada |
| [0004](0004-security-baseline.md) | Línea base de seguridad | Aceptada |
| [0005](0005-heterogeneous-sources.md) | Protocolos y fuentes de telemetría heterogéneos | Aceptada |
| [0006](0006-core-tracenumber-link.md) | Eslabón bus → core con tracenumber (core sin ID del bus) | Aceptada |
| [0007](0007-tcp-and-ftp-transport.md) | Tramas por TCP y archivos planos por FTP | Aceptada |
| [0008](0008-mft-file-transfer-link.md) | Eslabón MFT y nombre de archivo con rango de horas | Aceptada |

Pendientes: backend de trazas (Tempo vs. Jaeger vs. X-Ray) · estrategia de muestreo · persistencia del grafo (en memoria vs. DynamoDB).
