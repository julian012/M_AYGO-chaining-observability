# obsproto — Observabilidad end-to-end en sistemas heterogéneos

Prototipo de investigación (curso AYGO, *Arquitectura y Gobernabilidad Tecnológica*). Demuestra cómo **reconstruir la traza
de punta a punta** cuando cada capa (gateway, bus de integración, core legacy, servicios modernos) genera su **propio ID** y
**no puede modificarse** para compartir uno: se **encadenan** los IDs que cada capa ya registra.

> Todo es genérico y con **datos sintéticos**. No se usan datos, nombres ni logs reales de ninguna organización.

## Cómo funciona (30 segundos)
```
Canal ─▶ Gateway ─▶ Bus ─▶ Core legacy ─▶ Servicio moderno
 CLI-…    GW-…      BUS-…   CORE-… (log ancho fijo)   MOD-…
            └──── cada capa registra  ID recibido → ID propio ────┘
                         │
                 Correlacionador: lee los logs (solo lectura), arma el grafo de IDs
                 y responde GET /trace/<CUALQUIER ID>  →  traza completa
```
Detalle en [`docs/architecture.md`](docs/architecture.md) y [`ADR-0001`](docs/adr/0001-id-chaining.md).

## Requisitos
Docker + Compose v2, `openssl`, Python 3.9+ (solo para el generador de carga y las pruebas).

## Inicio rápido (local)
```bash
make up                       # genera .env y certificado TLS, construye y levanta todo
make load N=300 C=15          # carga sintética por 3 canales + % de trazas completas
make stats                    # estadísticas del correlacionador
make trace ID=CLI-xxxxxxxxxxxx  # traza a partir de cualquier ID (también GW-/BUS-/CORE-/MOD-)
make down                     # detener (make clean borra los logs)
```
Inyectar fallos en el core: `FAULT_RATE=0.1` en `.env` y `docker compose up -d`.

## Pruebas y calidad
```bash
pip install -e ".[dev]"
make test      # 52 pruebas: parsers, grafo de IDs, validación, rate limit
make lint      # ruff + cfn-lint
```
CI (GitHub Actions): lint, pruebas (Python 3.9 y 3.12), cfn-lint, gitleaks, pip-audit, Trivy (IaC e imagen), hadolint.

## Despliegue en AWS (Learner Lab)
Ver [`infra/README.md`](infra/README.md). CloudFormation, sin recursos IAM, una EC2 + bucket cifrado, acceso solo desde tu IP.

## Seguridad
Defensa en profundidad con 20 amenazas analizadas y 7 riesgos residuales declarados:
[`docs/threat-model.md`](docs/threat-model.md) · [`SECURITY.md`](SECURITY.md) · [`ADR-0004`](docs/adr/0004-security-baseline.md).

## Estructura
```
src/obsproto/        gateway, bus, core, modern + common/schema
src/obsproto/correlator/   parsers, graph (grafo de IDs), server (API)
tests/               pruebas unitarias
scripts/             gen-env, gen-certs, generate_load
infra/               plantilla CloudFormation + guía
docs/                arquitectura, formatos de log, amenazas, atributos de calidad, ADRs
evidence/            resultados curados para el paper
```

## Atributos de calidad
Escenarios, tácticas y métricas de los 11 atributos: [`docs/quality-attributes.md`](docs/quality-attributes.md).

## Estado y siguientes pasos
Ver [`TASKS.md`](TASKS.md).

## Licencia
MIT.
