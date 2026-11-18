# Atributos de calidad

Estado: ✅ cubierto y verificado · 🟡 diseñado, falta medir · ⬜ pendiente. Los números se completan en la fase de pruebas (`evidence/`).

| Atributo | Escenario | Táctica / decisión | Cómo se mide | Estado |
|---|---|---|---|---|
| **Performance** | 500 tx/s por el gateway sin degradar p95 > 200 ms | Servicios livianos, timeouts, `ThreadingHTTPServer`; correlación fuera del camino crítico (lee logs de forma asíncrona) | k6/`generate_load.py`: p50/p95/p99, rps; overhead con y sin correlacionador | 🟡 (smoke local: ~475 rps, p95 45 ms) |
| **Interoperability** | Integrar capas con formatos distintos (JSON, ancho fijo) y varios canales | Parsers por formato con contrato único `Event`; `trace_id` compatible con OTel | Tests de parsers; % de eventos aceptados vs. `malformedLines` | ✅ |
| **Scalability** | Subir 10× la carga | Capas sin estado (escalables horizontalmente); correlacionador con memoria acotada | Prueba de escalado (réplicas/ASG) y límite de `MAX_EVENTS` | 🟡 |
| **Reliability** | El core falla el 10 % de las veces | Timeouts y errores controlados (502); traza sigue siendo reconstruible | `FAULT_RATE=0.1`: % de trazas completas | ✅ (smoke: 300/300) |
| **Availability** | Caída del bus o del core | `restart: unless-stopped`, healthchecks, `depends_on: service_healthy` | Prueba de caos: matar un contenedor y medir recuperación | 🟡 |
| **Security** | Ver [threat-model.md](threat-model.md) (20 amenazas, 7 riesgos residuales) | Defensa en profundidad: TLS, API keys, red aislada, contenedores endurecidos, IaC segura, CI de seguridad | Pruebas negativas (401/413/422), cfn-lint, Trivy, gitleaks | ✅ / 🟡 |
| **Maintainability** | Cambiar un formato de log | Esquema de ancho fijo en una sola tupla; módulos pequeños; lint y formato en CI | Complejidad, cobertura, tiempo de cambio | ✅ |
| **Modifiability** | Añadir una capa o reemplazar un stand-in por un componente real | Convención `PREFIX-<hex>` + tablas `OWN_PREFIX/PARENT_PREFIX`; capas intercambiables | Pasos para añadir una capa (documentados) | 🟡 |
| **Testability** | Probar la correlación sin levantar infraestructura | Parsers y grafo como funciones/estructuras puras; `FAULT_RATE` y latencia inyectables | Cobertura de tests, 52 pruebas | ✅ |
| **Reusability** | Reutilizar el correlacionador en otro flujo | Paquete independiente `obsproto.correlator`, sin dependencia de las capas | Uso con otro conjunto de logs | 🟡 |
| **Supportability** | Diagnosticar una transacción lenta o fallida | `GET /trace/{cualquier ID}`, `/stats`, `/metrics`, logs estructurados, `make trace` | Tiempo de localización: manual vs. modelo | 🟡 |
