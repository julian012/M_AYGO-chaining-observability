# Unión bus → core cuando el core no recibe el ID del bus (tipo Stratus) · local, 19-nov-2026

**Caso real:** el core asigna a cada movimiento un consecutivo que nunca se repite (*tracenumber*) y lo devuelve en la respuesta;
no recibe el ID del bus. El bus no tiene logs en DEBUG: deja una línea INFO por mensaje (`BUS_INFO_PROFILE=summary`) y ahí el
tracenumber queda según la política `BUS_TRACE_LOG`. El único lugar donde puede quedar el eslabón bus → core es esa línea.

**Montaje:** Docker local, `CORE_LINK=tracenumber`, bus en INFO, 200 transacciones de calentamiento + 1500 (C=20) por política,
pila limpia en cada una. Ventana de la unión por máscara: ±5 s. Las uniones se evalúan contra la verdad de referencia
(`truth.log`, que escribe el bus y el correlacionador nunca lee) con `scripts/eval_core_link.py`.
Orquestador: `scripts/core_link_experiment.py`.

| Política | Cómo queda en el log del bus | rps | Trazas completas (1500) | Uniones correctas | Uniones **falsas** | Ambiguas | No unidas |
|---|---|---|---|---|---|---|---|
| `clear` | `trace=795070876254` | 373.9 | 1500/1500 (100.0 %) | 1700/1700 | **0** | 0 | 0 |
| `token` | `trace=tok_df117f57b2ed0e18` | 391.6 | 1500/1500 (100.0 %) | 1700/1700 | **0** | 0 | 0 |
| `mask4` | `trace=********6178` | 389.2 | 1500/1500 (100.0 %) | 1700/1700 | **0** | 0 | 0 |
| `mask3` | `trace=*********081` | 384.6 | 300/1500 (20.0 %) | 300/1700 | **0** | 1400 | 1400 |
| `mask2` | `trace=**********63` | 397.9 | 0/1500 (0.0 %) | 0/1700 | **0** | 1700 | 1700 |
| `none` | `(no se registra)` | 418.9 | 0/1500 (0.0 %) | 0/1700 | **0** | 0 | 1700 |

## Lectura
- **En claro y token: 100 %, deterministas.** El token (HMAC-SHA256 con llave) une igual que el valor en claro **sin exponerlo**
  en el log del bus: el correlacionador calcula el mismo token sobre el registro del core.
- **Máscara: depende de cuántos movimientos caben en la ventana.** Con *v* dígitos visibles hay 10^v terminaciones posibles; como
  el tracenumber es consecutivo, la terminación es única mientras en la ventana haya menos de 10^v movimientos.
  Aquí, ~390 movimientos/s × 10 s de ventana ≈ 3 900: con 4 dígitos (10 000) todo es único → 100 %; con 3 (1 000) casi todo es
  ambiguo → 20 %; con 2 (100) → 0 %.
- **0 uniones falsas en todas las políticas.** Ante dos o más candidatos el correlacionador no adivina: reporta la traza incompleta.
- **Sin registro: 0 %.** El eslabón solo existía en el log del bus.

## Implicaciones
- **Gobernanza:** si el bus ofusca el tracenumber, debe **tokenizar** (no enmascarar); la llave se custodia como un secreto y la
  comparte solo la plataforma de observabilidad.
- **Seguridad (efecto colateral):** enmascarar un consecutivo protege menos de lo que parece. Con 4 dígitos visibles y la hora,
  el movimiento se identifica sin ambigüedad (este experimento lo hace en el 100 % de los casos).
- **Escala:** en un banco el consecutivo lo consumen todos los canales, no solo este flujo. A ~1 000 movimientos/s y ±5 s de
  ventana (10 000) hasta la máscara de 4 dígitos se vuelve ambigua; el token no depende de la carga.
