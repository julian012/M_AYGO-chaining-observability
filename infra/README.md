# Despliegue en AWS (Learner Lab)

CloudFormation: 1 EC2 (Docker Compose) + bucket S3 cifrado. **No crea roles IAM** (denegado en el lab): usa `LabInstanceProfile`.
Costo estimado: ~USD 0.04–0.08/h mientras la instancia esté encendida.

## Requisitos
- Credenciales del lab en `~/.aws/credentials` (AWS Details → AWS CLI → Show). Caducan al reiniciar el lab.
- El repo publicado en HTTPS (la instancia lo clona). Despliega un **tag** inmutable, no `main`.

## Pasos
```bash
aws sts get-caller-identity                        # confirma cuenta/rol y que las credenciales no caducaron
cfn-lint infra/template.yaml

VPC=$(aws ec2 describe-vpcs --filters Name=isDefault,Values=true --query 'Vpcs[0].VpcId' --output text)
SUBNET=$(aws ec2 describe-subnets --filters Name=vpc-id,Values=$VPC Name=default-for-az,Values=true \
          --query 'Subnets[0].SubnetId' --output text)
MYIP=$(curl -s https://checkip.amazonaws.com)/32

aws cloudformation deploy --stack-name obsproto --region us-east-1 \
  --template-file infra/template.yaml \
  --parameter-overrides VpcId=$VPC SubnetId=$SUBNET AllowedCidr=$MYIP \
                        RepoUrl=https://github.com/julian012/M_AYGO-chaining-observability.git RepoRef=v0.1.0
aws cloudformation describe-stacks --stack-name obsproto --query 'Stacks[0].Outputs' --output table
```

## Usar
```bash
# Correlacionador: nunca expuesto; túnel SSH (comando exacto en la salida CorrelatorTunnel)
ssh -i labsuser.pem -L 9100:127.0.0.1:9100 ec2-user@<PublicIp>
# En la instancia, las claves están en /opt/obsproto/.env (permisos 600). Cópialas SOLO a tu terminal, nunca a git.
python scripts/generate_load.py --url https://<PublicIp>:8443 --cacert <cert copiado de /opt/obsproto/certs/server.crt> \
        --correlator http://127.0.0.1:9100 --n 500 --concurrency 20 --out evidence/run-aws-01.json
```

## Tablero de CloudWatch (`obsproto-pruebas`)
La plantilla crea el tablero; la URL está en la salida `DashboardUrl`. Muestra dos fuentes de métricas (namespace `ObsProto`):
- **Generador** (`scripts/generate_load.py --cw-scenario <nombre>`): al terminar cada corrida publica, en ventanas de 10 s,
  rps, p50/p95/p99 y errores, más el % de trazas completas y el p95 por protocolo. Se publica al final para no quitarle
  CPU a la prueba.
- **Host** (`scripts/monitor_host.py`, servicio systemd): cada 10 s, CPU y memoria de cada contenedor y `/stats` del
  correlacionador (completitud, respuestas del bus sin padre, líneas descartadas, eventos por capa).

```bash
# En la instancia (una vez): instalar y arrancar el monitor
sudo cp /opt/obsproto/infra/obsproto-monitor.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now obsproto-monitor
# Cada escenario con su nombre (prefijo aws- para separarlos de pruebas locales)
sudo python3 scripts/generate_load.py --url https://127.0.0.1:8443 --cacert certs/server.crt \
     --correlator http://127.0.0.1:9100 --n 600 --concurrency 20 --cw-scenario aws-normal-c20 --out /tmp/aws-normal-c20.json
```
Costo: métricas propias de alta resolución (~30 series, prorrateadas por hora) y llamadas `PutMetricData`: centavos durante
las pruebas. Los 3 primeros tableros son gratis. Una métrica nueva tarda unos minutos en aparecer en el tablero.

> **Nota de versión:** la instancia se actualizó en sitio por SSM (`git checkout v0.2.1`). El parámetro `RepoRef` del
> stack sigue en `v0.1.0`; cambiarlo reemplazaría el `UserData` y reiniciaría la instancia (nueva IP y certificado).

## Pruebas de estrés (`scripts/run_scenarios.sh`)
La carga se genera desde **otra instancia** (`EnableLoadGenerator=true`), para que no compita por CPU con la pila. Va por
la red privada al gateway (el grupo del generador es el único con acceso a `8443` además de tu IP). Las trazas se verifican en
la instancia de la aplicación, porque el correlacionador solo escucha en loopback.

```bash
# 1) Crear el generador (agrega 3 recursos; no toca la instancia de la aplicación: revisa el change set)
aws cloudformation deploy --stack-name obsproto --template-file infra/template.yaml \
  --parameter-overrides EnableLoadGenerator=true VpcId=… SubnetId=… AllowedCidr=$MYIP \
                        RepoUrl=https://github.com/julian012/M_AYGO-chaining-observability.git RepoRef=v0.1.0
# 2) Correr todos los escenarios (≈ 35 min); o solo algunos: … run_scenarios.sh setup warmup steps
APP=<PublicIp> APP_PRIV=<PrivateIp> LOADGEN=<LoadGenPublicIp> scripts/run_scenarios.sh
# 3) Apagar el generador al terminar
aws cloudformation deploy … --parameter-overrides EnableLoadGenerator=false …
```
`setup` lleva las dos instancias al último tag (`REF`), deja logs limpios, regenera el certificado con la IP pública y la
privada y **sube el límite de tasa** a 100 000/s para que los 429 no se confundan con saturación. Cada escenario queda en
`evidence/aws-stress-<fecha>/<escenario>.json` y en el tablero como `aws-<escenario>`.

| Escenario | Qué cambia | Mide |
|---|---|---|
| `warmup` | 400 tx, C=10 (no se reporta) | — |
| `normal` | 3 × 1500 tx, C=20 | Repetibilidad: rps, p50/p95/p99, % completas |
| `rest-only` | Todos los canales por REST | Costo de SOAP |
| `steps` | C = 10, 20, 50, 100, 200 | Punto de saturación |
| `gateway-1cpu` | `GATEWAY_CPUS=1` con C = 50 y 100 | Escalado vertical del cuello de botella |
| `sustained` | 10 min, C=50 (verifica las últimas 3000) | Estabilidad y memoria del correlacionador (evicción) |
| `manual-vs-model` | 30 transacciones, logs grandes | Seguimiento manual (4 `grep`) frente a `/trace` |
| `fault` | `FAULT_RATE=0.1` | Trazas con fallo del core |
| `bus-info` | `BUS_LOG_LEVEL=INFO` | Ruptura de la cadena |
| `bus-outage` | Bus detenido 30 s durante 120 s de carga | Errores, trazas incompletas por capa, recuperación |
| `no-correlator` | Correlacionador detenido | Overhead (comparar con `normal`) |

## Ahorro de crédito
- Detén la instancia al terminar cada sesión: `aws ec2 stop-instances --instance-ids <InstanceId>` (la pila se reinicia sola; la IP pública cambia y el certificado ya no coincide → regenera con `rm -r certs && scripts/gen-certs.sh <nueva-IP>`).
- Al final de todo: `aws s3 rm s3://<bucket> --recursive && aws cloudformation delete-stack --stack-name obsproto`.
