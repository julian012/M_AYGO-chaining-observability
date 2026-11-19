# Eslabón MFT en AWS · 19-nov-2026 (`v0.6.1`)

Mismo entorno que `aws-stress-2026-11-19`, con 7 contenedores: el core deja el archivo en `/outbox` con nombre diciente (nodo, fecha y
rango de horas) y el MFT lo copia y mueve a `/inbox`. La traza completa exige 5 capas: gateway, bus, core, mft, moderno.

| Escenario | Tx | rps | p50 / p95 / p99 (ms) | Errores | Trazas completas (5 capas) |
|---|---|---|---|---|---|
| `warmup` | 400 | 64.0 | 107.5 / 298.4 / 1385.6 | 0 | 400/400 |
| `normal-c20-r1` | 1500 | 108.0 | 169.7 / 339.4 / 430.6 | 0 | 1500/1500 |
| `normal-c20-r2` | 1500 | 106.5 | 168.8 / 387.7 / 559.3 | 0 | 1500/1500 |
| `normal-c20-r3` | 1500 | 97.2 | 181.8 / 398.4 / 499.5 | 0 | 1500/1500 |

**Tramo asíncrono** (300 transacciones de `normal-c20-r1`, segundos después de la respuesta al cliente):
- hasta que el MFT movió el archivo: mediana 5.0 s, máx. 7.88 s
- del movimiento al procesamiento en el moderno: mediana 1.53 s, máx. 1.6 s
- **traza completa:** mediana 6.24 s, p95 8.36 s, máx. 9.1 s
  (teórico ≈ lote del core 5 s + job del MFT 3 s + revisión del moderno 2 s)
