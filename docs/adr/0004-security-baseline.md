# ADR-0004: Línea base de seguridad

- **Estado:** Aceptada · **Fecha:** 2026-11-10

## Decisión
Seguridad por capas (defensa en profundidad), verificable y automatizada:
1. **Borde:** TLS ≥ 1.2, API key por canal, rate limit, validación por lista blanca, tamaño máximo de cuerpo.
2. **Interno:** red Docker `internal` (sin Internet), token servicio-a-servicio, validación repetida.
3. **Contenedores:** no root, FS de solo lectura, `cap_drop: ALL`, `no-new-privileges`, límites de recursos.
4. **Datos:** minimización (no se registran cuentas), sintéticos, logs montados en solo lectura para el lector.
5. **IaC/AWS:** SG a una IP `/32`, IMDSv2, EBS y S3 cifrados, S3 sin acceso público y solo TLS, sin recursos IAM nuevos.
6. **Cadena de suministro:** 0 dependencias de runtime, imagen base fijada, Trivy/pip-audit/gitleaks/Dependabot en CI.
7. **Secretos:** nunca versionados; generados en el host (`scripts/gen-env.sh`).

El detalle por amenaza está en [`../threat-model.md`](../threat-model.md); los riesgos residuales se declaran explícitamente.

## Consecuencias
Algunos controles de producción (mTLS, rotación con Secrets Manager, ACM) quedan fuera por las restricciones del Learner Lab y se documentan como trabajo futuro.
