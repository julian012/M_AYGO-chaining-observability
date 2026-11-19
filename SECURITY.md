# Política de seguridad

Este es un prototipo académico con **datos sintéticos**. Aun así se trata como si fuera real.

## Reporte de vulnerabilidades
Abre un *issue* privado o contacta al autor del repositorio. No publiques credenciales ni exploits funcionales.

## Reglas del repositorio
- **Nunca** se versionan secretos: `.env`, `*.pem`, `*.key`, `credenciales*`, `~/.aws` (ver `.gitignore`).
  `gitleaks` corre en pre-commit y en CI sobre todo el historial.
- Las credenciales del AWS Learner Lab son temporales y personales: solo en `~/.aws/credentials`.
- Si un secreto se filtra: **rotarlo primero**, luego limpiar el historial.
- No se usan datos, logs ni nombres reales de ninguna organización.

## Modelo de amenazas y controles
Ver [`docs/threat-model.md`](docs/threat-model.md) y [`docs/adr/0004-security-baseline.md`](docs/adr/0004-security-baseline.md).
