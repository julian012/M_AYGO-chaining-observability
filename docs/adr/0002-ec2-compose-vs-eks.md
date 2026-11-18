# ADR-0002: EC2 + Docker Compose en lugar de EKS/ECS/Lambda

- **Estado:** Aceptada · **Fecha:** 2026-11-10

## Contexto
Cuenta **AWS Academy Learner Lab** con crédito limitado (~USD 50): `iam:CreateRole` denegado, credenciales temporales y
sesiones acotadas. El prototipo debe correr en AWS y soportar pruebas de estrés.

## Decisión
Una instancia EC2 (`t3.medium`) con Docker Compose, subred pública sin NAT, desplegada con CloudFormation y el
`LabInstanceProfile` existente. Bucket S3 cifrado para evidencias.

## Alternativas
- **EKS:** el plano de control cuesta ~USD 73/mes; consume el presupuesto. Descartada.
- **ECS Fargate + ALB + NAT:** ALB (~USD 16/mes) y NAT (~USD 32/mes) son costos fijos altos. Descartada.
- **Lambda/API Gateway:** buena para servicios modernos (ya usada en el taller), pero no representa un core legacy con logs de archivo ni un bus.

## Consecuencias
- (+) Costo ≈ USD 0.04–0.08/h; se apaga al terminar; la misma pila corre en local y en AWS.
- (+) IaC reproducible sin crear IAM.
- (−) Un solo nodo: la disponibilidad se demuestra con reinicio de contenedores, no con múltiples AZ.
- (−) La escalabilidad horizontal se demuestra con réplicas/ASG temporal en la prueba, no de forma permanente.
