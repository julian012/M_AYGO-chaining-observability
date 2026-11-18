# Estado del proyecto

**Cierre de la entrega:** sáb 21-nov-2026, 07:03 a.m. · Leyenda: `[x]` hecho y verificado · `[~]` escrito, falta verificar · `[ ]` pendiente

## Hecho (esqueleto, 10-nov)
- [x] Capas simuladas: gateway, bus, core (log de ancho fijo), modern
- [x] Correlacionador: parsers, grafo de IDs, API `/trace`, `/stats`, `/metrics`
- [x] 52 pruebas unitarias; ruff limpio
- [x] Prueba local de punta a punta (procesos): 300/300 trazas completas con 10 % de fallos en el core
- [x] Pruebas negativas de seguridad locales (401, 413, 422)
- [x] Hallazgo corregido por las pruebas: `$` en la regex de IDs aceptaba `\n` final (inyección en logs) → `\Z`
- [x] Certificado TLS: RSA-2048 (EC fallaba con el LibreSSL del Python de macOS; ver `scripts/gen-certs.sh`)
- [x] Plantilla CloudFormation validada con `cfn-lint`
- [x] Docs: arquitectura, formatos de log, modelo de amenazas, atributos de calidad, 4 ADRs
- [x] CI definido (lint, tests, cfn-lint, gitleaks, pip-audit, Trivy, hadolint)
- [x] Stack levantado con Docker Compose en local: 300/300 trazas completas, 479,5 rps, p95 47,5 ms (`evidence/docker-local-smoke-2026-11-10.json`)
- [x] TLS del gateway verificado en local: cert de confianza ⇒ 200; sin confiar, HTTP plano y TLS 1.1 ⇒ rechazados; HSTS y `nosniff` presentes

## Siguiente
- [ ] Verificar healthchecks, `read_only` y volúmenes `:ro` en ejecución real
- [~] Repo publicado en https://github.com/julian012/M_AYGO-chaining-observability (18-nov); falta ver el CI en verde y crear el tag `v0.1.0`
- [ ] Primer despliegue en el Learner Lab con `infra/README.md`
- [ ] Exportar spans a OTLP + Grafana (dashboard de flujo y búsqueda por cualquier ID)
- [ ] Plan de pruebas de estrés (k6/Locust): carga normal, pico, sostenida, caída del core/bus
- [ ] Medir overhead con/sin correlacionador y tiempo de localización manual vs. modelo
- [ ] Prueba de escalado (réplicas) y de caos (matar un contenedor)
- [ ] Completar `docs/quality-attributes.md` con números reales
- [ ] Diagramas C4 (contexto/contenedores) y de secuencia de los 4 IDs
- [ ] Paper (IEEE): ver plan en `../Plan_Tareas_Proyecto_02.md`
- [ ] Revisar `docs/threat-model.md` contra lo realmente desplegado
