#!/usr/bin/env bash
# Certificado TLS autofirmado (RSA-2048, 30 días) para el gateway. Solo para el prototipo.
# Uso: scripts/gen-certs.sh [IP_PUBLICA_ADICIONAL]
set -euo pipefail
cd "$(dirname "$0")/.."

mkdir -p certs
if [[ -f certs/server.crt && -f certs/server.key ]]; then
  echo "certs/ ya existe; no se modifica."
  exit 0
fi

EXTRA_IP="${1:-}"
SAN="DNS:localhost,IP:127.0.0.1"
[[ -n "$EXTRA_IP" ]] && SAN="$SAN,IP:$EXTRA_IP"

umask 077
openssl req -x509 -newkey rsa:2048 -nodes \
  -keyout certs/server.key -out certs/server.crt -days 30 \
  -subj "/CN=obsproto-gateway" -addext "subjectAltName=$SAN" 2>/dev/null
# RSA y no EC: funciona también con LibreSSL antiguo (Python del sistema en macOS) y es verificable en local.
# El contenedor corre como UID 10001 y debe poder leer la llave montada en solo lectura.
# Riesgo residual aceptado y documentado (certificado efímero, autofirmado, no versionado): docs/threat-model.md
chmod 644 certs/server.key certs/server.crt
echo "Certificado generado en certs/ (SAN: $SAN)."
