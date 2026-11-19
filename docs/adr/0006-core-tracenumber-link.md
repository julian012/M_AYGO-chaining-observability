# ADR-0006: Eslabón bus → core cuando el core no recibe el ID del bus (tracenumber)

**Estado:** Aceptada · 19-nov-2026

## Contexto
En el caso real, el core (tipo Stratus) no recibe el ID del bus: asigna a cada movimiento un consecutivo que nunca se repite
(*tracenumber*) y lo devuelve en la respuesta. El bus no tiene logs en DEBUG en producción y, en INFO, ese valor queda ofuscado.
Así, el eslabón bus → core no existe en el log del core y solo puede quedar en la línea de respuesta del bus.

## Decisión
- `CORE_LINK=tracenumber`: la trama ya no lleva el ID del bus; el core registra su tracenumber (`CORE-<12 dígitos>`) sin padre y lo
  devuelve en la respuesta (`TRAMA_RSP_LAYOUT`, 38 caracteres).
- `BUS_INFO_PROFILE=summary`: una línea INFO por mensaje con `msgId`, `upstream`, `rc` y `trace=` según `BUS_TRACE_LOG`
  (`clear` · `token` HMAC-SHA256 con `TRACE_TOKEN_KEY` · `mask` con N dígitos visibles · `none`).
- El correlacionador (`correlator/corelink.py`) une por igualdad (claro), por token recalculado sobre el registro del core, o por
  máscara + hora **solo con un único candidato** en la ventana. Nunca adivina: con 2+ candidatos la traza queda incompleta.
- Se agrega el eslabón al grafo con `IdGraph.link` (solo a un hijo sin padre; nunca reemplaza uno existente).
- Evaluación con verdad de referencia (`truth.log` del bus, que el correlacionador no lee) para medir uniones falsas.

## Alternativas
- *Leer la tabla de la base de datos donde se guarda el tracenumber* como quinta fuente (réplica de solo lectura): determinista si
  la tabla guarda también el ID del bus o del canal; queda como trabajo futuro.
- *Unir por orden del consecutivo:* bajo concurrencia el bus no recibe las respuestas en el orden de asignación. Descartada.
- *Unir por hora y monto sin dígitos del tracenumber:* inferencia con riesgo de enlaces falsos. Descartada.

## Consecuencias
- Resultado local (`evidence/core-link-local-2026-11-19`): claro y token 100 %; máscara 4 dígitos 100 %, 3 dígitos 20 %,
  2 dígitos 0 % (ambiguas); sin registro 0 %; **0 uniones falsas en todas**.
- Regla de gobernanza: si se ofusca el tracenumber, **tokenizar** con llave custodiada; enmascarar depende de la carga.
- Nuevo secreto: `TRACE_TOKEN_KEY` (bus y correlacionador). Quien tenga la llave puede recalcular tokens de tracenumbers
  candidatos, por eso se trata como secreto (riesgo residual en el modelo de amenazas).
