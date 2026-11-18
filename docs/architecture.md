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
  M & W & P -->|HTTPS + API key| GW
  subgraph "Red interna (sin salida a Internet)"
    GW["Gateway<br/>ID: GW-…"] -->|X-Upstream-Id| BUS["Bus<br/>ID: BUS-…"]
    BUS --> CORE["Core legacy<br/>ID: CORE-…<br/>log ancho fijo"]
    CORE --> MOD["Servicio moderno<br/>ID: MOD-…"]
  end
  GW -.log JSON.-> V1[(vol gateway)]
  BUS -.log JSON.-> V2[(vol bus)]
  CORE -.log ancho fijo.-> V3[(vol core)]
  MOD -.log JSON.-> V4[(vol modern)]
  V1 & V2 & V3 & V4 -->|solo lectura| COR["Correlacionador<br/>parsers + grafo de IDs"]
  COR -->|/trace/{cualquier ID}, /stats, /metrics<br/>Bearer token, loopback| U[Operador]
  classDef c fill:#eef,stroke:#88a
```

## Componentes
| Componente | Rol real simulado | ID propio | Log |
|---|---|---|---|
| `gateway` | Gateway de seguridad/API | `GW-` (padre: `CLI-`, del cliente) | JSON |
| `bus` | Bus de integración (ESB) | `BUS-` (padre: `GW-`) | JSON |
| `core` | Core legacy no modificable | `CORE-` (padre: `BUS-`) | **Texto de ancho fijo** |
| `modern` | Servicio posterior al core | `MOD-` (padre: `CORE-`) | JSON |
| `correlator` | Plataforma de observabilidad | — | — (lee los 4 logs) |

Todos los nombres son genéricos; los datos son sintéticos.

## Propiedades del correlacionador
- Se puede consultar por **cualquier ID** de cualquier capa.
- Independiente del orden de llegada (los hijos terminan antes que sus padres) y tolerante a duplicados.
- Una cadena rota se reporta como **traza incompleta** con las capas faltantes; nunca se inventan enlaces.
- Un fallo del core (`status != 00`) no cuenta como capa faltante de `modern` (no se invoca por diseño).
- Memoria acotada (`MAX_EVENTS`, evicción FIFO).

## Despliegue
- **Local:** `docker compose` (ver README).
- **AWS (Learner Lab):** una EC2 con Docker Compose + bucket cifrado (`infra/template.yaml`). Ver [adr/0002](adr/0002-ec2-compose-vs-eks.md).

## Evolución prevista
1. Exportar los spans reconstruidos a OTLP (OTel Collector → Tempo/Jaeger) y visualizar en Grafana.
2. Reemplazar el stand-in del bus/gateway por IBM ACE / DataPower de desarrollo (trabajo futuro).
3. mTLS entre servicios internos (hoy: red aislada + token compartido).
