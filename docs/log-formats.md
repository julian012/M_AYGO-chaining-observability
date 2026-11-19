# Formatos de mensaje y de log

Cada capa usa el protocolo y el formato de log que tendría en una integración empresarial real (ver
[ADR-0005](adr/0005-heterogeneous-sources.md)). Explicación visual con ejemplos reales: [flujo-y-telemetria.md](flujo-y-telemetria.md).

## 1. Mensajes entre capas (`src/obsproto/wire.py`)

| Salto | Protocolo | ID de la capa anterior |
|---|---|---|
| Móvil / web → gateway | REST/JSON, `POST /v1/transactions` | Header `X-Client-Id` |
| Tercero → gateway | SOAP 1.1, `POST /soap/v1/transactions` | `soapenv:Header/txn:ClientRequestId` |
| Gateway → bus | SOAP 1.1, `POST /ws/TransactionService` | `soapenv:Header/wsa:MessageID` |
| Bus → core | Trama por **socket TCP** (`core:9000`), cada mensaje precedido de su longitud en 4 dígitos ASCII | Posiciones 4–23 |
| Core → moderno | **Archivo plano** de novedades por **FTP** (`/inbox` → `/procesados`), en lotes | Posiciones 2–21 de cada registro de detalle |

Namespace de la transacción: `urn:obsproto:txn:v1`. Cuerpo: `Transaction` con exactamente `Type`, `Account` y `AmountCents`.

**Trama de solicitud (60 caracteres)**

| Campo | Pos. | Ancho | Contenido |
|---|---|---|---|
| `mti` | 0–3 | 4 | `0200` |
| `bus_id` | 4–23 | 20 | `BUS-<12 hex>`, alineado a la izquierda |
| `txn` | 24–27 | 4 | `TRF `, `PAY `, `BAL ` |
| `account` | 28–47 | 20 | PIC X(20): dígitos alineados a la izquierda, relleno con espacios |
| `amount` | 48–59 | 12 | centavos, ceros a la izquierda |

**Trama de respuesta (38 caracteres):** `mti` = `0210` (4) · `bus_id` eco (20, vacío si no se envió) · `rc` (2): `00` OK,
`91` fallo · `tracenumber` (12): consecutivo del movimiento en el core, ceros si no aplica.
El resultado del core va en `rc`; la respuesta HTTP es siempre 200 si la trama es válida.

**Modo `CORE_LINK=tracenumber` (core de tramas, [ADR-0006](adr/0006-core-tracenumber-link.md)):** la solicitud lleva el
campo `bus_id` vacío. El core asigna un consecutivo de 12 dígitos que nunca se repite, lo usa como su ID (`CORE-<tracenumber>`),
lo registra **sin ID recibido** y lo devuelve en la respuesta.

**Transporte TCP de las tramas:** `<longitud en 4 dígitos ASCII><trama>`, por ejemplo `0060` + 60 caracteres y `0038` + 38 en la
respuesta. Una conexión puede llevar varios mensajes seguidos. Cabecera inválida (no numérica, 0 o más de 1024) ⇒ se cierra la conexión.

**Archivo plano de novedades (core → moderno, 63 caracteres por línea):** `NOV_<nodo>_<YYYYMMDDHHMMSSmmm>.txt`

| Registro | Contenido |
|---|---|
| `00` cabecera | fecha-hora de generación (14) + nodo (8) |
| `01` detalle | ID del core (20, pos. 2–21) + txn (4) + monto en centavos (12) + estado (2) + hora del movimiento (17) |
| `99` cola | cantidad de registros de detalle (8): si no cuadra, el archivo no se procesa (`.err`) |

Solo los movimientos exitosos (`00`) generan novedad. El core sube el archivo como `.tmp` y lo renombra a `.txt` al terminar.

## 2. Logs, uno por tipo de fuente

### Gateway: access log `clave=valor`
```
2026-11-18T14:42:28.676Z INFO  gateway tid=GW-7c22b329b483 in_id=CLI-58860194592d ch=partner proto=SOAP status=200 upstream=200 dur_ms=14
```
`tid` = ID propio · `in_id` = ID recibido · `ch` se deriva de la API key (no de un header del cliente) ·
`status` = lo que recibió el cliente · `upstream` = lo que respondió el bus (503 = timeout o caída).

### Bus: texto con niveles
```
<ts> INFO  [ESB.TransactionRoute] thread=T30496 received SOAP Submit upstream=GW-…
<ts> DEBUG [ESB.TransactionRoute] thread=T30496 mapped msgId=BUS-… upstream=GW-… out=CORE.TRAMA
<ts> INFO  [ESB.TransactionRoute] thread=T30496 reply msgId=BUS-… rc=00 dur_ms=14
```
El par completo solo está en la línea **DEBUG** `mapped`. `rc`: `00` OK, `91` fallo del core, `TO` timeout o caída,
`XX` trama de respuesta inválida (los no `00` se registran como `ERROR`). Nivel con `BUS_LOG_LEVEL` (por defecto `DEBUG`).

**Perfil `BUS_INFO_PROFILE=summary`** (sin DEBUG, como en producción): una sola línea INFO por mensaje con el par completo y,
con un core de tramas, el tracenumber que devolvió según `BUS_TRACE_LOG`:
```
<ts> INFO  [ESB.TransactionRoute] thread=T43712 reply msgId=BUS-… upstream=GW-… rc=00 trace=795070117166 dur_ms=32       (clear)
                                                                              … rc=00 trace=tok_11f558a938873f72 dur_ms=37  (token)
                                                                              … rc=00 trace=********6178 dur_ms=29          (mask, 4 dígitos)
                                                                              … rc=00 dur_ms=31                             (none)
```
`token` = `tok_` + 16 hex de HMAC-SHA256(`TRACE_TOKEN_KEY`, tracenumber). El bus escribe además `truth.log` (par real
bus → tracenumber) **solo para evaluar** uniones falsas; el correlacionador nunca lo lee.

### Core legacy: ancho fijo (81 caracteres + `\n`)
Definido una sola vez en `CORE_LAYOUT` (`src/obsproto/common.py`); el simulador y el parser comparten la tupla.

| Campo | Pos. (0-based) | Ancho | Contenido |
|---|---|---|---|
| `ts` | 0–16 | 17 | `YYYYMMDDHHMMSSmmm` UTC |
| `core_id` | 17–36 | 20 | `CORE-<12 hex>` alineado a la izquierda |
| `parent_id` | 37–56 | 20 | `BUS-<12 hex>` alineado a la izquierda; **vacío** con `CORE_LINK=tracenumber` |
| `txn` | 57–60 | 4 | `TRF `, `PAY `, `BAL ` |
| `amount` | 61–72 | 12 | centavos, ceros a la izquierda |
| `status` | 73–74 | 2 | `00` OK · `91` fallo simulado |
| `dur_ms` | 75–80 | 6 | duración, ceros a la izquierda |

```
20261118144228675CORE-c0f0ac5c0476   BUS-3c414414810d    BAL 00000000735600000014
```
> El log del core **no contiene número de cuenta** (minimización de datos): solo lo necesario para correlacionar.

### Servicio moderno: spans OpenTelemetry (JSON, una línea por span)
```json
{"resource":{"service.name":"modern-notifications"},"traceId":"ba6b…","spanId":"7613…","parentSpanId":"",
 "name":"process NOVEDAD","kind":"SPAN_KIND_SERVER","startTimeUnixNano":…,"endTimeUnixNano":…,
 "status":{"code":"STATUS_CODE_OK"},"attributes":{"app.id":"MOD-…","upstream.id":"CORE-…", …}}
```
Como el core no propaga `traceparent`, cada span abre una traza OTel nueva. El eslabón está en los atributos
`upstream.id` → `app.id`; el correlacionador conserva el `traceId` como `otelTraceId` en la traza reconstruida.

### MFT: sesión y archivo ([ADR-0008](adr/0008-mft-file-transfer-link.md))
```
<ts> INFO  [MFT] job=NOVEDADES_CORE01 run=RUN-… session=SSH-… event=ssh.connect host=ftp user=mft
<ts> INFO  [MFT] job=… run=… session=… transfer=MFT-ef31a5775335 event=file.copy src=/outbox/NOV_CORE01_20261119_144841260-144842403.txt dst=/inbox/… bytes=37120 sha256=ec1e9e7fb2ac3a01
<ts> INFO  [MFT] job=… run=… session=… transfer=MFT-ef31a5775335 event=file.move src=/outbox/NOV_… status=OK
<ts> INFO  [MFT] job=… run=… session=… event=ssh.disconnect files=1
```
El MFT no abre el archivo. El eslabón con cada movimiento del core sale del **nombre**: `NOV_<nodo>_<YYYYMMDD>_<inicio>-<fin>.txt`
(hora del primer y del último movimiento, ms, UTC). Un movimiento exitoso del core se asocia a la única transferencia cuyo
rango contiene su hora; con dos o más candidatos no se une.

## 3. Reglas de validación (seguridad)
- **Mensajes:** cuerpo ≤ 4 KB. SOAP: se rechaza `<!DOCTYPE`/`<!ENTITY` antes de parsear (XXE, expansión de entidades),
  raíz `soapenv:Envelope` y campos exactos. Trama: longitud exacta, ASCII, MTI conocido, ID `BUS-`, campos numéricos.
  Después, en todas las capas, `validate_txn` (lista blanca de campos, tipos y rangos).
- **Logs:** cada línea es entrada no confiable. Longitud exacta (core) o ≤ 1024; regex anclada de la línea completa
  (gateway, bus); ID propio y padre con `^(CLI|GW|BUS|CORE|MOD)-[0-9a-f]{12}\Z`, y el prefijo de cada uno debe
  corresponder a la capa (`bus` ⇒ `BUS-` con padre `GW-`), así que un log manipulado no puede saltarse capas.
- Líneas inválidas se cuentan (`malformedLines`) y se descartan; nunca detienen al correlacionador.
- El parser del bus **no** une por hilo ni por tiempo: sin la línea DEBUG la respuesta queda sin padre (`busUnlinkedReplies`).
