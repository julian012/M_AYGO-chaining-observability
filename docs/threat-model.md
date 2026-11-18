# Modelo de amenazas (STRIDE) y controles

**Activos:** integridad de la correlación (evidencia para diagnóstico), confidencialidad de datos de transacción,
disponibilidad del flujo, credenciales (API keys, tokens, llaves AWS del lab).
**Fronteras de confianza:** Internet ⇄ gateway (TLS + API key) · red interna (aislada) · volúmenes de logs ⇄ correlacionador (solo lectura) · operador ⇄ correlacionador (loopback + token / túnel SSH).

| # | Amenaza | STRIDE | Control implementado | Dónde | Verificación |
|---|---|---|---|---|---|
| 1 | Cliente sin credencial invoca el flujo | S | API key por canal, comparación en tiempo constante; sin clave ⇒ 401 | `gateway.py`, `common.token_matches` | `test_common`, smoke |
| 2 | Llamada directa a bus/core saltándose el gateway | S/E | Red `internal: true` (sin puertos publicados) + `X-Internal-Token` | `docker-compose.yml`, `BaseHandler.require_internal` | smoke (401) |
| 3 | Inyección de campos/valores maliciosos | T | Lista blanca estricta de campos, tipos y rangos; validación repetida en cada capa | `schema.py` | `test_common` |
| 4 | **Inyección en logs** (falsificar eventos) | T/R | `json.dumps` escapa `\n`; IDs con regex anclada en `\Z`; el `client-id` recibido se valida (`CLI-<12 hex>`) o se rechaza | `common.py`, `parsers.py` | `test_parsers`, `test_common` |
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

## Riesgos residuales (aceptados y declarados)
1. **Tráfico interno sin cifrar** (HTTP dentro de la red aislada de Docker). Mitigado con red interna + token; mejora: mTLS.
2. **Token interno compartido** entre servicios (no por servicio ni rotado). Mejora: tokens por servicio + AWS Secrets Manager (no disponible/permitido de forma fiable en el Learner Lab).
3. **Llave TLS legible (0644)** dentro del directorio `certs/` para que el UID 10001 del contenedor la lea. Mitiga: certificado autofirmado de 30 días, no versionado, mismo host.
4. **Certificado autofirmado**: el cliente debe fijar el certificado (`--cacert`). En producción: ACM/CA corporativa.
5. **Learner Lab**: `LabInstanceProfile` es más amplio que el mínimo privilegio; no se pueden crear roles propios (`iam:CreateRole` denegado). Es una limitación del entorno, no del diseño.
6. **Plugin de compose** descargado de GitHub con verificación SHA-256 obtenida del mismo origen (integridad de transferencia, no autenticidad). Mejora: fijar el hash en la plantilla.
7. **Correlación por ID, no por firma**: quien pueda escribir en los logs puede crear eslabones falsos. Mitiga: volúmenes de escritura exclusiva por capa y lectura solo para el correlacionador.

## Datos
Todo el dato es **sintético**. No se usan datos, formatos, nombres ni logs reales de ninguna organización.
