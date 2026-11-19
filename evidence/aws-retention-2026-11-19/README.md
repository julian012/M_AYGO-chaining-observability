# Carga sostenida y manual frente al modelo · 19-nov-2026 (`v0.6.2`)

Con la corrección de `/stats` (completitud en caché 30 s) y la muestra dentro de la ventana de memoria del correlacionador.

- **Sostenida 10 min, C=50:** 61855 tx · 103.0 rps · p95 902.6 ms · 0 errores ·
  últimas 3 000: 3000/3000 completas.
- **Ventana de memoria al terminar:** 49810 transacciones en memoria, 49810 completas
  (de 62 255 en el log: 80 %, ≈ 8 min a 103 rps).
- **Manual frente al modelo** (30 transacciones al azar entre las 20 000 más recientes; logs: gateway 8.1 MB,
  bus 21.7 MB, core 4.9 MB, moderno 27.4 MB):
  manual (4 `grep`) mediana **34.0 ms** (p95 40.31) · modelo (`/trace`) mediana
  **1.1 ms** (p95 1.41) · **30.9×** · completas: manual
  30/30, modelo 30/30.
