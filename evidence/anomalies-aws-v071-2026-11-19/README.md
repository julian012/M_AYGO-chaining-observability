# Anomalías en AWS con alarma de CloudWatch · 19-nov-2026 (`v0.7.1`)

EC2 `t3.medium`, 7 contenedores, carga continua desde la misma instancia (8 hilos). El experimento corrió dentro de la instancia
(`nohup`). Detector cada 2 s; el monitor publica `ActiveAlerts` cada 10 s; alarma `obsproto-detector-alerta-activa` con periodos de
10 s. Toda alerta disparada durante una línea base cuenta como falsa alarma.

| Anomalía | Detección (detector) | Alertas (regla:capa) | Alarma de CloudWatch (hora -05) | Lo que vio el cliente | Trazas del periodo al final | Falsas alarmas |
|---|---|---|---|---|---|---|
| Core lento (+300 ms) | **3.6 s** | `latency:core` 3.6 s | 15:53:01 → ALARM 15:53:26 (+25 s) | 0.0 % errores · p50 77.1 → 339.9 ms | 100.0 % | 0 |
| MFT detenido | **19.0 s** | `chain_break:mft` 19.0 s; `source_silent:modern` 25.3 s; `source_silent:mft` 25.3 s | 15:56:03 → ALARM 15:56:26 (+23 s) | 0.0 % errores · p50 80.8 → 79.7 ms | 100.0 % | 0 |
| Bus pasa a INFO | **5.6 s** | `chain_break:bus` 5.6 s | 15:58:37 → ALARM 15:58:56 (+19 s) | 0.0 % errores · p50 81.8 → 81.4 ms | 0.0 % | 0 |
| Log del bus ilegible | **1.4 s** | `source_silent:bus` 1.4 s; `chain_break:bus` 5.7 s | 16:01:19 → ALARM 16:01:46 (+27 s) | 0.0 % errores · p50 85.2 → 87.3 ms | 100.0 % | 0 |

La alarma pasó a ALARM **exactamente cuatro veces** (una por anomalía) y volvió a OK en las cuatro; ninguna falsa alarma. Frente a la
corrida con `v0.7.0` (`evidence/anomalies-aws-2026-11-19/`) desaparece la falsa alarma de arranque. Retraso de la alarma frente al
detector: 19–27 s (publicación cada 10 s + evaluación de 10 s).
