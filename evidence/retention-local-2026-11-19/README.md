# Retención y consulta histórica · local, 19-nov-2026 (`v0.7.0`)

El correlacionador en línea guarda en memoria una ventana de transacciones recientes (en AWS, ~50 000 ≈ 8 min a 100 rps). Para
históricos, `scripts/replay_history.py` relee los logs archivados con los mismos parsers y reconstruye el grafo completo: el alcance
pasa a depender de la retención de los logs. Datos: 90 s de carga, 31113 transacciones, 5 fuentes
(gateway, bus, core, MFT, moderno). Reconstrucción completa: **2.18 s**, 116.1 MB, 124452 eventos.

Para simular una retención desalineada se conserva solo la parte más reciente del log de una fuente.

| Retención | Trazas completas | % completas por decil de antigüedad (más viejo → más reciente) | Incompletas por capas faltantes |
|---|---|---|---|
| Todas las fuentes con la misma retención | 100.0 % | 100 · 100 · 100 · 100 · 100 · 100 · 100 · 100 · 100 · 100 | — |
| Bus conserva el 50 % más reciente | 49.99 % | 0 · 0 · 0 · 0 · 0 · 99 · 100 · 100 · 100 · 100 | `bus+core+mft+modern`: 15560 |
| MFT conserva el 50 % más reciente | 48.47 % | 0 · 0 · 0 · 0 · 0 · 84 · 100 · 100 · 100 · 100 | `mft`: 16032 |
| Moderno conserva el 30 % más reciente | 30.0 % | 0 · 0 · 0 · 0 · 0 · 0 · 0 · 100 · 100 · 100 | `modern`: 21780 |

## Lectura
- El alcance histórico lo fija **la fuente que menos tiempo guarda**: por encima de su retención, todas las trazas quedan incompletas
  y la capa faltante dice qué dependencia es.
- Regla de gobierno: **alinear la retención** de todas las fuentes que participan en la cadena (y del archivo de los logs), no solo
  la de cada sistema por separado. La plantilla de AWS expira la evidencia en S3 a los 30 días: es una política explícita, no un
  efecto secundario.
