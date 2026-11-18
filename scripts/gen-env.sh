#!/usr/bin/env bash
# Genera .env con secretos aleatorios (nunca se versiona). Idempotente: no sobrescribe.
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ -f .env ]]; then
  echo ".env ya existe; no se modifica."
  exit 0
fi

command -v openssl >/dev/null || { echo "Se requiere openssl" >&2; exit 1; }
umask 077
cat > .env <<EOF
# Generado por scripts/gen-env.sh — NO versionar, NO compartir.
INTERNAL_TOKEN=$(openssl rand -hex 24)
CORRELATOR_TOKEN=$(openssl rand -hex 24)
API_KEYS=mobile:$(openssl rand -hex 24),web:$(openssl rand -hex 24),partner:$(openssl rand -hex 24)
GATEWAY_BIND=${GATEWAY_BIND:-127.0.0.1}
FAULT_RATE=0.0
EOF
chmod 600 .env
echo ".env generado (permisos 600)."
