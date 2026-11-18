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

## Ahorro de crédito
- Detén la instancia al terminar cada sesión: `aws ec2 stop-instances --instance-ids <InstanceId>` (la pila se reinicia sola; la IP pública cambia y el certificado ya no coincide → regenera con `rm -r certs && scripts/gen-certs.sh <nueva-IP>`).
- Al final de todo: `aws s3 rm s3://<bucket> --recursive && aws cloudformation delete-stack --stack-name obsproto`.
