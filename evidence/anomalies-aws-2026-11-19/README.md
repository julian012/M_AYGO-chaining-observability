# Anomalías en AWS con alarma de CloudWatch · 19-nov-2026 (`v0.7.0`)

EC2 `t3.medium`, 7 contenedores, carga continua desde la misma instancia (8 hilos). El experimento corrió dentro de la instancia
(`nohup`), independiente del equipo del operador. Detector cada 2 s; el monitor publica `ActiveAlerts` cada 10 s y la alarma
`obsproto-detector-alerta-activa` (CloudFormation) se evalúa con periodos de 10 s.

| Anomalía | Detección (detector) | Alertas (regla:capa) | Lo que vio el cliente | Trazas del periodo al final |
|---|---|---|---|---|
| Core lento (+300 ms) | **4.9 s** | `latency:core` 4.9 s | 0.0 % errores · p50 75.3 → 339.0 ms | 100.0 % |
| MFT detenido | **15.6 s** | `chain_break:mft` 15.6 s; `source_silent:modern` 22.0 s; `source_silent:mft` 26.3 s | 0.0 % errores · p50 79.9 → 76.4 ms | 100.0 % |
| Bus pasa a INFO | **6.1 s** | `chain_break:bus` 6.1 s | 0.0 % errores · p50 80.1 → 78.9 ms | 0.0 % |
| Log del bus ilegible | **2.4 s** | `source_silent:bus` 2.4 s; `chain_break:bus` 4.5 s | 0.0 % errores · p50 79.4 → 78.5 ms | 100.0 % |

## Alarma de CloudWatch (historial de estados)
| Hora (-05) | Transición | Qué era | Retraso frente al detector |
|---|---|---|---|
| 11:54:36 | OK → ALARM (10 s) | **Falsa alarma de arranque** (ver abajo) | — |
| 11:55:36 | OK → ALARM | Core lento (detector 11:55:16) | 20 s |
| 11:58:36 | OK → ALARM | MFT detenido (detector 11:58:14) | 22 s |
| 12:01:16 | OK → ALARM | Bus en INFO (detector 12:00:51) | 25 s |
| 12:03:56 | OK → ALARM | Log del bus ilegible (detector 12:03:35) | 21 s |

La alarma de la plataforma agrega 20–25 s sobre el detector: el monitor publica cada 10 s y la alarma evalúa periodos de 10 s.
Todas volvieron a OK al revertir la anomalía.

## Falsa alarma de arranque y corrección
A las 11:54:14 (3 s después de iniciar la carga) el detector marcó `source_silent` en el MFT y en el moderno ("sin líneas hace 62 s"):
la pila acababa de arrancar sin tráfico y el silencio se contaba desde el arranque del correlacionador, no desde que empezó el tráfico.
Además, el experimento no contaba como falsas las alertas disparadas *durante* la línea base, por eso reportó "0". Ambas cosas se
corrigieron en `v0.7.1` (detector y experimento) y se verificaron en local (`evidence/anomalies-local-2026-11-19/`). La sesión del
Learner Lab terminó antes de repetir la corrida en AWS con `v0.7.1`.

`alerts-history.json`: historial completo del detector en esta corrida.
