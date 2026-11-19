# Pruebas de estrés en AWS · 18/19-nov-2026

**Entorno:** AWS Learner Lab us-east-1. Aplicación: EC2 `t3.medium` (2 vCPU, 4 GiB), Docker Compose, `v0.3.1`, cada contenedor
limitado a 0,5 CPU (salvo `gw1cpu-*`: gateway con 1 CPU). Generador: EC2 `t3.small` aparte, por la red privada, TLS verificado.
Límite de tasa del gateway subido a 100 000/s durante las pruebas (para no confundir 429 con saturación). Bus en DEBUG salvo `bus-info`.
Tablero: CloudWatch `obsproto-pruebas` (series `aws-<escenario>`). Orquestador: `scripts/run_scenarios.sh`.
Las trazas se verifican en la instancia de la aplicación consultando `/trace` por cada ID enviado con respuesta 200, 500 o 502
(el estado 0 = timeout o conexión rechazada en el cliente no se verifica). `sustained`: solo las últimas 3000 (el grafo tiene evicción).

| Escenario | C | Tx | rps | Latencia ms p50 / p95 / p99 | Errores | Trazas completas | Incompletas por capas faltantes |
|---|---|---|---|---|---|---|---|
| `warmup` | 10 | 400 | 84.5 | 99.0 / 216.6 / 306.4 | 0 {'200': 400} | 400/400 (100.0 %) | — |
| `normal-c20-r1` | 20 | 1500 | 101.0 | 190.9 / 318.2 / 406.4 | 0 {'200': 1500} | 1500/1500 (100.0 %) | — |
| `normal-c20-r2` | 20 | 1500 | 93.4 | 195.2 / 396.4 / 408.3 | 0 {'200': 1500} | 1500/1500 (100.0 %) | — |
| `normal-c20-r3` | 20 | 1500 | 101.2 | 188.1 / 363.3 / 406.3 | 0 {'200': 1500} | 1500/1500 (100.0 %) | — |
| `rest-only-c20` | 20 | 1500 | 100.7 | 191.8 / 346.0 / 404.5 | 0 {'200': 1500} | 1500/1500 (100.0 %) | — |
| `step-c10` | 10 | 1000 | 92.9 | 99.8 / 189.3 / 210.4 | 0 {'200': 1000} | 1000/1000 (100.0 %) | — |
| `step-c20` | 20 | 1200 | 103.9 | 178.2 / 378.7 / 459.4 | 0 {'200': 1200} | 1200/1200 (100.0 %) | — |
| `step-c50` | 50 | 3000 | 104.9 | 441.5 / 782.4 / 1001.1 | 0 {'200': 3000} | 3000/3000 (100.0 %) | — |
| `step-c100` | 100 | 6000 | 101.5 | 902.7 / 1663.0 / 1900.3 | 0 {'200': 6000} | 6000/6000 (100.0 %) | — |
| `step-c200` | 200 | 12000 | 61.6 | 1410.6 / 4134.7 / 8104.5 | 1834 {'0': 1834, '200': 10166} | 10166/10166 (100.0 %) | — |
| `warmup-1cpu` | 10 | 300 | 89.5 | 70.5 / 190.0 / 1298.5 | 0 {'200': 300} | 300/300 (100.0 %) | — |
| `gw1cpu-c50` | 50 | 3000 | 132.1 | 320.9 / 635.6 / 853.6 | 0 {'200': 3000} | 3000/3000 (100.0 %) | — |
| `gw1cpu-c100` | 100 | 6000 | 129.2 | 748.9 / 1145.7 / 1317.8 | 0 {'200': 6000} | 6000/6000 (100.0 %) | — |
| `sustained-c50-10min` | 50 | 59484 | 99.1 | 455.4 / 899.3 / 1047.4 | 0 {'200': 59484} | 3000/3000 (100.0 %) | — |
| `fault-core-10pct` | 20 | 1500 | 92.9 | 187.4 / 396.9 / 811.9 | 162 {'200': 1338, '500': 60, '502': 102} | 1500/1500 (100.0 %) | — |
| `bus-info` | 20 | 1000 | 55.6 | 188.2 / 396.0 / 2018.6 | 40 {'200': 960, '500': 13, '502': 27} | 0/1000 (0.0 %) | bus+core+modern: 1000 |
| `bus-outage` | 20 | 11730 | 97.6 | 189.9 / 397.5 / 496.0 | 3909 {'200': 7821, '500': 1303, '502': 2606} | 7821/11730 (66.68 %) | bus+core+modern: 3909 |
| `no-correlator-c20` | 20 | 1500 | 95.5 | 189.5 / 397.3 / 404.7 | 0 {'200': 1500} | no se verifica | — |

## Manual frente al modelo (`manual-vs-model.json`, después de la carga sostenida)
Logs: gateway 12.6 MB, bus 33.8 MB, core 7.6 MB, moderno 41.0 MB
(96,970 transacciones). Muestra de 30 transacciones.
- Manual (4 `grep` en 4 formatos): mediana 36.36 ms, p95 61.53 ms · cadenas completas 30/30
- Modelo (1 consulta `/trace`): mediana 1.76 ms, p95 6.79 ms · trazas completas 16/30
- Mediana 20.7× más rápida (tiempo de máquina; el de una persona incluye entrar a 4 sistemas). En local, con ~4 000 transacciones: 12,8 ms frente a 1,0 ms (12,8×).

## Datos de CloudWatch (no están en los JSON)
- **Cuello de botella:** con el gateway a 0,5 CPU, su CPU máxima por minuto fue 50–52 % (su tope). Con 1 CPU, el gateway quedó en
  45 % promedio / 76 % máximo y el **bus** llegó a 51 % (su tope de 0,5): el cuello de botella pasa al bus.
- **Memoria del correlacionador** en la carga sostenida (máx. por 2 min): 145 → 155 → 167 → 164 → 177 → 190 MB (límite 256 MB). Sin reinicios ni OOM.
- **Caída del bus:** detenido 04:14:46Z, arrancado 04:15:18Z. Errores por ventana de 10 s: 0 hasta 04:14:40, luego 234, 1184, 1114, 1147, 230
  y 0 desde la ventana de 04:15:30. Recuperación ≤ 22 s después del arranque; sin intervención manual.

## Hallazgo: reinicio del correlacionador con logs mayores que su memoria
Tras `no-correlator`, el correlacionador volvió a leer los logs desde el inicio (~437 000 eventos, más que `MAX_EVENTS` = 200 000) y quedó
con **0 trazas completas en memoria** (`/stats`: 35 904 transacciones, 0 completas). Causa: los cuatro archivos avanzan a ritmos distintos
(1 MB por lectura = ~13 000 registros del core pero ~2 400 spans del moderno) y la evicción es por orden de llegada, así que se descartan
eventos recientes de unas capas mientras siguen entrando eventos viejos de otras. En operación continua no ocurre (100 % verificado en la
carga sostenida).

**Corrección (`v0.3.2`):** el grafo descarta el evento más viejo **según su hora**, no el primero que llegó (prueba de regresión
`test_replay_at_uneven_speeds_keeps_recent_traces`). Verificación en AWS con los mismos logs (~437 000 eventos, relectura en 111 s):

| | Antes (código viejo) | Después (`v0.3.2`) |
|---|---|---|
| 1500 transacciones de `no-correlator-c20` | 0/1500 completas (faltan `core+modern`) | **1500/1500** completas |
| `/stats` tras la relectura | 0 de 35 904 en memoria | 48 311 de 53 224 (90,8 %) |
| Memoria del correlacionador | — | 201 MB (límite subido a 384 MB) |

Las 4913 incompletas tras la corrección = 3909 de `bus-outage` + 1000 de `bus-info` (incompletas por diseño) + 4 en el borde de la
ventana de memoria. Evidencia: `correlator-restart-before.json` y `correlator-restart-after.json`.
