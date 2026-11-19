# Modelo de amenazas (STRIDE) y controles

**Activos:** integridad de la correlación (evidencia para diagnóstico), confidencialidad de datos de transacción,
disponibilidad del flujo, credenciales (API keys, tokens, llaves AWS del lab).
**Fronteras de confianza:** Internet ⇄ gateway (TLS + API key) · red interna (aislada) · volúmenes de logs ⇄ correlacionador (solo lectura) · operador ⇄ correlacionador (loopback + token / túnel SSH).

| # | Amenaza | STRIDE | Control implementado | Dónde | Verificación |
|---|---|---|---|---|---|
| 1 | Cliente sin credencial invoca el flujo | S | API key por canal, comparación en tiempo constante; sin clave ⇒ 401 | `gateway.py`, `common.token_matches` | `test_common`, smoke |
| 2 | Llamada directa a bus/core saltándose el gateway | S/E | Red `internal: true` (sin puertos publicados) + `X-Internal-Token` | `docker-compose.yml`, `BaseHandler.require_internal` | smoke (401) |
| 3 | Inyección de campos/valores maliciosos | T | Lista blanca estricta de campos, tipos y rangos; validación repetida en cada capa | `schema.py` | `test_common` |
| 4 | **Inyección en logs** (falsificar eventos) | T/R | Los loggers de texto eliminan `\r`/`\n` y solo escriben valores ya validados; los spans usan `json.dumps`; IDs con regex anclada en `\Z`; el `client-id` (header o SOAP) se valida (`CLI-<12 hex>`) o se rechaza; cada parser exige la línea completa con regex anclada | `common.py`, `parsers.py` | `test_parsers`, `test_common` |
| 5 | Log manipulado que enlaza capas no contiguas | T | El parser exige prefijos coherentes con la capa | `parsers.py` | `test_parsers` |
| 6 | Alteración de logs por el correlacionador comprometido | T | Volúmenes montados `:ro` | `docker-compose.yml` | revisión |
| 7 | Fuga de datos sensibles en logs | I | El core no registra cuenta; los logs solo tienen IDs, estado y duración | `core.py`, `log-formats.md` | revisión |
| 8 | Fuga de IDs internos al cliente | I | La respuesta solo devuelve el `clientId` | `gateway.py` | smoke |
| 9 | Errores que revelan detalles internos | I | Respuestas genéricas (`internal_error`); sin trazas al cliente; `sys_version` vacío | `common.BaseHandler` | revisión |
| 10 | Consulta no autorizada de trazas | I | Bearer token; puerto solo en `127.0.0.1`; en AWS por túnel SSH | `server.py`, SG | smoke (401) |
| 11 | Denegación por volumen / payload grande | D | Rate limit por canal (token bucket), cuerpo ≤ 4 KB (413), timeouts de socket y de llamadas salientes, `mem_limit`/`cpus`/`pids_limit` | `common.py`, compose | smoke (413) |
| 12 | Memoria ilimitada en el correlacionador | D | `MAX_EVENTS` con evicción; líneas > 4 KB descartadas | `graph.py`, `server.py` | `test_graph` |
| 13 | Interceptación en el borde | I | TLS ≥ 1.2 en el gateway; HSTS | `common.serve` | smoke TLS |
| 14 | Escape de contenedor / escalado de privilegios | E | Usuario 10001, `read_only`, `cap_drop: ALL`, `no-new-privileges`, `tmpfs noexec` | compose, Dockerfile | `docker compose config` |
| 15 | Robo de credenciales de la instancia (SSRF) | E | IMDSv2 obligatorio y hop-limit 1 (contenedores no llegan a metadatos) | `infra/template.yaml` | cfn-lint |
| 16 | Exposición de la instancia a Internet | S/I | SG solo permite una IP `/32` (Rule rechaza `0.0.0.0/0`); sin `0.0.0.0/0` de entrada; egress limitado | `infra/template.yaml` | cfn-lint |
| 17 | Datos en reposo | I | EBS cifrado; bucket S3 cifrado, sin acceso público, solo TLS, expiración 30 d | `infra/template.yaml` | cfn-lint |
| 18 | Secretos en el repositorio | I | `.env`/llaves generados en el host, ignorados por git; plantilla `.env.example`; escaneo con gitleaks (CI + pre-commit) | `.gitignore`, CI | CI |
| 19 | Dependencias / imagen vulnerables | T | Solo stdlib (0 dependencias de runtime); imagen base fijada; Trivy + pip-audit + Dependabot | Dockerfile, CI | CI |
| 20 | Repudio (no se sabe qué pasó) | R | Cada capa deja log con ID, estado y duración; la traza es reproducible | todo | `make trace` |
| 21 | **XXE / expansión de entidades** en SOAP (lectura de archivos, *billion laughs*) | I/D | Se rechaza `<!DOCTYPE` y `<!ENTITY` antes de parsear; cuerpo ≤ 4 KB; estructura exacta del sobre y de la transacción | `wire.py` | `test_wire` |
| 22 | Trama manipulada (desbordar campos, ID de otra capa) | T | Longitud exacta, solo ASCII, MTI conocido, ID con prefijo `BUS-`, campos numéricos verificados; luego `validate_txn` | `wire.py`, `core.py` | `test_wire` |
| 23 | Enlace inventado por correlación heurística | T | El parser del bus solo une por ID (línea DEBUG `mapped`); nunca por hilo ni por hora. Sin el par, la traza se reporta incompleta | `parsers.BusLogParser` | `test_parsers` |
| 24 | Exposición del tracenumber del core en el log del bus | I | Política `BUS_TRACE_LOG=token`: HMAC-SHA256 con `TRACE_TOKEN_KEY` (secreto en `.env`, solo bus y correlacionador); la máscara se desaconseja porque con la hora identifica el movimiento | `tracelink.py`, `corelink.py` | `test_corelink`, `evidence/core-link-local-*` |
| 25 | Conexión directa al core por TCP | S/T | Puerto solo en la red interna (sin publicar); validación estricta de longitud y trama; timeout de 10 s por conexión. Sin token por mensaje (como un core real): **riesgo residual** | `core.py`, `wire.py`, compose | `test_transport` |
| 26 | Robo o alteración de archivos planos en el FTP | I/T | Usuario y contraseña (`FTP_PASSWORD`), solo dos carpetas, nombres en lista blanca (sin `..`), tamaño máximo, `.tmp` → `.txt` al terminar y cola con conteo (un archivo truncado o alterado en número de registros no se procesa). FTP en claro: **riesgo residual** | `ftpserver.py`, `core.py`, `modern.py` | `test_transport` |
| 27 | Archivo renombrado o con rango falso para atribuirle movimientos ajenos | T | El nombre se valida con regex estricta (nodo, fecha, rango con fin ≥ inicio); solo se une con candidato único; el hash del MFT queda en su log para auditoría. Quien pueda escribir en `/outbox` puede crear nombres: misma frontera de confianza que los logs (riesgo residual 7) | `wire.py`, `filelink.py` | `test_mft` |

## Principios de confianza cero
Los controles de la tabla siguen los principios de una arquitectura de confianza cero (NIST SP 800-207):

| Principio | Cómo se aplica |
|---|---|
| Verificación explícita | Cada canal se autentica con su API key en el gateway, que termina TLS; las capas internas exigen un token interno. |
| Mínimo privilegio | Contenedores sin root, sin capacidades de Linux y con sistema de archivos de solo lectura; cada capa escribe solo en su volumen y el correlacionador solo lee. Su API escucha únicamente en la interfaz local. |
| Supuesto de brecha | La red interna no tiene salida a Internet, cada línea de log se valida como entrada no confiable y el modelo nunca une por suposición, de modo que una línea falsa no enlaza capas que no son contiguas. |

La excepción declarada es el transporte interno sin cifrar (riesgos 1 y 12): se aparta del principio de no confiar en la red
interna y su mejora es mTLS.

## Riesgos residuales (aceptados y declarados)
1. **Tráfico interno sin cifrar** (HTTP dentro de la red aislada de Docker). Mitigado con red interna + token; mejora: mTLS.
2. **Token interno compartido** entre servicios (no por servicio ni rotado). Mejora: tokens por servicio + AWS Secrets Manager (no disponible/permitido de forma fiable en el Learner Lab).
3. **Llave TLS legible (0644)** dentro del directorio `certs/` para que el UID 10001 del contenedor la lea. Mitiga: certificado autofirmado de 30 días, no versionado, mismo host.
4. **Certificado autofirmado**: el cliente debe fijar el certificado (`--cacert`). En producción: ACM/CA corporativa.
5. **Learner Lab**: `LabInstanceProfile` es más amplio que el mínimo privilegio; no se pueden crear roles propios (`iam:CreateRole` denegado). Es una limitación del entorno, no del diseño.
6. **Plugin de compose** descargado de GitHub con verificación SHA-256 obtenida del mismo origen (integridad de transferencia, no autenticidad). Mejora: fijar el hash en la plantilla.
7. **Correlación por ID, no por firma**: quien pueda escribir en los logs puede crear eslabones falsos. Mitiga: volúmenes de escritura exclusiva por capa y lectura solo para el correlacionador.
8. **Salida a Internet sin restringir por destino** (Trivy AWS-0104): sin NAT ni VPC endpoints (costo, ADR-0002) la instancia sale a `0.0.0.0/0`, aunque solo por 443/80/53. Mejora: VPC endpoints o proxy de salida con lista de dominios.
9. **S3 cifrado con llave de AWS, no con CMK** (Trivy AWS-0132): SSE-S3 + bloqueo público + TLS obligatorio. Mejora: SSE-KMS con CMK y rotación si los datos dejaran de ser sintéticos.

10. **Generador de carga (solo durante las pruebas):** segunda instancia con SSH desde una IP y salida al gateway únicamente por su grupo de seguridad. Recibe solo `API_KEYS` (no los tokens internos ni el del correlacionador), copiadas por SSH, nunca por `UserData`. Se elimina al terminar (`EnableLoadGenerator=false`).

11. **Llave de tokenización (`TRACE_TOKEN_KEY`):** quien la tenga puede calcular el token de cualquier tracenumber candidato (el espacio de un consecutivo es predecible). Se custodia como secreto y no sale del bus ni del correlacionador. Mejora: rotación y almacenamiento en un gestor de secretos.

12. **Transporte interno sin cifrar ni autenticar por mensaje:** las tramas TCP y el FTP viajan en claro dentro de la red interna de Docker, y el core no autentica cada trama (antes, con HTTP, exigía `X-Internal-Token`). Mejora: TLS mutuo en el socket del core y SFTP/FTPS para los archivos.

Trivy AWS-0028 (IMDSv2) es un falso positivo: la plantilla exige tokens y `HopLimit: 1`. Las tres excepciones están en `.trivyignore` con su justificación.

## Datos
Todo el dato es **sintético**. No se usan datos, formatos, nombres ni logs reales de ninguna organización.
