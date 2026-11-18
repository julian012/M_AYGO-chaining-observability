# Formatos de log

## JSON (gateway, bus, modern)
Una línea por evento, UTF-8, máx. 1024 bytes.
```json
{"ts":"2026-11-12T15:30:45.123Z","layer":"bus","id":"BUS-1a2b3c4d5e6f","parent_id":"GW-aabbccddeeff","status":200,"duration_ms":12}
```
El gateway agrega `"channel"` (`mobile` | `web` | `partner`), derivado de la API key (no de un header del cliente).
`json.dumps` escapa saltos de línea: un valor malicioso no puede fabricar una segunda entrada.

## Core legacy: ancho fijo (81 caracteres + `\n`)
Definido una sola vez en `CORE_LAYOUT` (`src/obsproto/common.py`); el simulador y el parser comparten la tupla.

| Campo | Pos. (0-based) | Ancho | Contenido |
|---|---|---|---|
| `ts` | 0–16 | 17 | `YYYYMMDDHHMMSSmmm` UTC |
| `core_id` | 17–36 | 20 | `CORE-<12 hex>` alineado a la izquierda |
| `parent_id` | 37–56 | 20 | `BUS-<12 hex>` alineado a la izquierda |
| `txn` | 57–60 | 4 | `TRF `, `PAY `, `BAL ` |
| `amount` | 61–72 | 12 | centavos, ceros a la izquierda |
| `status` | 73–74 | 2 | `00` OK · `91` fallo simulado |
| `dur_ms` | 75–80 | 6 | duración, ceros a la izquierda |

Ejemplo:
```
20261112153045123CORE-1a2b3c4d5e6f   BUS-aabbccddeeff    TRF 00000000250000000042
```
> El log del core **no contiene número de cuenta** (minimización de datos): solo lo necesario para correlacionar.

## Reglas de validación del parser (seguridad)
- Longitud exacta (core) o ≤ 1024 (JSON); ASCII en el core.
- ID propio y padre con regex estricta `^(CLI|GW|BUS|CORE|MOD)-[0-9a-f]{12}\Z`.
- El prefijo del ID propio y el del padre deben corresponder a la capa declarada (`bus` ⇒ `BUS-` con padre `GW-`).
  Un log manipulado no puede saltarse capas.
- Líneas inválidas se cuentan (`malformedLines`) y se descartan; nunca detienen al correlacionador.
