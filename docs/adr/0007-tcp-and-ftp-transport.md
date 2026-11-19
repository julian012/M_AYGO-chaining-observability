# ADR-0007: Tramas por TCP y archivos planos por FTP

**Estado:** Aceptada · 19-nov-2026

## Contexto
Hasta v0.4.0 el bus enviaba la trama al core con un `POST` HTTP y el core avisaba al servicio moderno con otro `POST`. No es
realista: una trama de ancho fijo hacia un core COBOL viaja por un **socket TCP** (o una cola), con una cabecera de longitud por
mensaje; y el core no llama en línea a los sistemas posteriores: les deja **archivos planos** que se transfieren por **FTP** y se
procesan en lote.

## Decisión
- **Bus → Core:** socket TCP (`core:9000`). Cada mensaje: longitud en 4 dígitos ASCII + trama (`0060…` / `0038…`). Una conexión
  puede llevar varios mensajes. Servidor `ThreadingTCPServer`; cabecera inválida ⇒ se cierra la conexión.
- **Core → Moderno:** el core acumula las novedades de los movimientos exitosos y cada `BATCH_INTERVAL_S` (5 s) sube por FTP un
  archivo plano de ancho fijo (cabecera `00`, detalle `01` con el ID del core, cola `99` con el conteo) como `.tmp` y lo renombra.
  El moderno revisa `/inbox` cada `POLL_S` (2 s), valida la cola, emite un span por registro y mueve el archivo a `/procesados`
  (o lo aparta como `.err` si no es íntegro).
- **FTP:** stand-in con la biblioteca estándar (`obsproto/ftpserver.py`), solo en la red interna, con usuario y contraseña
  (`FTP_PASSWORD` en `.env`), dos carpetas y nombres en lista blanca.

## Alternativas
- *Imagen de un servidor FTP real (vsftpd, pure-ftpd):* más fiel, pero agrega una imagen de terceros al escaneo y el modo pasivo
  dentro de Docker es frágil. El modelo solo depende de que el archivo llegue, no del producto. Queda como mejora.
- *SFTP/FTPS:* es lo correcto en producción; requiere dependencias fuera de la biblioteca estándar. Riesgo residual declarado.
- *Cola de mensajes (MQ) entre bus y core:* también es común; el efecto sobre la correlación es el mismo que TCP.

## Consecuencias
- El tramo core → moderno es **asíncrono**: la traza queda incompleta unos segundos y se completa sola (local: 2,2–3,0 s;
  máximo teórico ≈ lote + revisión). El verificador espera 12 s por defecto (`--settle`).
- El correlacionador no cambió: lee los mismos cuatro logs. El modelo es independiente del transporte.
- Rendimiento local igual que con HTTP (477 rps, p95 52 ms, 600/600 completas). Las pruebas de AWS del 18-nov se hicieron con
  HTTP (v0.3.x) y así se declara.
- El core ya no exige `X-Internal-Token`: queda protegido por la red interna (riesgo residual). FTP viaja en claro (riesgo residual).
