#!/usr/bin/env bash
# Orquestador de las pruebas de estrés. Se ejecuta desde el equipo del operador y controla, por SSH:
#   - APP     instancia de la aplicación (pila + correlacionador + monitor)
#   - LOADGEN instancia generadora de carga (EnableLoadGenerator=true en la plantilla)
# La carga va por la red privada (APP_PRIV:8443) y se verifica en APP, donde el correlacionador solo escucha en loopback.
# Cada escenario se publica en el tablero de CloudWatch "obsproto-pruebas" como aws-<nombre> y se guarda en evidence/.
#
# Uso:
#   APP=<ip pública app> APP_PRIV=<ip privada app> LOADGEN=<ip pública generador> scripts/run_scenarios.sh [escenario ...]
#   Sin argumentos corre todos, en orden. Escenarios: setup warmup normal rest-only steps gateway-1cpu sustained
#   manual-vs-model fault bus-info bus-outage no-correlator
set -euo pipefail
cd "$(dirname "$0")/.."

: "${APP:?falta APP (IP pública de la aplicación)}"
: "${APP_PRIV:?falta APP_PRIV (IP privada de la aplicación)}"
: "${LOADGEN:?falta LOADGEN (IP pública del generador)}"
KEY="${KEY:-$HOME/.ssh/labsuser.pem}"
REF="${REF:-$(git describe --tags --abbrev=0)}"      # versión que se despliega en las dos instancias
GAP="${GAP:-20}"                                   # segundos entre escenarios (separa las series en el tablero)
OUT="${OUT:-evidence/aws-stress-$(date +%Y-%m-%d)}"
SSH=(ssh -i "$KEY" -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ServerAliveInterval=30)
mkdir -p "$OUT"

app() { "${SSH[@]}" -n "ec2-user@$APP" "cd /opt/obsproto && sudo bash -c $(printf '%q' "$1")"; }
lg() { "${SSH[@]}" -n "ec2-user@$LOADGEN" "cd /opt/obsproto && $1"; }
log() { printf '\n[%s] %s\n' "$(date +%H:%M:%S)" "$*"; }

wait_healthy() {
  app 'for i in $(seq 1 60); do n=$(docker compose ps --format "{{.Health}}" | grep -c "^healthy$" || true); [ "$n" -ge "${1:-5}" ] && exit 0; sleep 3; done; docker compose ps; exit 1'
}

set_env() {  # set_env KEY VALUE servicio  -> cambia .env y recrea solo ese servicio
  app "grep -q '^$1=' .env && sed -i 's|^$1=.*|$1=$2|' .env || echo '$1=$2' >> .env; docker compose up -d $3 >/dev/null 2>&1"
  wait_healthy
}

# run NOMBRE "args del generador" ["args de verificación"]
run() {
  local name="$1" gen="$2" ver="${3:-}"
  log "escenario $name: $gen"
  lg "python3 scripts/generate_load.py --url https://$APP_PRIV:8443 --cacert certs/server.crt --cw-scenario aws-$name \
      --ids-out /tmp/$name.ids --out /tmp/$name.json $gen > /dev/null"
  "${SSH[@]}" "ec2-user@$LOADGEN" "cat /tmp/$name.ids" | "${SSH[@]}" "ec2-user@$APP" "cat > /tmp/$name.ids"
  "${SSH[@]}" "ec2-user@$LOADGEN" "cat /tmp/$name.json" | "${SSH[@]}" "ec2-user@$APP" "cat > /tmp/$name.json"
  if [[ "$ver" != "skip" ]]; then
    app "python3 scripts/generate_load.py --verify-ids /tmp/$name.ids --summary /tmp/$name.json \
         --correlator http://127.0.0.1:9100 --cw-scenario aws-$name --out /tmp/$name.json $ver > /dev/null"
  fi
  "${SSH[@]}" -n "ec2-user@$APP" "cat /tmp/$name.json" > "$OUT/$name.json"
  python3 - "$OUT/$name.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1])); c = d.get("correlation") or {}; l = d.get("latencyMs") or {}
print(f"  -> {d['requests']} tx · {d['throughputRps']} rps · p50 {l.get('p50')} / p95 {l.get('p95')} / p99 {l.get('p99')} ms"
      f" · errores {d['errors']} {d['statusCounts']} · trazas completas {c.get('completePct', 'n/a')} %"
      f" {c.get('incompleteByMissingLayers') or ''}")
PY
  sleep "$GAP"
}

# ------------------------------------------------------------------------------------------- escenarios
s_setup() {
  log "setup: las dos instancias pasan a $REF"
  local g="git -c safe.directory=/opt/obsproto"
  app "export HOME=/root; $g fetch -q --depth 1 origin tag $REF && $g checkout -q -f $REF && $g describe --tags"
  lg "cloud-init status --wait >/dev/null 2>&1 || true; $g fetch -q --depth 1 origin tag $REF && $g checkout -q -f $REF && $g describe --tags"
  log "setup: logs limpios, certificado con IP pública y privada, límite de tasa alto, monitor activo; copia de API keys y certificado"
  app "FORCE=1 scripts/gen-certs.sh $APP $APP_PRIV"
  # El límite de tasa (200/s por canal) se sube para que los 429 no se confundan con saturación; se declara en el paper.
  app "for kv in RATE_PER_SEC=100000 RATE_BURST=100000 BUS_LOG_LEVEL=DEBUG FAULT_RATE=0.0 GATEWAY_CPUS=0.5; do \
         k=\${kv%%=*}; grep -q \"^\$k=\" .env && sed -i \"s|^\$k=.*|\$kv|\" .env || echo \"\$kv\" >> .env; done; \
       docker compose down -v >/dev/null 2>&1; docker compose up -d --build >/dev/null 2>&1; systemctl restart obsproto-monitor"
  wait_healthy
  lg "mkdir -p certs"
  # Mínimo privilegio: el generador solo recibe las API keys (no los tokens internos ni el del correlacionador).
  "${SSH[@]}" "ec2-user@$APP" "sudo grep '^API_KEYS=' /opt/obsproto/.env" \
    | "${SSH[@]}" "ec2-user@$LOADGEN" "umask 077; cat > /opt/obsproto/.env"
  "${SSH[@]}" "ec2-user@$APP" "cat /opt/obsproto/certs/server.crt" \
    | "${SSH[@]}" "ec2-user@$LOADGEN" "cat > /opt/obsproto/certs/server.crt"
  lg "python3 --version"
}

s_warmup() { run warmup "--n 400 --concurrency 10"; }
s_normal() { for r in 1 2 3; do run "normal-c20-r$r" "--n 1500 --concurrency 20"; done; }
s_rest_only() { run rest-only-c20 "--n 1500 --concurrency 20 --rest-only"; }
s_steps() { for c in 10 20 50 100 200; do run "step-c$c" "--n $((c * 60 > 1000 ? c * 60 : 1000)) --concurrency $c"; done; }
s_gateway_1cpu() {
  set_env GATEWAY_CPUS 1 gateway
  run warmup-1cpu "--n 300 --concurrency 10"
  for c in 50 100; do run "gw1cpu-c$c" "--n $((c * 60)) --concurrency $c"; done
  set_env GATEWAY_CPUS 0.5 gateway
}
s_sustained() { run sustained-c50-10min "--duration 600 --concurrency 50" "--verify-last 3000"; }
s_manual_vs_model() {
  log "manual frente al modelo (con los logs grandes de la carga sostenida)"
  app "python3 scripts/manual_vs_model.py --samples 30 --out /tmp/manual-vs-model.json > /dev/null"
  "${SSH[@]}" -n "ec2-user@$APP" "cat /tmp/manual-vs-model.json" > "$OUT/manual-vs-model.json"
  python3 -c 'import json,sys;d=json.load(open(sys.argv[1]));print("  -> manual",d["manual"]["machineMs"],"· modelo",d["model"]["machineMs"],"· x",d["speedupMedian"],"· logs",d["logSizes"])' "$OUT/manual-vs-model.json"
}
s_fault() {
  set_env FAULT_RATE 0.1 core
  run fault-core-10pct "--n 1500 --concurrency 20"
  set_env FAULT_RATE 0.0 core
}
s_bus_info() {
  set_env BUS_LOG_LEVEL INFO bus
  run bus-info "--n 1000 --concurrency 20"
  set_env BUS_LOG_LEVEL DEBUG bus
}
s_bus_outage() {
  local name=bus-outage
  log "escenario $name: 120 s de carga; el bus se detiene en el segundo 40 y vuelve en el 70"
  "${SSH[@]}" -n "ec2-user@$LOADGEN" "cd /opt/obsproto && python3 scripts/generate_load.py --url https://$APP_PRIV:8443 \
      --cacert certs/server.crt --cw-scenario aws-$name --duration 120 --concurrency 20 --timeout 5 \
      --ids-out /tmp/$name.ids --out /tmp/$name.json > /dev/null" &
  local bg=$!
  sleep 40; local stop_at; stop_at=$(date -u +%FT%TZ); app "docker compose stop bus >/dev/null 2>&1"
  sleep 30; local start_at; start_at=$(date -u +%FT%TZ); app "docker compose start bus >/dev/null 2>&1"
  wait "$bg"
  wait_healthy
  "${SSH[@]}" "ec2-user@$LOADGEN" "cat /tmp/$name.ids" | "${SSH[@]}" "ec2-user@$APP" "cat > /tmp/$name.ids"
  "${SSH[@]}" "ec2-user@$LOADGEN" "cat /tmp/$name.json" | "${SSH[@]}" "ec2-user@$APP" "cat > /tmp/$name.json"
  app "python3 scripts/generate_load.py --verify-ids /tmp/$name.ids --summary /tmp/$name.json \
       --correlator http://127.0.0.1:9100 --cw-scenario aws-$name --out /tmp/$name.json > /dev/null"
  "${SSH[@]}" -n "ec2-user@$APP" "cat /tmp/$name.json" > "$OUT/$name.json"
  python3 - "$OUT/$name.json" "$stop_at" "$start_at" <<'PY'
import json, sys
p = sys.argv[1]; d = json.load(open(p)); d["outage"] = {"busStoppedAt": sys.argv[2], "busStartedAt": sys.argv[3]}
json.dump(d, open(p, "w"), indent=2); c = d.get("correlation") or {}
print(f"  -> {d['requests']} tx · errores {d['statusCounts']} · completas {c.get('completePct')} % · incompletas {c.get('incompleteByMissingLayers')}")
PY
  sleep "$GAP"
}
s_no_correlator() {
  app "docker compose stop correlator >/dev/null 2>&1"
  run no-correlator-c20 "--n 1500 --concurrency 20" skip
  app "docker compose start correlator >/dev/null 2>&1"
  wait_healthy
}

ALL=(setup warmup normal rest-only steps gateway-1cpu sustained manual-vs-model fault bus-info bus-outage no-correlator)
for s in "${@:-${ALL[@]}}"; do
  "s_${s//-/_}"
done
log "listo: resultados en $OUT y en el tablero de CloudWatch obsproto-pruebas"
