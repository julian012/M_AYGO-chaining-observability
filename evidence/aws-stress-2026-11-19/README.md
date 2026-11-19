# Pruebas de estrés en AWS · 19-nov-2026 (`v0.5.1`: tramas por TCP, novedades por FTP)

**Entorno:** EC2 `t3.medium` (2 vCPU, 4 GiB), Docker Compose, 6 contenedores con 0,5 CPU (salvo `gw1cpu-*`: gateway con 1 CPU).
Carga desde una `t3.small` aparte por la red privada (TLS verificado). Límite de tasa del gateway subido a 100 000/s. Bus en DEBUG
salvo `bus-info`. Verificación de cada ID enviado (200/500/502) contra `/trace` en la instancia de la aplicación.
Orquestador: `scripts/run_scenarios.sh`. Series en CloudWatch `obsproto-pruebas` (`aws-<escenario>`).

| Escenario | C | Tx | rps | p50 / p95 / p99 (ms) | Errores | Trazas completas | Incompletas por capas faltantes |
|---|---|---|---|---|---|---|---|
| `warmup` | 10 | 400 | 74.5 | 97.0 / 206.0 / 1457.6 | 0 {'200': 400} | 400/400 (100.0 %) | — |
| `normal-c20-r1` | 20 | 1500 | 106.3 | 169.4 / 340.6 / 525.4 | 0 {'200': 1500} | 1500/1500 (100.0 %) | — |
| `normal-c20-r2` | 20 | 1500 | 104.3 | 174.8 / 360.1 / 444.9 | 0 {'200': 1500} | 1500/1500 (100.0 %) | — |
| `normal-c20-r3` | 20 | 1500 | 100.2 | 178.5 / 399.1 / 486.5 | 0 {'200': 1500} | 1500/1500 (100.0 %) | — |
| `rest-only-c20` | 20 | 1500 | 105.8 | 171.2 / 344.3 / 409.1 | 0 {'200': 1500} | 1500/1500 (100.0 %) | — |
| `step-c10` | 10 | 1000 | 102.1 | 94.3 / 197.9 / 247.7 | 0 {'200': 1000} | 1000/1000 (100.0 %) | — |
| `step-c20` | 20 | 1200 | 99.1 | 182.3 / 402.4 / 503.4 | 0 {'200': 1200} | 1200/1200 (100.0 %) | — |
| `step-c50` | 50 | 3000 | 101.1 | 427.2 / 907.9 / 1057.5 | 0 {'200': 3000} | 3000/3000 (100.0 %) | — |
| `step-c100` | 100 | 6000 | 109.3 | 832.7 / 1522.4 / 1867.9 | 0 {'200': 6000} | 6000/6000 (100.0 %) | — |
| `step-c200` | 200 | 12000 | 57.5 | 1314.9 / 6245.4 / 8791.0 | 1657 {'0': 1657, '200': 10343} | 10343/10343 (100.0 %) | — |
| `warmup-1cpu` | 10 | 300 | 142.0 | 45.5 / 97.1 / 806.3 | 0 {'200': 300} | 300/300 (100.0 %) | — |
| `gw1cpu-c50` | 50 | 3000 | 151.7 | 296.3 / 538.6 / 1045.8 | 0 {'200': 3000} | 3000/3000 (100.0 %) | — |
| `gw1cpu-c100` | 100 | 6000 | 142.7 | 606.5 / 1249.0 / 1393.2 | 0 {'200': 6000} | 6000/6000 (100.0 %) | — |
| `sustained-c50-10min` | 50 | 62794 | 104.6 | 417.8 / 902.1 / 1089.2 | 0 {'200': 62794} | 3000/3000 (100.0 %) | — |
| `fault-core-10pct` | 20 | 1500 | 106.1 | 179.7 / 321.8 / 403.3 | 156 {'200': 1344, '500': 53, '502': 103} | 1500/1500 (100.0 %) | — |
| `bus-info` | 20 | 1000 | 102.2 | 173.8 / 398.9 / 478.0 | 0 {'200': 1000} | 0/1000 (0.0 %) | bus+core+modern: 1000 |
| `bus-outage` | 20 | 13213 | 109.9 | 172.0 / 380.4 / 478.4 | 4200 {'200': 9013, '500': 1400, '502': 2800} | 9013/13213 (68.21 %) | bus+core+modern: 4200 |
| `no-correlator-c20` | 20 | 1500 | 100.6 | 175.7 / 421.1 / 660.6 | 0 {'200': 1500} | no se verifica | — |

**Tiempo hasta traza completa** (tramo core → moderno por FTP, 300 de `normal-c20-r1`): mediana 3.75 s, p95 6.15 s,
máx. 6.79 s (lote del core 5 s + revisión del moderno 2 s).

## Datos de CloudWatch
- **Cuello de botella:** con el gateway a 0,5 CPU (`step-c100`) su CPU máxima por minuto fue 52,8 % (su tope); bus 48 %, core 27 %,
  moderno 39 %. Con 1 CPU (`gw1cpu-*`) el gateway llegó a 81 % y ningún contenedor a su tope: el siguiente límite no se identificó.
- **Caída del bus:** detenido 15:16:21Z, arrancado 15:16:54Z. Errores por ventana de 10 s: 793, 1 194, 1 200, 1 013 y 0 desde la
  ventana de 15:17:00 → recuperación en menos de 10 s, sin intervención.
- **Memoria del correlacionador** en la carga sostenida: 130 → 208 MB (límite 384 MB).

## Core sin ID del bus (tracenumber) en AWS
| Política | rps | Trazas completas | Uniones correctas | Uniones **falsas** | Ambiguas |
|---|---|---|---|---|---|
| `clear` | 97.2 | 1500/1500 (100.0 %) | 1700/1700 | **0** | 0 |
| `token` | 99.5 | 1500/1500 (100.0 %) | 1700/1700 | **0** | 0 |
| `mask4` | 97.3 | 1500/1500 (100.0 %) | 1700/1700 | **0** | 0 |
| `mask3` | 99.4 | 1500/1500 (100.0 %) | 1700/1700 | **0** | 0 |
| `mask2` | 98.3 | 0/1500 (0.0 %) | 0/1700 | **0** | 1700 |
| `none` | 99.8 | 0/1500 (0.0 %) | 0/1700 | **0** | 0 |

A ~98 rps la máscara de 3 dígitos fue única (≈1 000 movimientos en la ventana de ±5 s, justo bajo las 1 000 terminaciones); en local, a
~420 rps, solo el 20 % (`evidence/core-link-local-2026-11-19/`). Confirma que la máscara depende de la carga y el token no.

## Manual frente al modelo (primera medición, con dos problemas)
Manual 39.67 ms · modelo 18.15 ms · cadenas completas: manual
30/30, modelo 15/30. (1) La muestra se tomó de todo el historial (~100 000 tx) y el
grafo solo conserva las recientes en memoria. (2) `/stats` recalculaba la completitud de todo el grafo cada 10 s (monitor) y el
correlacionador llegó a su tope de CPU. Corregido en `v0.6.2`; medición válida en `evidence/aws-retention-2026-11-19/`.
