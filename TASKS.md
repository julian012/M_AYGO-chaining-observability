# Estado del proyecto

**Cierre de la entrega:** sáb 21-nov-2026, 07:03 a.m. · Leyenda: `[x]` hecho y verificado · `[~]` escrito, falta verificar · `[ ]` pendiente

## Hecho (esqueleto, 10-nov)
- [x] Capas simuladas: gateway, bus, core (log de ancho fijo), modern
- [x] Correlacionador: parsers, grafo de IDs, API `/trace`, `/stats`, `/metrics`
- [x] 52 pruebas unitarias; ruff limpio (88 desde el 18-nov)
- [x] Prueba local de punta a punta (procesos): 300/300 trazas completas con 10 % de fallos en el core
- [x] Pruebas negativas de seguridad locales (401, 413, 422)
- [x] Hallazgo corregido por las pruebas: `$` en la regex de IDs aceptaba `\n` final (inyección en logs) → `\Z`
- [x] Certificado TLS: RSA-2048 (EC fallaba con el LibreSSL del Python de macOS; ver `scripts/gen-certs.sh`)
- [x] Plantilla CloudFormation validada con `cfn-lint`
- [x] Docs: arquitectura, formatos de log, modelo de amenazas, atributos de calidad, 4 ADRs
- [x] CI definido (lint, tests, cfn-lint, gitleaks, pip-audit, Trivy, hadolint)
- [x] Stack levantado con Docker Compose en local: 300/300 trazas completas, 479,5 rps, p95 47,5 ms (`evidence/docker-local-smoke-2026-11-10.json`)
- [x] TLS del gateway verificado en local: cert de confianza ⇒ 200; sin confiar, HTTP plano y TLS 1.1 ⇒ rechazados; HSTS y `nosniff` presentes

## Fuentes heterogéneas (18-nov, ADR-0005)
- [x] Protocolos reales por salto: REST/JSON y SOAP en el gateway, SOAP gateway→bus, trama de ancho fijo bus→core, REST core→moderno
- [x] Telemetría distinta por capa: access log `clave=valor`, texto con niveles (par solo en DEBUG), ancho fijo, spans OTel
- [x] Un parser por fuente; el del bus no une por hilo ni por tiempo
- [x] Seguridad: XXE/DTD rechazados, tramas validadas; amenazas 21–23
- [x] Local: 300/300 trazas completas con bus en DEBUG (496 rps, p95 44,6 ms); 0/150 con bus en INFO (`evidence/local-hetero-*`)
- [x] Documentación visual en el repo: `docs/flujo-y-telemetria.md` y `docs/visual/flujo-trazabilidad.html`
- [x] AWS actualizado a `v0.2.0` (18-nov): 500/500 trazas completas con las 4 fuentes; con calentamiento, p99 222 ms (antes 1,6 s), pero solo 117 rps y p50 166 ms (`evidence/aws-v020-warm-2026-11-18.json`)
- [~] Hallazgo: la primera corrida tras arrancar es lenta (p95 ~2 s); el calentamiento corrige la cola en AWS, no el throughput. Hipótesis: CPU (2 vCPU compartidas con el generador). Confirmar cargando desde otra instancia

## Tablero de CloudWatch (18-nov, v0.2.1)
- [x] `obsproto-pruebas` creado por CloudFormation (change set: solo agrega el tablero)
- [x] Generador publica series por escenario (`--cw-scenario`); monitor del host como servicio systemd
- [x] Demostración en AWS: escenario `aws-demo-c20`, 600/600 trazas completas, publicado en CloudWatch

## Preparación de las pruebas de estrés (18-nov, noche)
- [x] Generador de carga opcional en la plantilla (`EnableLoadGenerator`), por red privada, con mínimo privilegio
- [x] Generador: modo por tiempo (`--duration`), `--ids-out` / `--verify-ids` (verificación en la instancia de la app), `--verify-last`, capas faltantes por traza
- [x] `scripts/run_scenarios.sh`: 11 escenarios, evidencia por escenario y series en CloudWatch
- [x] `scripts/manual_vs_model.py`: 4 `grep` capa por capa frente a `/trace` (local: 12,8 ms frente a 1,0 ms con logs pequeños)
- [x] Certificado con varias IPs; `GATEWAY_CPUS` configurable (escalado vertical)
- [x] Pruebas en AWS (18/19-nov): 11 escenarios con generador aparte; resumen en `evidence/aws-stress-2026-11-18/README.md`
- [x] Hallazgo y corrección: tras reiniciar con más historia que `MAX_EVENTS`, 0 trazas completas en memoria → evicción por hora del evento (`v0.3.2`); verificado en AWS: 0/1500 → 1500/1500
- [x] Generador de carga eliminado del stack; instancia de la aplicación detenida

## Siguiente
- [x] En AWS: 5 contenedores `healthy`, `ReadonlyRootfs=true`, usuario 10001 (18-nov)
- [x] Repo publicado en https://github.com/julian012/M_AYGO-chaining-observability, CI en verde (5 jobs) y tag `v0.1.0` (18-nov)
- [x] Despliegue en el Learner Lab (stack `obsproto`, t3.medium): 500/500 trazas completas, 87 rps, p95 303 ms, p99 1,6 s (`evidence/aws-smoke-2026-11-18.json`). El throughput es mucho menor que en local (479 rps): investigar en las pruebas de estrés
- [ ] Exportar spans a OTLP + Grafana (dashboard de flujo y búsqueda por cualquier ID)
- [ ] Plan de pruebas de estrés (k6/Locust): carga normal, pico, sostenida, caída del core/bus
- [ ] Medir overhead con/sin correlacionador y tiempo de localización manual vs. modelo
- [ ] Prueba de escalado (réplicas) y de caos (matar un contenedor)
- [ ] Completar `docs/quality-attributes.md` con números reales
- [~] Diagrama de secuencia de los IDs hecho (`docs/flujo-y-telemetria.md`); faltan C4 de contexto/contenedores
- [ ] Paper (IEEE): ver plan en `../Plan_Tareas_Proyecto_02.md`
- [ ] Revisar `docs/threat-model.md` contra lo realmente desplegado
