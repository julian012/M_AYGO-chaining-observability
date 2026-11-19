# Arquitectura

## Problema
Un flujo de negocio nace en varios canales (móvil, web, API expuesta a terceros) pero siempre atraviesa el mismo camino:
**Gateway → Bus de integración → Core legacy → servicios modernos**. Cada capa genera **su propio identificador** y ninguna
puede modificarse para propagar uno único. Seguir una transacción exige cruzar IDs manualmente entre sistemas.

## Solución: encadenamiento de IDs (*ID chaining*)
No se intenta unificar el ID. Cada capa ya registra **el ID que recibió y el que generó**; esos pares son eslabones.
El correlacionador lee los logs (solo lectura), arma el grafo `CLI → GW → BUS → CORE → MOD` y deriva un `trace_id`
canónico (SHA-256 truncado a 32 hex, compatible con el formato de trace-id de OpenTelemetry).

```mermaid
flowchart LR
  subgraph Canales
    M[Móvil]:::c
    W[Web]:::c
    P[API tercero]:::c
  end
  M & W -->|"REST/JSON + API key"| GW
  P -->|"SOAP/XML + API key"| GW
  subgraph "Red interna (sin salida a Internet)"
    GW["Gateway<br/>ID: GW-…"] -->|"SOAP · wsa:MessageID"| BUS["Bus ESB<br/>ID: BUS-…"]
    BUS -->|"trama por socket TCP"| CORE["Core legacy<br/>ID: CORE-…"]
    CORE -->|"archivo plano, lote cada 5 s"| FTP[("FTP<br/>/inbox")]
    FTP -->|"descarga y procesa"| MOD["Servicio moderno<br/>ID: MOD-…"]
  end
  GW -.access log clave=valor.-> V1[(vol gateway)]
  BUS -.texto INFO/DEBUG.-> V2[(vol bus)]
  CORE -.registro ancho fijo.-> V3[(vol core)]
  MOD -.spans OTel.-> V4[(vol modern)]
  V1 & V2 & V3 & V4 -->|solo lectura| COR["Correlacionador<br/>4 parsers + grafo de IDs"]
  COR -->|/trace/{cualquier ID}, /stats, /metrics<br/>Bearer token, loopback| U[Operador]
  classDef c fill:#eef,stroke:#88a
```

Recorrido paso a paso, mensajes y logs reales de cada capa: [flujo-y-telemetria.md](flujo-y-telemetria.md).

## Componentes
| Componente | Rol real simulado | ID propio (padre) | Entrada | Telemetría |
|---|---|---|---|---|
| `gateway` | Gateway de seguridad/API tipo appliance | `GW-` (`CLI-`) | REST/JSON y SOAP | Access log `clave=valor` |
| `bus` | Bus de integración (ESB) | `BUS-` (`GW-`) | SOAP | Texto con niveles; el par en DEBUG |
| `core` | Core legacy (COBOL, tipo Stratus) no modificable | `CORE-` (`BUS-`, o ninguno en modo tracenumber) | Trama de ancho fijo por TCP | Registro de ancho fijo; novedades en archivo plano |
| `ftp` | Transferencia de archivos (stand-in) | — | Archivos planos del core | — |
| `modern` | Servicio posterior al core | `MOD-` (`CORE-`) | Archivo plano por FTP (en lotes, asíncrono) | Spans OpenTelemetry |
| `correlator` | Plataforma de observabilidad | — | — | Lee las 4 fuentes |

Todos los nombres son genéricos; los datos son sintéticos.

## Propiedades del correlacionador
- Se puede consultar por **cualquier ID** de cualquier capa.
- Independiente del orden de llegada (los hijos terminan antes que sus padres) y tolerante a duplicados.
- Una cadena rota se reporta como **traza incompleta** con las capas faltantes; nunca se inventan enlaces.
- Un fallo del core (`status != 00`) no cuenta como capa faltante de `modern` (no se invoca por diseño).
- Nunca une por hilo ni por tiempo: si una fuente no deja el par de IDs (p. ej. el bus en INFO), la traza se reporta incompleta.
- Memoria acotada (`MAX_EVENTS`): al llenarse descarta el evento más viejo **según su hora**, no el primero que llegó. Así, al reiniciar y releer logs a ritmos distintos, las transacciones recientes quedan completas (hallazgo de las pruebas en AWS).

## Despliegue
- **Local:** `docker compose` (ver README).
- **AWS (Learner Lab):** una EC2 con Docker Compose + bucket cifrado (`infra/template.yaml`). Ver [adr/0002](adr/0002-ec2-compose-vs-eks.md).

## Evolución prevista
1. Exportar los spans reconstruidos a OTLP (OTel Collector → Tempo/Jaeger) y visualizar en Grafana.
2. Reemplazar los stand-ins por productos reales (appliance de integración, ESB, core en CICS) de desarrollo (trabajo futuro).
3. mTLS entre servicios internos (hoy: red aislada + token compartido).
