# ADR-0003: Solo biblioteca estándar de Python en tiempo de ejecución

- **Estado:** Aceptada · **Fecha:** 2026-11-10

## Contexto
Los stand-ins y el correlacionador son pequeños. Cada dependencia añade superficie de ataque, CVEs que seguir y peso a la imagen.

## Decisión
Cero dependencias de runtime (`http.server`, `urllib`, `ssl`, `json`). Las herramientas de desarrollo (pytest, ruff, cfn-lint,
pip-audit) quedan en `[project.optional-dependencies].dev`.

## Consecuencias
- (+) Sin cadena de suministro en runtime; imagen mínima; `pip-audit` trivial.
- (−) `http.server` no está pensado para producción: se acota con timeouts, límites de cuerpo y de hilos; el rendimiento se
  reporta con honestidad como límite del prototipo (ver evaluación). En un sistema real: un servidor ASGI/WSGI o el propio
  gateway/ESB.
