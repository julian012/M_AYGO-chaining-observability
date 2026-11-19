# ADR-0005: Protocolos y fuentes de telemetría heterogéneos

**Estado:** Aceptada · 18-nov-2026

## Contexto
La primera versión del prototipo (v0.1.0) usaba REST/JSON en todos los saltos y un log JSON con `id` y `parent_id`
en el gateway, el bus y el servicio moderno. Solo el core tenía un formato realista (ancho fijo). Ese es el mejor
caso posible: hace que la correlación parezca más fácil de lo que es en una integración empresarial real, donde:
- los terceros se integran por **SOAP/XML** y un gateway de integración cerrado (no programable en Python) transforma a SOAP;
- el ESB le habla al core por **tramas de ancho fijo**;
- cada producto registra a su manera: access logs de texto, logs con niveles INFO/DEBUG, registros posicionales
  y, en los servicios nuevos, **spans OpenTelemetry** que no comparten `traceId` con nadie porque el core no propaga contexto.

## Decisión
Cada capa simula el protocolo y la telemetría que tendría en la realidad (detalle en `docs/log-formats.md`):

| Capa | Entrada | ID recibido en | Telemetría |
|---|---|---|---|
| Gateway | REST/JSON (móvil, web) y SOAP (tercero) | `X-Client-Id` / `ClientRequestId` | Access log `clave=valor` |
| Bus | SOAP | `wsa:MessageID` | Texto con niveles; el par solo en DEBUG |
| Core | Trama de 60 caracteres | Posiciones 4–23 | Registro de ancho fijo |
| Moderno | REST/JSON | `X-Upstream-Id` | Spans OTel (atributos `app.id`, `upstream.id`) |

El correlacionador tiene **un parser por tipo de fuente**. El del bus tiene estado (une la línea DEBUG con la de
respuesta) y **no** intenta unir por hilo ni por tiempo.

## Alternativas
- *Mantener JSON homogéneo:* más simple, pero no responde la pregunta del proyecto (múltiples fuentes).
- *Usar los productos reales (gateway de integración, ESB y core comerciales):* fuera del presupuesto y del Learner Lab; queda como trabajo futuro.
- *Unir el bus por hilo/tiempo cuando falta DEBUG:* aumentaría el % de trazas, pero con enlaces falsos bajo concurrencia.

## Consecuencias
- El nivel de log del bus (`BUS_LOG_LEVEL`) se vuelve una variable del experimento: en INFO la cadena se rompe (0 %
  de trazas completas en la prueba local), lo que respalda una **regla de gobernanza**: el par *ID recibido → ID propio*
  debe quedar en una línea con nivel activo en producción.
- Se puede comparar el costo de REST frente a SOAP (`byProtocol` en el generador).
- Más superficie de ataque (XML, tramas): amenazas 21–23 en `threat-model.md`, con pruebas en `tests/test_wire.py`.
- Sigue sin dependencias de runtime: `xml.etree` de la biblioteca estándar, con DTD y entidades rechazadas antes de parsear.
