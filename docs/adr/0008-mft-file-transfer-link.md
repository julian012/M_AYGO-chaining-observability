# ADR-0008: Eslabón MFT y nombre de archivo con rango de horas

**Estado:** Aceptada · 19-nov-2026

## Contexto
En integraciones financieras, el archivo plano del core no llega solo al sistema posterior: una plataforma de transferencia gestionada (MFT, tipo
manejador de archivos) se conecta por SSH/SFTP, lo copia y lo mueve a otro destino, y deja un log de sesión y de archivo. El MFT no abre el
archivo: solo conoce su nombre, tamaño y hash. El nombre suele ser *diciente*: dice el origen, la fecha y el rango de horas.

## Decisión
- El core deja el archivo en `/outbox` con el nombre `NOV_<nodo>_<YYYYMMDD>_<HHMMSSmmm>-<HHMMSSmmm>.txt` (primer y último
  movimiento del lote, UTC; un lote no cruza la medianoche).
- Servicio `mft` (stand-in): cada `MFT_POLL_S` (3 s) abre una sesión, copia cada `.txt` de `/outbox` a `/inbox` (como `.tmp`,
  verifica el hash, renombra) y borra el origen. Log con `job`, `run`, `session`, `transfer=MFT-…` y eventos `ssh.connect`,
  `file.copy` (bytes, sha256), `file.move`, `ssh.disconnect`.
- El correlacionador (`correlator/filelink.py`) arma un índice de transferencias por el rango del nombre y, para cada movimiento
  exitoso del core, agrega a la traza el span `mft` cuya ventana contiene la hora del movimiento. **Solo con un candidato único**;
  con rangos solapados (p. ej. dos nodos, porque el registro del core no dice su nodo) no se une. Con `MFT_ENABLED=1` la traza
  completa exige la capa `mft`.

## Alternativas
- *Que el MFT abra el archivo y registre los IDs:* más preciso, pero una MFT real no lo hace y el log crecería por registro.
- *Unir por hora de transferencia:* el MFT mueve el archivo segundos o minutos después; sin el rango del nombre sería inferencia.
- *SFTP real:* la biblioteca estándar no trae SSH. El stand-in mueve por el FTP interno y reproduce el log de sesión SSH/SFTP de
  una MFT, que es lo único que usa el correlacionador. Con un producto real solo cambia el parser del log.

## Consecuencias
- Traza de cinco capas: `gateway → bus → core → mft → moderno`, con el tiempo del tramo asíncrono separado en partes
  (lote del core, job del MFT, revisión del moderno). Local: 600/600 completas, 496 rps; traza completa en mediana 7,5 s
  después de la respuesta (máx. 8,2 s; teórico ≈ 5 + 3 + 2 s).
- Regla de gobernanza adicional: **el nombre del archivo es un contrato**. Si no dice nodo, fecha y rango, el eslabón del MFT
  no se puede ubicar sin abrir el archivo.
- Riesgo: si dos nodos generan rangos solapados, los movimientos de ese tramo quedan sin MFT (se reporta, no se adivina).
  Mejora: que el registro del core incluya el nodo.
