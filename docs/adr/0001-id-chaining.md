# ADR-0001: Correlación por encadenamiento de IDs en lugar de un ID único propagado

- **Estado:** Aceptada · **Fecha:** 2026-11-10

## Contexto
Cada capa (gateway, bus, core) genera su propio identificador y no puede modificarse para reutilizar uno común ni para
propagar `traceparent` (W3C Trace Context). El core solo emite logs de ancho fijo.

## Decisión
Cada capa registra **el ID recibido y el propio**. Un correlacionador externo reconstruye la cadena (grafo de IDs) a partir
de los logs y deriva un `trace_id` canónico.

## Alternativas
1. **Propagar `traceparent` / un ID único extremo a extremo.** Descartada: requiere modificar sistemas que no se pueden tocar.
2. **Correlación por tiempo + payload.** Descartada: ambigua bajo carga y propensa a falsos enlaces.
3. **Agente/instrumentación en el core.** Descartada: no soportado ni modificable.

## Consecuencias
- (+) No invasivo; funciona con sistemas heterogéneos y legacy.
- (+) Verificable: la métrica es el % de trazas completas.
- (−) Latencia de correlación (se lee de logs, no en línea).
- (−) La integridad depende de la integridad de los logs → volúmenes de solo lectura para el lector (ver threat-model #6, #5, #7).
