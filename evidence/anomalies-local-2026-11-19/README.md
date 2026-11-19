# Anomalías: detección con la telemetría centralizada · local, 19-nov-2026 (`v0.7.1`)

Carga continua (8 hilos, ~290 rps) en Docker local con 7 contenedores. Por anomalía: 60 s de línea base, 60 s de anomalía
inyectada en caliente, reversión y 45 s de recuperación. Detector del correlacionador cada 2 s. Orquestador:
`scripts/anomaly_experiment.py`; toda alerta disparada durante la línea base cuenta como falsa alarma. Corrida con `caffeinate`
(una corrida anterior se descartó porque el equipo entró en reposo; otra, con `v0.7.0`, no contaba bien las falsas alarmas).

| Anomalía | Detección | Alertas (regla:capa) | Lo que vio el cliente | Trazas del periodo completas al final | Falsas alarmas en la línea base |
|---|---|---|---|---|---|
| Core lento (+300 ms) | **6.1 s** | `latency:core` a los 6.1 s (resuelta 12.5 s tras revertir) | 0.0 % errores · p50 27.8 → 333.0 ms | 100.0 % | 0 |
| MFT detenido | **16.5 s** | `chain_break:mft` a los 16.5 s (resuelta 8.4 s tras revertir); `source_silent:mft` a los 20.5 s (resuelta 6.3 s tras revertir); `source_silent:modern` a los 22.6 s (resuelta 8.4 s tras revertir) | 0.0 % errores · p50 27.1 → 27.3 ms | 100.0 % | 0 |
| Bus pasa a INFO (configuración) | **5.0 s** | `chain_break:bus` a los 5.0 s (resuelta 7.2 s tras revertir) | 0.0 % errores · p50 27.7 → 27.4 ms | 0.0 % | 0 |
| Log del bus ilegible (fuente deja de transmitir) | **1.2 s** | `source_silent:bus` a los 1.2 s (resuelta 3.6 s tras revertir); `chain_break:bus` a los 5.3 s (resuelta 5.7 s tras revertir) | 0.0 % errores · p50 27.5 → 27.4 ms | 100.0 % | 0 |

## Lectura
- **Las dos caras.** En el MFT detenido, el bus en INFO y el log ilegible el negocio no vio nada (0 % de errores, latencia normal);
  el detector los señaló en 1,2 a 16,5 s nombrando la capa.
- **Localización.** En el core lento el cliente solo vio lentitud; el detector señaló al core y marcó gateway y bus como afectadas.
- **Fuente caída frente a dato no registrado.** Al volver el MFT o el log del bus, el correlacionador se puso al día y las trazas del
  periodo quedaron 100 % completas; con el bus en INFO el dato nunca se escribió (0 %).
- Sin falsas alarmas en las líneas base; todas las alertas se resolvieron solas al revertir.
